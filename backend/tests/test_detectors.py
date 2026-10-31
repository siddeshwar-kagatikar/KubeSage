import pytest

from kubesage.profiler.baselines import evaluate
from kubesage.profiler.detectors import detect
from tests.conftest import timeline_for


@pytest.mark.parametrize(
    ("fixture", "category"),
    [
        ("s1-large-image", "large_image"),
        ("s3-model-weights", "model_loading"),
        ("s6a-slow-readiness", "slow_readiness"),
    ],
)
def test_top_category_matches_scenario(fixture, category, settings):
    candidates = detect(timeline_for(fixture), settings)
    assert candidates, fixture
    assert candidates[0].category == category
    assert candidates[0].score > 0.6


def test_warm_start_raises_nothing(settings):
    assert detect(timeline_for("s1-large-image-warm"), settings) == []


def test_slow_registry_not_flagged_for_normal_throughput(settings):
    categories = {c.category for c in detect(timeline_for("s1-large-image"), settings)}
    assert "slow_registry" not in categories


def test_slo_breach(settings):
    breach = evaluate(59.0, 30.0, [], settings)
    assert breach.reason == "slo" and breach.threshold_s == 30.0 and breach.baseline_s is None


def test_regression_needs_enough_baseline_samples(settings):
    assert evaluate(20.0, 30.0, [5.0, 6.0], settings) is None
    breach = evaluate(20.0, 30.0, [5.0, 6.0, 7.0], settings)
    assert breach.reason == "regression" and breach.baseline_s == 6.0 and breach.threshold_s == 9.0


def test_within_slo_and_baseline(settings):
    assert evaluate(8.0, 30.0, [6.0, 6.0, 7.0], settings) is None
