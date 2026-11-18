"""Lab-only node operations through `docker exec` on kind nodes.

This is root access to the node, outside Kubernetes RBAC: it only runs when KUBESAGE_LAB_MODE is on (enforced by
policy) and every eviction is audited. Same procedure as scripts/evict-image.ps1.
"""

import json
import subprocess
import time

from kubesage.audit import audit


class CrictlError(RuntimeError):
    def __init__(self, args: tuple[str, ...], stderr: str):
        super().__init__(f"crictl {' '.join(args)} failed: {stderr}")
        self.stderr = stderr

    @property
    def already_gone(self) -> bool:
        """containerd removes images and containers asynchronously: a second remove finds nothing to remove."""
        text = self.stderr.lower()
        return "no such image" in text or "no such container" in text or "not found" in text


def _crictl(node: str, *args: str, timeout: float = 60) -> str:
    proc = subprocess.run(
        ["docker", "exec", node, "crictl", *args], capture_output=True, text=True, check=False, timeout=timeout
    )
    if proc.returncode != 0:
        raise CrictlError(args, (proc.stderr or proc.stdout).strip())
    return proc.stdout


def _remove(node: str, *args: str) -> None:
    """crictl rm / rmi that treats "already gone" as success."""
    try:
        _crictl(node, *args)
    except CrictlError as exc:
        if not exc.already_gone:
            raise


def ensure_image(node: str, image: str, timeout_s: float = 900) -> bool:
    """Make sure `image` is in the node's containerd store (warm start). True if it had to be pulled."""
    try:
        _crictl(node, "inspecti", image)
        return False
    except CrictlError:
        _crictl(node, "pull", image, timeout=timeout_s)
        audit("lab.pull_image", node, image=image)
        return True


def _matches(image: dict, repository_path: str) -> bool:
    refs = list(image.get("repoTags") or []) + list(image.get("repoDigests") or [])
    return any(f"/{repository_path}:" in ref or f"/{repository_path}@" in ref for ref in refs)


def evict_images(node: str, repository_paths: list[str], timeout_s: float = 120) -> list[str]:
    """Remove every image of the given repositories from the node so the next start is a cold pull.

    Waits while a running container still uses an image; removes exited containers that reference it. Removal is
    idempotent (containerd lists an image briefly after it was removed) and a real failure is retried until the
    deadline, then reported with crictl's own message.
    """
    evicted: list[str] = []
    last_error: CrictlError | None = None
    deadline = time.monotonic() + timeout_s
    while True:
        images = [
            i
            for i in json.loads(_crictl(node, "images", "-o", "json"))["images"]
            if any(_matches(i, path) for path in repository_paths)
        ]
        if not images:
            break
        if time.monotonic() > deadline:
            reason = f": {last_error}" if last_error else " (a running container still uses it)"
            raise TimeoutError(f"images {repository_paths} not evicted from {node} after {timeout_s:.0f} s{reason}")
        containers = json.loads(_crictl(node, "ps", "-a", "-o", "json"))["containers"]
        for image in images:
            users = [c for c in containers if image["id"] in (c.get("imageRef"), c.get("image", {}).get("image"))]
            if any(c["state"] != "CONTAINER_EXITED" for c in users):
                continue
            try:
                for container in users:
                    _remove(node, "rm", container["id"])
                _remove(node, "rmi", image["id"])
            except CrictlError as exc:
                last_error = exc
                continue
            evicted.extend(image.get("repoTags") or [image["id"]])
        time.sleep(2)
    evicted = list(dict.fromkeys(evicted))  # a stale listing can show an image twice
    audit("lab.evict_images", node, repositories=repository_paths, evicted=evicted)
    return evicted
