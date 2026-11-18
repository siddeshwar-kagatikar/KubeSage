"""Evaluation helpers: numeric faithfulness and statistics."""

import pytest

from evals.faithfulness import check, evidence_numbers, is_supported, numbers_in
from evals.stats import bootstrap_median_diff, confusion_matrix, mann_whitney_p, precision_recall, wilson_interval
from kubesage.copilot.evidence import EvidenceStore
from kubesage.copilot.models import DiagnosisDraft

# --- faithfulness -----------------------------------------------------------------------------------------


def test_numbers_in_ignores_identifiers_and_evidence_refs():
    found = numbers_in("S3 took 26.45 s (see E12), 262,144,000 bytes, v1.2 and 0.8118 throttling")
    assert found == [(26.45, 2), (262144000.0, 0), (0.8118, 4)]


def test_evidence_numbers_walks_nested_json_and_strings():
    pool = evidence_numbers(
        {
            "phases": [{"duration_s": 26.45, "ok": True}],
            "message": "Image size: 53299792 bytes",
            "at": "2026-11-09T06:00:00Z",
        }
    )
    assert pool == {26.45, 53299792.0}


@pytest.mark.parametrize(
    ("stated", "decimals", "evidence", "ok"),
    [
        (26.45, 2, 26.4501, True),  # rounding
        (26.5, 1, 26.45, True),
        (648, 0, 647964219, True),  # bytes -> MB
        (12.4, 1, 12430014, True),  # bytes/s -> MB/s
        (82, 0, 0.82, True),  # fraction -> percent
        (0.5, 1, 500, True),  # ms -> s
        (30.0, 1, 26.45, False),  # invented
        (99, 0, 0.82, False),
    ],
)
def test_is_supported(stated, decimals, evidence, ok):
    assert is_supported(stated, decimals, {evidence}) is ok


def draft(**overrides) -> DiagnosisDraft:
    base = {
        "category": "model_loading",
        "summary": "Startup took 31.0 s and model.download took 26.45 s.",
        "confidence": "high",
        "findings": [{"statement": "model.download took 26.45 s of 31.0 s.", "evidence_ids": ["E1"]}],
        "ruled_out": [{"category": "capacity", "reason": "scheduling took 0.0 s", "evidence_ids": ["E1"]}],
        "recommendations": [
            {"action": "model_cache_pvc", "rationale": "Cache the 262 MB weights.", "evidence_ids": ["E1"]}
        ],
    }
    return DiagnosisDraft(**(base | overrides))


def store() -> EvidenceStore:
    evidence = EvidenceStore()
    evidence.add(
        "get_startup_timeline",
        {},
        {"total_s": 31.0, "scheduling_s": 0.0, "model": {"duration_s": 26.45, "bytes": 262144000}},
    )
    evidence.add("get_runbook", {}, {"runbook": "Step 1: check 3 things. Page 12."})
    return evidence


def test_faithful_draft_is_fully_supported():
    result = check(draft(), store())
    # summary 31.0, 26.45; finding 26.45, 31.0; ruled-out 0.0; recommendation 262
    assert result.claims == 6
    assert result.supported == 6 and result.unsupported == [] and result.rate == 1.0


def test_invented_number_is_flagged_with_its_location():
    result = check(
        draft(findings=[{"statement": "The pull took 52.1 s and the pod restarted 4 times.", "evidence_ids": ["E1"]}]),
        store(),
    )
    flagged = {(u["where"], u["number"]) for u in result.unsupported}
    assert flagged == {("finding", 52.1), ("finding", 4.0)}


def test_number_must_come_from_the_cited_evidence_not_just_any_evidence():
    # 12 exists in E2 but the finding cites E1 only
    result = check(draft(findings=[{"statement": "It took 12 s.", "evidence_ids": ["E1"]}]), store())
    assert [u["number"] for u in result.unsupported] == [12.0]
    ok = check(draft(findings=[{"statement": "It took 12 s.", "evidence_ids": ["E2"]}]), store())
    assert ok.unsupported == []


def test_no_numbers_means_no_rate():
    plain = draft(
        summary="It was slow.",
        findings=[{"statement": "No numbers.", "evidence_ids": ["E1"]}],
        ruled_out=[],
        recommendations=[],
    )
    assert check(plain, store()).rate is None


# --- statistics -------------------------------------------------------------------------------------------


def test_wilson_interval():
    lo, hi = wilson_interval(7, 7)
    assert hi == 1.0 and lo == pytest.approx(0.6457, abs=1e-3)
    lo, hi = wilson_interval(5, 10)
    assert (lo, hi) == (pytest.approx(0.2366, abs=1e-3), pytest.approx(0.7634, abs=1e-3))
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_bootstrap_median_diff_is_seeded_and_brackets_the_true_gap():
    baseline = [31, 32, 31, 30, 33, 31, 32, 31]
    candidate = [6, 6, 7, 6, 6, 5, 6, 6]
    first = bootstrap_median_diff(baseline, candidate)
    assert first == bootstrap_median_diff(baseline, candidate)
    diff, lo, hi = first
    assert diff == 25.0 and lo <= diff <= hi and lo > 20


def test_mann_whitney_detects_separation_and_handles_degenerate_input():
    assert mann_whitney_p([31, 32, 31, 30, 33, 31], [6, 6, 7, 6, 5, 6]) < 0.01
    assert mann_whitney_p([1, 1, 1], [1, 1, 1]) is None
    assert mann_whitney_p([], [1]) is None


def test_mcnemar_exact():
    from evals.stats import mcnemar_exact

    only_a, only_b, p = mcnemar_exact([True] * 8 + [False] * 2, [False] * 8 + [True] * 2)
    assert (only_a, only_b) == (8, 2) and p == pytest.approx(0.1094, abs=1e-3)
    assert mcnemar_exact([True, False], [True, False]) == (0, 0, None)


def test_confusion_matrix_and_precision_recall():
    pairs = [("a", "a"), ("a", "b"), ("b", "b"), ("b", "b"), ("c", "b")]
    labels = ["a", "b", "c"]
    assert confusion_matrix(pairs, labels) == [[1, 1, 0], [0, 2, 0], [0, 1, 0]]
    scores = precision_recall(pairs, labels)
    assert scores["a"] == {"precision": 1.0, "recall": 0.5, "support": 2}
    assert scores["b"]["precision"] == pytest.approx(0.5) and scores["b"]["recall"] == 1.0
    assert scores["c"]["precision"] is None and scores["c"]["recall"] == 0.0
