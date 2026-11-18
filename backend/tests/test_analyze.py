"""The analysis script's aggregation, on small hand-built datasets."""

import pytest

from evals import analyze
from evals.expectations import evaluate_row, expect_for


def result(row_id, fault, kind, knobs, record, cold=True):
    row = {"id": row_id, "fault": fault, "kind": kind, "group": fault, "expect": expect_for(knobs, cold)}
    return {"row": row, "status": "ok", "record": record, "outcome": evaluate_row(row, record)}


def record(top="model_loading", incident=1, **kw):
    base = {
        "top_category": top,
        "candidates": [{"category": top}] if top else [],
        "incident_id": incident,
        "signals": {"model_s": 26.4},
        "phases": [],
        "total_s": 34.0,
        "observed_total_s": 34.6,
        "finalise_lag_s": 3.1,
    }
    return base | kw


MODEL = {"model_mb": 250}
GOOD = result("m-1", "model_loading", "single", MODEL, record())
WRONG = result("m-2", "model_loading", "single", MODEL, record(top="app_init"))
QUIET = result(
    "c-1",
    "control-healthy",
    "control",
    {},
    record(top=None, incident=None, signals={}, total_s=7.0, observed_total_s=7.2),
)
NOISY = result("c-2", "control-healthy", "control", {}, record(top="app_init", incident=None, signals={}))


def test_detection_table_counts_accuracy_false_alarms_and_ranges():
    table = analyze.rq1_detection([GOOD, WRONG, QUIET, NOISY])
    rows = {r[0]: r for r in table.rows}
    assert rows["model_loading"][1:4] == [2, "1/2 (0.09-0.91)", "1/2"]  # runs, top-1 with CI, recall
    assert rows["model_loading"][5] == "2/2"  # model_s 26.4 is inside [25, 32] in both rows
    assert rows["control (all)"][6] == "1/2"  # one control raised a false alarm
    assert table.rows[-1][0] == "control (all)"  # summary rows come last


def test_confusion_counts_a_correct_acceptable_answer_on_the_diagonal():
    pairs = analyze.confusion_pairs([GOOD, WRONG, QUIET, NOISY])
    assert pairs == [
        ("model_loading", "model_loading"),
        ("model_loading", "app_init"),
        ("none", "none"),
        ("none", "app_init"),
    ]
    table, matrix = analyze.rq1_confusion([GOOD, WRONG, QUIET, NOISY])
    labels = analyze.LABELS
    assert matrix[labels.index("model_loading")][labels.index("app_init")] == 1
    assert table.header[0].startswith("expected") and len(table.rows) == len(labels)


def test_fidelity_and_agreement():
    fidelity = analyze.rq1_fidelity([GOOD, WRONG])
    assert fidelity.rows[0][:3] == ["model_s", 2, "2/2"]
    agreement, errors = analyze.rq1_agreement([GOOD, QUIET])
    assert errors == pytest.approx([0.6, 0.2]) and agreement.rows[0][1] == 2
    assert agreement.rows[1][2] == "3.1"  # median recording lag


def system_row(row_id, kind, correct, first_correct, first_valid, source="llm"):
    score = {
        "correct": correct,
        "citations_valid": True,
        "faith_claims": 4,
        "faith_supported": 4,
        "wrong_exclusions": [],
    }
    return {
        "row_id": row_id, "kind": kind, "status": "ok", "source": source, "llm_calls": 5, "input_tokens": 1000,
        "output_tokens": 200, "seconds": 20.0, **score,
        "first_draft": {**score, "correct": first_correct},
        "attempts": [{"attempt": 1, "valid": first_valid, "errors": [], "category": "x"}],
    }  # fmt: skip


