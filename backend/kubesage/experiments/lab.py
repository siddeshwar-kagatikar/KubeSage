"""Lab-only node operations through `docker exec` on kind nodes.

This is root access to the node, outside Kubernetes RBAC: it only runs when KUBESAGE_LAB_MODE is on (enforced by
policy) and every eviction is audited. Same procedure as scripts/evict-image.ps1.
"""

import json
import subprocess
import time

from kubesage.audit import audit


def _crictl(node: str, *args: str) -> str:
    return subprocess.run(
        ["docker", "exec", node, "crictl", *args], capture_output=True, text=True, check=True, timeout=60
    ).stdout


def _matches(image: dict, repository_path: str) -> bool:
    refs = list(image.get("repoTags") or []) + list(image.get("repoDigests") or [])
    return any(f"/{repository_path}:" in ref or f"/{repository_path}@" in ref for ref in refs)


def evict_images(node: str, repository_paths: list[str], timeout_s: float = 120) -> list[str]:
    """Remove every image of the given repositories from the node so the next start is a cold pull.

    Waits while a running container still uses an image; removes exited containers that reference it.
    """
    evicted: list[str] = []
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
            raise TimeoutError(f"images {repository_paths} still in use on {node} after {timeout_s:.0f} s")
        containers = json.loads(_crictl(node, "ps", "-a", "-o", "json"))["containers"]
        for image in images:
            users = [c for c in containers if image["id"] in (c.get("imageRef"), c.get("image", {}).get("image"))]
            if any(c["state"] != "CONTAINER_EXITED" for c in users):
                continue
            for container in users:
                _crictl(node, "rm", container["id"])
            _crictl(node, "rmi", image["id"])
            evicted.extend(image.get("repoTags") or [image["id"]])
        time.sleep(2)
    audit("lab.evict_images", node, repositories=repository_paths, evicted=evicted)
    return evicted
