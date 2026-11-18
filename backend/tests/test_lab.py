"""Image eviction on kind nodes, with `crictl` replaced by a fake containerd."""

import json

import pytest

from kubesage.experiments import lab
from kubesage.experiments.lab import CrictlError, evict_images


class FakeNode:
    """A containerd that lists a removed image once more (it removes asynchronously) and can refuse removals."""

    def __init__(self, images, containers=(), stale_listings=1, rmi_error=None):
        self.images = {i["id"]: i for i in images}
        self.containers = list(containers)
        self.stale = {}
        self.stale_listings = stale_listings
        self.rmi_error = rmi_error
        self.calls = []

    def __call__(self, node, *args, timeout=60):
        self.calls.append(args)
        if args[:2] == ("images", "-o"):
            listed = [
                *self.images.values(),
                *(i for i, n in self.stale.items() if n > 0 for i in [self.stale_image(i)]),
            ]
            for key in list(self.stale):
                self.stale[key] -= 1
            return json.dumps({"images": listed})
        if args[:2] == ("ps", "-a"):
            return json.dumps({"containers": self.containers})
        if args[0] == "rm":
            self.containers = [c for c in self.containers if c["id"] != args[1]]
            return ""
        if args[0] == "rmi":
            if self.rmi_error:
                raise CrictlError(args, self.rmi_error)
            if args[1] not in self.images and args[1] not in self.stale:
                raise CrictlError(args, f'no such image "{args[1]}"')
            image = self.images.pop(args[1], None)
            if image is not None:
                self.stale[args[1]] = self.stale_listings
                self._removed = getattr(self, "_removed", {}) | {args[1]: image}
            else:
                raise CrictlError(args, f'no such image "{args[1]}"')
            return ""
        raise AssertionError(args)

    def stale_image(self, image_id):
        return self._removed[image_id]


def image(image_id, tag):
    return {"id": image_id, "repoTags": [f"localhost:5001/demo-ai-service:{tag}"], "repoDigests": []}


@pytest.fixture(autouse=True)
def no_sleep_no_audit(monkeypatch):
    monkeypatch.setattr(lab.time, "sleep", lambda _s: None)
    monkeypatch.setattr(lab, "audit", lambda *a, **k: None)


def test_eviction_survives_an_image_that_is_listed_after_it_was_removed(monkeypatch):
    node = FakeNode([image("sha256:a", "fat"), image("sha256:b", "slim")])
    monkeypatch.setattr(lab, "_crictl", node)
    evicted = evict_images("n", ["demo-ai-service"])
    assert sorted(evicted) == ["localhost:5001/demo-ai-service:fat", "localhost:5001/demo-ai-service:slim"]
    assert node.images == {}


def test_exited_containers_are_removed_but_running_ones_block_until_the_deadline(monkeypatch):
    exited = {"id": "c1", "imageRef": "sha256:a", "state": "CONTAINER_EXITED"}
    node = FakeNode([image("sha256:a", "fat")], [exited])
    monkeypatch.setattr(lab, "_crictl", node)
    evict_images("n", ["demo-ai-service"])
    assert ("rm", "c1") in node.calls

    running = {"id": "c2", "imageRef": "sha256:a", "state": "CONTAINER_RUNNING"}
    monkeypatch.setattr(lab, "_crictl", FakeNode([image("sha256:a", "fat")], [running]))
    ticks = iter(range(0, 1000, 60))
    monkeypatch.setattr(lab.time, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError, match="still uses it"):
        evict_images("n", ["demo-ai-service"], timeout_s=100)


def test_a_real_failure_is_reported_with_crictls_own_message(monkeypatch):
    node = FakeNode([image("sha256:a", "fat")], rmi_error="image is in use by container c9")
    monkeypatch.setattr(lab, "_crictl", node)
    ticks = iter(range(0, 1000, 60))
    monkeypatch.setattr(lab.time, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError, match="image is in use by container c9"):
        evict_images("n", ["demo-ai-service"], timeout_s=100)


def test_already_gone_detection():
    assert CrictlError(("rmi", "x"), 'no such image "x"').already_gone
    assert CrictlError(("rm", "x"), "container not found").already_gone
    assert not CrictlError(("rmi", "x"), "image is in use").already_gone
