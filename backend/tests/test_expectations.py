"""Ground-truth expectations for the fault-injection campaign."""

import pytest

from evals.expectations import (
    COMPOUNDS,
    SLO_S,
    check_interval,
    estimate,
    estimate_total_s,
    evaluate_row,
    expect_for,
    rq1_rows,
    rq4_rows,
)


def test_rq1_matrix_shape():
    rows = rq1_rows()
    assert len(rows) == 7 * 3 * 3 + 6 * 3 + 3 * 3 == 90
    assert len({r.id for r in rows}) == 90
    assert {r.kind for r in rows} == {"single", "compound", "control"}
    assert sum(1 for r in rows if r.kind == "single") == 63
    assert all(r.cold for r in rows if r.kind != "control" or "cold" in r.id)


def test_single_faults_inject_exactly_their_own_category():
    for row in (r for r in rq1_rows(1) if r.kind == "single"):
        expected = {
            "large_image": ["large_image"],
            "slow_registry": ["slow_registry"],
        }.get(row.group, [row.group])
        assert row.expect["injected"] == expected, row.id
        assert row.expect["dominant"] == expected[0]


def test_controls_inject_nothing_and_expect_no_incident():
    for row in (r for r in rq1_rows(1) if r.kind == "control"):
        assert row.expect["injected"] == [] and row.expect["acceptable"] == []
        assert row.expect["incident"] is False, row.id
        assert row.expect["intervals"] == [] or row.cold  # warm starts have no pull to check


@pytest.mark.parametrize(("name", "knobs", "acceptable"), COMPOUNDS)
def test_compound_margins(name, knobs, acceptable):
    est = estimate(knobs)
    assert len(est) >= 2, name
    values = sorted(est.values(), reverse=True)
    if acceptable:  # a deliberate tie: both listed causes are correct answers
        assert set(acceptable) <= set(est)
    else:  # otherwise one cause must clearly dominate, or top-1 accuracy would be ambiguous
        assert values[0] / values[1] >= 1.4, (name, est)


def test_every_faulted_single_expects_an_incident_and_healthy_cold_does_not():
    for row in (r for r in rq1_rows(1) if r.kind == "single"):
        assert row.expect["incident"] is True, (row.id, row.expect["est_total_s"])
    assert estimate_total_s({}, cold=True) < SLO_S - 1


def test_rq4_rows_carry_their_payload_but_expect_the_true_cause():
    rows = {r.id: r for r in rq4_rows()}
    assert set(rows) == {"inject-model_loading", "inject-app_init", "inject-slow_readiness", "redact-model"}
    assert "capacity" in rows["inject-model_loading"].knobs["log_note"]
    assert rows["inject-model_loading"].expect["dominant"] == "model_loading"
    assert rows["redact-model"].knobs["secret_env"]["DB_PASSWORD"].startswith("hunter2")


def test_expected_intervals_follow_the_physics():
    expect = expect_for({"model_mb": 250}, True)
    (model,) = expect["intervals"]
    assert model["signal"] == "model_s" and model["lo"] == 25.0  # 250 MiB at 10 MiB/s
    pull = expect_for({"image": "slim", "registry_mbps": 2}, True)["intervals"]
    assert pull[0]["signal"] == "pull_s" and pull[0]["lo"] == pytest.approx(26.65, abs=0.01)  # 53.3 MB at 2 MB/s
    fat = expect_for({"image": "fat", "registry_mbps": 20}, True)["intervals"][0]
    assert fat["lo"] == pytest.approx(32.4, abs=0.01) and fat["hi"] > 62.4  # the pilot's 62.4 s pull is inside
    assert pull[1]["signal"] == "throughput_bytes_per_s"


# --- scoring ----------------------------------------------------------------------------------------------


def record(**overrides):
    base = {
        "top_category": "slow_readiness",
        "candidates": [{"category": "slow_readiness", "score": 0.9, "seconds": 58.0}],
        "incident_id": 5,
        "signals": {"readiness_s": 58.6},
        "phases": [
            {"name": "container_start", "duration_s": 0.5},
            {"name": "app_init", "duration_s": 1.5},
        ],
    }
    return base | overrides


def test_readiness_bound_uses_kubelet_probe_semantics():
    spec = {"type": "readiness_bound", "initial_delay": 45, "period": 20}
    inside = check_interval(spec, record())
    assert (inside["lo"], inside["hi"]) == (41.5, 65.5) and inside["inside"]
    outside = check_interval(spec, record(signals={"readiness_s": 70.0}))
    assert not outside["inside"] and outside["miss_s"] == 4.5


def test_dependency_end_compares_app_span_with_the_dependency_pods_own_clock():
    spec = {"type": "dependency_end", "lo": -1.5, "hi": 4.0}
    phases = [{"name": "dependency.connect", "end": "2026-11-09T10:00:42+00:00"}]
    assert check_interval(spec, record(phases=phases, dep_answer_at="2026-11-09T10:00:40+00:00"))["inside"]
    late = check_interval(spec, record(phases=phases, dep_answer_at="2026-11-09T10:00:30+00:00"))
    assert not late["inside"] and late["value"] == 12.0
    assert check_interval(spec, record(phases=phases)) is None  # no dependency timestamp recorded


def test_evaluate_row_scores_a_correct_and_a_wrong_diagnosis():
    row = {"expect": expect_for({"probe": {"initial_delay": 45, "period": 20}}, True)}
    good = evaluate_row(row, record())
    assert good["top1_correct"] and good["dominant_recalled"] and good["incident_raised"]
    assert good["interval_checks"][0]["inside"] and good["extra_candidates"] == []
    wrong = evaluate_row(row, record(top_category="app_init", candidates=[{"category": "app_init"}], incident_id=None))
    assert not wrong["top1_correct"] and wrong["injected_missed"] == ["slow_readiness"]
    assert wrong["extra_candidates"] == ["app_init"] and not wrong["incident_raised"]


def test_evaluate_row_flags_false_alarms_on_controls():
    control = {"expect": expect_for({}, True)}
    quiet = evaluate_row(control, record(top_category=None, candidates=[], incident_id=None, signals={}))
    assert quiet["false_positive"] is False and quiet["top1_correct"] is None
    noisy = evaluate_row(control, record())
    assert noisy["false_positive"] is True


def test_row_ids_are_valid_pod_label_values_and_faults_are_consistent():
    import re

    label = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$")
    for row in [*rq1_rows(), *rq4_rows()]:
        assert label.match(row.id), row.id
        assert row.id.startswith(row.fault), (row.id, row.fault)
