"""Copilot graph behaviour with a scripted model (no API calls)."""

import pytest

from kubesage.copilot.context import IncidentContext
from kubesage.copilot.evidence import EvidenceStore
from kubesage.copilot.graph import run_copilot
from kubesage.copilot.tools import build_tools
from kubesage.llm.calls import CallLogger
from kubesage.llm.replay import ScriptedModel
from kubesage.profiler.detectors import detect
from kubesage.profiler.service import snapshot_pod
from tests.conftest import load_fixture, timeline_for


def context_for(fixture: str, settings) -> IncidentContext:
    pod, events, _ = load_fixture(fixture)
    tl = timeline_for(fixture)
    candidates = [c.to_dict() for c in detect(tl, settings)]
    return IncidentContext(
        incident_id=1,
        workload={"namespace": tl.namespace, "kind": "Deployment", "name": "model-weights-svc", "slo_seconds": 30.0},
        incident={
            "total_s": tl.total_s,
            "threshold_s": 30.0,
            "breach_reason": "slo",
            "baseline_s": None,
            "top_category": candidates[0]["category"],
            "candidates": candidates,
        },
        startup={
            "pod_name": tl.pod_name,
            "namespace": tl.namespace,
            "node": tl.node,
            "image": tl.image,
            "created_at": tl.created_at.isoformat(),
            "ready_at": tl.ready_at.isoformat(),
            "total_s": tl.total_s,
            "cold": tl.cold,
            "phases": [p.to_dict() for p in tl.phases],
            "signals": tl.signals,
        },
        pod_snapshot=snapshot_pod(pod),
        events=[
            {"reason": e["reason"], "type": e["type"], "message": e["message"], "count": e.get("count", 1)}
            for e in events
        ],
        logs='{"event": "phase_end", "phase": "model.download", "duration_s": 26.45}\nAPI_TOKEN=abc123secret',
    )


def tool_turn(*calls: tuple[str, dict]) -> dict:
    return {"type": "ai", "content": "", "tool_calls": [{"name": n, "args": a} for n, a in calls]}


DONE = {"type": "ai", "content": "Model download dominates startup.", "tool_calls": []}


def draft(evidence_ids=("E1", "E3"), category="model_loading", **extra) -> dict:
    return {
        "type": "structured",
        "data": {
            "category": category,
            "summary": "The pod spent most of its startup downloading model weights.",
            "confidence": "high",
            "findings": [
                {"statement": "model.download took 26.45 s of a 35 s startup.", "evidence_ids": list(evidence_ids)}
            ],
            "ruled_out": [],
            "recommendations": [
                {
                    "action": "model_cache_pvc",
                    "rationale": "Weights are re-downloaded on every start.",
                    "evidence_ids": ["E4"],
                }
            ],
            **extra,
        },
    }


def run(fixture, settings, records, model=None):
    ctx = context_for(fixture, settings)
    evidence = EvidenceStore()
    tools = build_tools(ctx, evidence)
    calls = CallLogger(None, "scripted", "scripted", "test", persist=False)
    result = run_copilot(ctx, model or ScriptedModel(records), tools, evidence, calls, settings)
    return result, calls


def test_grounded_diagnosis_is_accepted(settings):
    result, calls = run(
        "s3-model-weights",
        settings,
        [
            tool_turn(("get_container_logs", {"contains": "model"})),
            tool_turn(("get_runbook", {"category": "model_loading"})),
            DONE,
            draft(),
        ],
    )
    assert result.source == "llm"
    assert result.draft.category == "model_loading"
    assert [i.tool for i in result.evidence.items] == [
        "get_startup_timeline",
        "get_detector_findings",
        "get_container_logs",
        "get_runbook",
    ]
    assert result.tool_calls == 2
    assert [c["purpose"] for c in calls.records] == ["investigate", "investigate", "investigate", "synthesise"]


def test_still_invalid_after_retry_falls_back(settings):
    # Only E1-E3 exist here. Both drafts' recommendations cite E4, so both fail validation.
    result, _ = run(
        "s3-model-weights",
        settings,
        [tool_turn(("get_runbook", {"category": "model_loading"})), DONE, draft(evidence_ids=("E9",)), draft(("E1",))],
    )
    assert result.source == "fallback"
    assert any("unknown evidence E4" in e for e in result.validation_errors)


def test_retry_succeeds_after_feedback(settings):
    fixed = draft(("E1",))
    fixed["data"]["recommendations"][0]["evidence_ids"] = ["E3"]
    result, _ = run(
        "s3-model-weights",
        settings,
        [tool_turn(("get_runbook", {"category": "model_loading"})), DONE, draft(evidence_ids=("E9",)), fixed],
    )
    assert result.source == "llm"
    assert result.validation_errors == []


