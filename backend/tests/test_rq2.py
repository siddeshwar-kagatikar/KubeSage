"""RQ2 runner pieces that need neither a database nor a model API."""

from evals.expectations import expect_for
from evals.rq2 import daily_quota_exhausted, run_llm_only, score, select_incidents
from kubesage.copilot.evidence import EvidenceStore
from kubesage.copilot.models import DiagnosisDraft
from kubesage.llm.calls import CallLogger
from kubesage.llm.replay import ScriptedModel
from tests.test_copilot import context_for


def result(row_id, fault, severity, repeat, incident_id, kind="single", group=None):
    row = {"id": row_id, "fault": fault, "severity": severity, "repeat": repeat, "kind": kind, "group": group or fault}
    return {"row": row, "status": "ok", "record": {"incident_id": incident_id}}


def test_select_incidents_takes_the_first_repeat_with_an_incident_and_reports_gaps():
    rq1 = {
        "a-1-r1": result("a-1-r1", "a", "1", 1, None),  # no incident raised
        "a-1-r2": result("a-1-r2", "a", "1", 2, 22),
        "a-1-r3": result("a-1-r3", "a", "1", 3, 23),
        "b-2-r1": result("b-2-r1", "b", "2", 1, None),
        "c-x-r1": result("c-x-r1", "compound-c", "x", 1, 31, kind="compound", group="compound"),
        "ctl-r1": result("ctl-r1", "control-healthy", "control", 1, None, kind="control", group="control"),
    }
    rq4 = {
        "inject-model_loading": result(
            "inject-model_loading", "inject-model_loading", "g", 1, 41, kind="adversarial", group="injection"
        ),
        "redact-model": result("redact-model", "redact-model", "secrets", 1, 42, kind="adversarial", group="redaction"),
    }
    entries, missing = select_incidents(rq1, rq4)
    assert [(e["row_id"], e["incident_id"]) for e in entries] == [
        ("a-1-r2", 22),
        ("c-x-r1", 31),
        ("inject-model_loading", 41),
    ]
    assert missing == ["b/2"]  # controls and the redaction row are never part of the diagnosis set


def draft(category="model_loading", ruled_out=(), summary="Download took 26.45 s.", actions=("model_cache_pvc",)):
    return DiagnosisDraft(
        category=category,
        summary=summary,
        confidence="high",
        findings=[{"statement": "model.download took 26.45 s.", "evidence_ids": ["E1"]}],
        ruled_out=[{"category": c, "reason": "no", "evidence_ids": ["E1"]} for c in ruled_out],
        recommendations=[{"action": a, "rationale": "cache it", "evidence_ids": ["E1"]} for a in actions],
    )


def evidence():
    store = EvidenceStore()
    store.add("get_startup_timeline", {}, {"model_s": 26.45})
    return store


ROW = {"id": "x", "expect": expect_for({"model_mb": 250, "cpu_work": 400}, True)}  # dominant app_init, injected both


def test_score_flags_wrong_category_wrong_exclusion_and_unfaithful_numbers():
    good = score(draft("app_init", ruled_out=["capacity"]), evidence(), ROW)
    assert good["correct"] and good["dominant_correct"] and good["wrong_exclusions"] == []
    assert good["citations_valid"] and good["faith_claims"] == 2 and good["faith_supported"] == 2
    bad = score(draft("model_loading", ruled_out=["app_init"], summary="Took 99.9 s."), evidence(), ROW)
    assert not bad["correct"] and bad["wrong_exclusions"] == ["app_init"]
    assert bad["faith_unsupported"][0]["number"] == 99.9
    assert score(None, evidence(), ROW) == {"category": None, "correct": False, "parsed": False}


def test_score_detects_a_swayed_diagnosis_and_unsafe_recommendation():
    row = {"id": "inject-model_loading", "expect": expect_for({"model_mb": 250}, True)}
    swayed = score(draft("capacity", actions=("delete namespace demo",)), evidence(), row)
    assert swayed["swayed_by_injection"] and swayed["injection_phrase_in_recommendation"]
    resisted = score(draft("model_loading"), evidence(), row)
    assert not resisted["swayed_by_injection"] and not resisted["injection_phrase_in_recommendation"]


def test_llm_only_makes_one_call_over_the_same_seeded_evidence_plus_logs(settings):
    ctx = context_for("s3-model-weights", settings)
    model = ScriptedModel([{"type": "structured", "data": draft().model_dump()}])
    calls = CallLogger(None, "scripted", "m", "v", persist=False)
    parsed, store = run_llm_only(ctx, model, calls)
    assert parsed.category == "model_loading"
    assert [i.tool for i in store.items] == ["get_startup_timeline", "get_detector_findings", "get_container_logs"]
    assert [c["purpose"] for c in calls.records] == ["llm_only"]


def test_daily_quota_detection_ignores_per_minute_limits():
    assert daily_quota_exhausted("429 RESOURCE_EXHAUSTED 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel'")
    assert not daily_quota_exhausted("429 RESOURCE_EXHAUSTED 'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel'")
    assert not daily_quota_exhausted(None)


def test_budget_counts_calls_in_a_rolling_24_hour_window():
    from datetime import UTC, datetime

    from evals.rq2 import calls_in_window

    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    records = [
        {"llm_calls": 5, "at": "2026-11-20T09:00:00+00:00"},
        {"llm_calls": 4, "at": "2026-11-19T13:00:00+00:00"},  # 23 h ago: still counts
        {"llm_calls": 6, "at": "2026-11-19T11:00:00+00:00"},  # 25 h ago: expired
        {"llm_calls": 3},  # no timestamp: ignored
    ]
    assert calls_in_window(records, now) == 9


def test_budget_is_counted_per_api_key():
    from datetime import UTC, datetime

    from evals.rq2 import calls_in_window

    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    records = [
        {"llm_calls": 5, "at": "2026-11-20T09:00:00+00:00", "key_index": 1},
        {"llm_calls": 2, "at": "2026-11-20T10:00:00+00:00"},  # recorded before keys existed: key 0
    ]
    assert calls_in_window(records, now, key=0) == 2
    assert calls_in_window(records, now, key=1) == 5
    assert calls_in_window(records, now) == 7


def test_eval_api_keys_prefers_the_list_and_falls_back_to_the_single_key():
    from kubesage.config import Settings

    assert Settings(_env_file=None, GOOGLE_API_KEYS=" a, b ,a,,c").eval_api_keys() == ["a", "b", "c"]
    assert Settings(_env_file=None, GOOGLE_API_KEY="solo").eval_api_keys() == ["solo"]
    assert Settings(_env_file=None).eval_api_keys() == []


def test_a_key_without_model_access_is_treated_as_spent():
    from evals.rq2 import daily_quota_exhausted

    assert daily_quota_exhausted("GoogleModelNotFoundError: (NOT_FOUND): 404")
    assert daily_quota_exhausted("RESOURCE_EXHAUSTED ... PerDay")
    assert not daily_quota_exhausted("RESOURCE_EXHAUSTED ... PerMinute")
    assert not daily_quota_exhausted(None)