def test_rq2_tables_compare_systems_and_the_validator():
    copilot = [
        system_row("a", "single", True, True, True),
        system_row("b", "compound", True, False, False),
        system_row("c", "single", False, False, False, "fallback"),
    ]
    rules = [{"row_id": r, "kind": k, "status": "ok", "source": "rules", "correct": c, "llm_calls": 0, "faith_claims": None,
              "faith_supported": None, "citations_valid": None, "wrong_exclusions": [], "input_tokens": 0, "output_tokens": 0, "seconds": 0.0}
             for r, k, c in (("a", "single", True), ("b", "compound", False), ("c", "single", False))]  # fmt: skip
    tables = {t.name: t for t in analyze.rq2_tables({("copilot", "m1"): copilot, ("rules", "-"): rules})}
    quality = {r[0]: r for r in tables["rq2_quality"].rows}
    assert quality["rules"][2].startswith("1/3") and quality["copilot (m1)"][2].startswith("2/3")
    assert quality["copilot (m1)"][3].startswith("1/1")  # the one compound incident
    validator = {r[0]: r for r in tables["rq2_validator"].rows}
    assert validator["copilot, first draft (m1)"][2].startswith("1/3")  # before validation: only incident a is right
    assert validator["copilot, validated (m1)"][2].startswith("2/3")
    assert validator["validator activity (m1)"][2:5] == [
        "first draft invalid: 2/3",
        "retry recovered: 1/2",
        "fallback: 1/3",
    ]
    assert tables["rq2_cost"].rows[0][2] == "5.0"


def experiment(verdict, base, cand, action="model_cache_pvc"):
    trials = [{"arm": "baseline", "warmup": True, "status": "ok", "total_s": 99.0}]
    trials += [{"arm": "baseline", "warmup": False, "status": "ok", "total_s": v} for v in base]
    trials += [{"arm": "candidate", "warmup": False, "status": "ok", "total_s": v} for v in cand]
    trials += [{"arm": "candidate", "warmup": False, "status": "failed", "total_s": None}]
    return {"action": action, "verdict": verdict, "trials": trials, "prep": {"x_s": 38.0}, "wall_s": 600.0,
            "comparison": {"candidate": {"prep_median_s": None}}}  # fmt: skip


def test_rq3_tables():
    main = [
        {
            "row": {"id": "rq3-model_loading-250MB"},
            "action_correct": True,
            "experiment": experiment("improved", [31, 32, 31, 30, 33, 31], [6, 6, 7, 6, 5, 6]),
        }
    ]
    table = analyze.rq3_main_table(main)
    row = table.rows[0]
    assert row[:4] == ["model_loading-250MB", "model_cache_pvc", "yes", "improved"]
    assert (
        row[4].startswith("31.0 [") and row[5].startswith("6.0 [") and row[8] == "6/6"
    )  # warm-up and failed trial excluded
    assert row[6].startswith("25.0 (") and float(row[7]) < 0.01 and row[9] == "38.0"
    controls = [
        {"control": "aa", "status": "ok", "expected": "no_change", "verdict": "no_change", "correct": True},
        {"control": "aa", "status": "ok", "expected": "no_change", "verdict": "improved", "correct": False},
        {"control": "broken", "status": "ok", "expected": "failed", "verdict": "failed", "correct": True},
    ]
    rows = {r[0]: r for r in analyze.rq3_controls_table(controls).rows}
    assert rows["aa"][3] == "improved: 1, no_change: 1" and rows["aa"][4].startswith("1/2")
    assert rows["broken"][4].startswith("1/1")


def test_save_writes_csv_and_escaped_latex(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "TABLES", tmp_path)
    analyze.save(analyze.Table("t", "50% of runs_ok", ["a_b", "n"], [["x_y", 3]]))
    assert (tmp_path / "t.csv").read_text(encoding="utf-8").splitlines() == ["a_b,n", "x_y,3"]
    tex = (tmp_path / "t.tex").read_text(encoding="utf-8")
    assert "x\\_y & 3" in tex and "50\\% of runs\\_ok" in tex and "\\toprule" in tex