def test_llm_outage_falls_back_to_detectors(settings):
    class Broken:
        def bind_tools(self, tools):
            return self

        def with_structured_output(self, *a, **k):
            return self

        def invoke(self, _):
            raise ConnectionError("quota exceeded")

    result, calls = run("s3-model-weights", settings, [], model=Broken())
    assert result.source == "fallback"
    assert result.draft.category == "model_loading"
    assert "quota exceeded" in result.llm_error
    assert calls.records[0]["status"] == "error"
    assert {ref for f in result.draft.findings for ref in f.evidence_ids} <= {"E1", "E2"}


def test_tool_budget_is_enforced(settings):
    settings.copilot_max_tool_calls = 2
    turns = [tool_turn(("get_pod_spec", {}), ("get_pod_events", {}), ("get_workload_history", {}))]
    result, _ = run("s3-model-weights", settings, [*turns, draft(("E1", "E3"), recommendations=[])])
    assert result.tool_calls == 2
    assert len(result.evidence.items) == 4  # E1, E2 seeded + 2 executed; the third call was refused
    assert result.source == "llm"


def test_diverging_from_strong_detector_requires_ruling_it_out(settings):
    result, _ = run(
        "s3-model-weights",
        settings,
        [
            DONE,
            draft(("E1",), category="app_init", recommendations=[]),
            draft(("E1",), category="app_init", recommendations=[]),
        ],
    )
    assert result.source == "fallback"
    assert any("model_loading" in e for e in result.validation_errors)


def test_logs_and_env_are_redacted(settings):
    ctx = context_for("s3-model-weights", settings)
    evidence = EvidenceStore()
    tools = {t.name: t for t in build_tools(ctx, evidence)}
    out = tools["get_container_logs"].invoke({})
    assert "abc123secret" not in out


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("password=hunter2", "hunter2"),
        ('{"api_key": "sk-abcdefghijklmnopqrstu"}', "abcdefghijklmnop"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc", "eyJhbGci"),
        ("postgres://user:s3cr3t@db:5432/x", "s3cr3t"),
        ("key AIzaSyA1234567890abcdefghijklmnopqrstuv", "AIzaSy"),
    ],
)
def test_redact_text(text, secret):
    from kubesage.llm.redact import redact_text

    assert secret not in redact_text(text)


def test_redact_env():
    from kubesage.llm.redact import redact_env

    env = redact_env(
        [
            {"name": "DB_PASSWORD", "value": "x"},
            {"name": "MODEL_URL", "value": "http://hub/weights.bin"},
            {"name": "TOKEN", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}},
        ]
    )
    assert env[0]["value"] == "[REDACTED]"
    assert env[1]["value"] == "http://hub/weights.bin"
    assert env[2]["valueFrom"] == "secret s/k"


PER_MINUTE = (
    "429 RESOURCE_EXHAUSTED ... 'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier' ... "
    "Please retry in 31.8s."
)
PER_DAY = "429 RESOURCE_EXHAUSTED ... 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel' ... retry in 2455s"


def test_per_minute_rate_limit_is_retried_after_suggested_delay(monkeypatch):
    from kubesage.llm import calls as calls_module

    sleeps = []
    monkeypatch.setattr(calls_module.time, "sleep", sleeps.append)
    attempts = iter([RuntimeError(PER_MINUTE), "ok"])

    def invoke():
        item = next(attempts)
        if isinstance(item, Exception):
            raise item
        return item

    logger = CallLogger(None, "p", "m", "v", persist=False)
    assert logger.call("investigate", invoke) == "ok"
    assert sleeps == [pytest.approx(32.8)]
    assert [r["status"] for r in logger.records] == ["error", "ok"]


def test_daily_quota_and_repeated_limits_are_not_retried_forever(monkeypatch):
    from kubesage.llm import calls as calls_module

    monkeypatch.setattr(calls_module.time, "sleep", lambda _s: None)
    logger = CallLogger(None, "p", "m", "v", persist=False)

    def daily():
        raise RuntimeError(PER_DAY)

    with pytest.raises(RuntimeError):
        logger.call("investigate", daily)
    assert len(logger.records) == 1

    def always_per_minute():
        raise RuntimeError(PER_MINUTE)

    with pytest.raises(RuntimeError):
        logger.call("investigate", always_per_minute)
    assert len(logger.records) == 1 + 3  # first try + 2 retries
