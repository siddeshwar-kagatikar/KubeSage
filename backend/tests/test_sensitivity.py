"""Detector sensitivity sweep on hand-built startup records."""

import pytest

from evals.expectations import expect_for
from evals.sensitivity import DEFAULTS, candidates_for, score, sweep


def signals(**kw) -> dict:
    base = {
        "total_s": 40.0, "cold": True, "pull_s": None, "image_bytes": None, "throughput_bytes_per_s": None,
        "scheduling_s": 0.0, "init_containers_s": None, "app_init_s": 30.0, "readiness_s": 0.0,
        "app_init_and_readiness_s": None, "model_s": 0.0, "dependency_s": 0.0, "span_seconds": {"init.compute": 30.0},
        "failed_scheduling_events": 0, "probe_failures_after_app_ready": 0, "readiness_probe": None,
        "has_startup_probe": False, "cpu_limit": "250m", "restarts": 0,
    } | kw  # fmt: skip
    return base


def result(knobs: dict, **sig) -> dict:
    return {"status": "ok", "row": {"expect": expect_for(knobs, True)}, "record": {"signals": signals(**sig)}}


CPU = result({"cpu_work": 100})  # injected app_init; 30 s of init.compute in a 40 s startup
# healthy: 1.5 s of init in a 5 s startup (30 % share, so only the seconds threshold keeps it quiet)
CONTROL = result({}, total_s=5.0, app_init_s=1.5, span_seconds={"warmup": 0.0})


def test_candidates_use_the_production_detectors():
    assert candidates_for(CPU["record"], DEFAULTS)[0] == "app_init"
    assert candidates_for(CONTROL["record"], DEFAULTS) == []


def test_score_counts_top1_recall_and_false_alarms():
    scored = score([CPU, CONTROL], DEFAULTS)
    assert scored == {"top1": 1.0, "recall": 1.0, "fp_rate": 0.0, "rows": 2}
    miss = score([CPU, CONTROL], DEFAULTS.model_copy(update={"min_phase_seconds": 60.0}))
    assert miss["top1"] == 0.0 and miss["recall"] == 0.0 and miss["fp_rate"] == 0.0


def test_loose_thresholds_trade_recall_for_false_alarms():
    rows = sweep([CPU, CONTROL], "min_phase_seconds", [0.5, 5, 60])
    assert [r["fp_rate"] for r in rows] == [1.0, 0.0, 0.0]  # at 0.5 s the healthy 1.5 s init is reported
    assert [r["recall"] for r in rows] == [1.0, 1.0, 0.0]
    assert [r["default"] for r in rows] == [False, True, False]


def test_failed_rows_are_ignored():
    failed = {"status": "failed", "row": CPU["row"], "record": {}}
    assert score([failed, CPU], DEFAULTS)["rows"] == 1


def test_swept_values_keep_the_settings_type():
    row = sweep([CPU], "large_image_bytes", [200e6])[0]
    assert row["value"] == pytest.approx(200e6)
    assert isinstance(DEFAULTS.model_copy(update={"large_image_bytes": int(200e6)}).large_image_bytes, int)
