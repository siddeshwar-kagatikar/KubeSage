"""Reads image variants (tags) and sizes from the lab registry, for switch_image_variant."""

import httpx

ACCEPT = (
    "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"
)


def split_repository(repository: str, hosts: list[str]) -> str | None:
    """'localhost:5001/demo-ai-service' -> 'demo-ai-service' if the host is served by the lab registry."""
    host, _, path = repository.partition("/")
    return path if host in hosts and path else None


class Registry:
    def __init__(self, url: str, timeout: float = 10.0):
        self._client = httpx.Client(base_url=url, timeout=timeout)

    def tags(self, path: str) -> list[str]:
        response = self._client.get(f"/v2/{path}/tags/list")
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json().get("tags") or []

    def compressed_bytes(self, path: str, tag: str) -> int | None:
        """Sum of compressed layer sizes for linux/amd64; None if the tag does not exist."""
        response = self._client.get(f"/v2/{path}/manifests/{tag}", headers={"Accept": ACCEPT})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        manifest = response.json()
        if "manifests" in manifest:
            platform = next(
                (
                    m
                    for m in manifest["manifests"]
                    if m.get("platform", {}).get("os") == "linux" and m["platform"].get("architecture") == "amd64"
                ),
                None,
            )
            if platform is None:
                return None
            manifest = self._client.get(f"/v2/{path}/manifests/{platform['digest']}", headers={"Accept": ACCEPT}).json()
        return sum(layer["size"] for layer in manifest.get("layers", []))

    def variants(self, path: str) -> dict[str, int]:
        """tag -> compressed bytes for every tag of the repository."""
        sizes = {tag: self.compressed_bytes(path, tag) for tag in self.tags(path)}
        return {tag: size for tag, size in sizes.items() if size is not None}
