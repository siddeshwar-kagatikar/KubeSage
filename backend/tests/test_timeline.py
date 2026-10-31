"""Timeline building against startups recorded from the kind lab (see scripts/capture-fixture.ps1)."""

import itertools

import pytest

from kubesage.profiler.timeline import parse_go_duration
from tests.conftest import timeline_for


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("56.239s", 56.239), ("850ms", 0.85), ("1m2.5s", 62.5), ("1h0m1s", 3601.0), ("250us", 0.00025)],
)
def test_parse_go_duration(text, seconds):
    assert parse_go_duration(text) == pytest.approx(seconds)


def test_phases_never_negative_and_ordered():
    for name in ("s1-large-image", "s1-large-image-warm", "s3-model-weights", "s6a-slow-readiness"):
        tl = timeline_for(name)
        top = [p for p in tl.phases if p.parent is None]
        assert all(p.duration_s >= 0 for p in tl.phases), name
        assert all(a.start <= b.start for a, b in itertools.pairwise(top)), name
        assert tl.spans_found, name
        # Physically consistent: the app cannot start before its image is pulled, nor children before app_init.
        app_init = tl.phase("app_init")
        if pull := tl.phase("image_pull"):
            assert app_init.start >= pull.end, name
        assert all(p.start >= app_init.start for p in tl.phases if p.parent == "app_init"), name


def test_cold_large_image():
    tl = timeline_for("s1-large-image")
    s = tl.signals
    assert s["cold"] is True
    assert s["pull_s"] == pytest.approx(56.239)
    assert s["image_bytes"] == 647_964_219
    assert s["total_s"] == pytest.approx(59.0)
    assert tl.phase("image_pull").duration_s == pytest.approx(56.239)


def test_warm_start_has_no_pull():
    tl = timeline_for("s1-large-image-warm")
    assert tl.signals["cold"] is False
    assert tl.phase("image_pull") is None
    assert tl.signals["total_s"] <= 5


def test_model_weights_spans_are_children_of_app_init():
    tl = timeline_for("s3-model-weights")
    children = {p.name: p for p in tl.phases if p.parent == "app_init"}
    assert {"process.boot", "import", "model.download", "model.load", "warmup"} <= children.keys()
    assert children["model.download"].duration_s == pytest.approx(26.45, abs=0.01)
    assert children["model.download"].attributes["bytes"] == 262_144_000
    assert tl.signals["model_s"] > 26


def test_slow_readiness_separates_app_ready_from_ready_condition():
    tl = timeline_for("s6a-slow-readiness")
    assert tl.signals["app_init_s"] < 3
    assert tl.signals["readiness_s"] > 55
    assert tl.signals["readiness_probe"]["initialDelaySeconds"] == 45
