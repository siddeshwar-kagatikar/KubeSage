"""RQ3: do the chosen fixes help, and does the A/B harness tell good fixes from no-ops and harmful ones?

    python -m evals.rq3 main     --model gemini-2.5-flash    # 5 fixable faults x 2 severities, the agent picks the fix
    python -m evals.rq3 controls                              # forced fixes: A/A placebo, harmful, broken (no LLM calls)
    python -m evals.rq3 summary

Runs in this process (the experiment runner and the optimizer are the product code; only the choice of fix can be
forced, never the preparation or the policy checks). Needs the lab cluster, Postgres and the KubeSage backend running:
the backend's profiler records every sandbox trial's startup. Do not restart the backend during a run: its startup
recovery would mark a running experiment failed and clean the sandbox.

Experiments use n=8 trials per arm after one discarded warm-up (alternating baseline/candidate, cold start for both).
"""

import argparse
import json
import time
from pathlib import Path

from evals.campaign import (
    keep_awake,
    preflight,
    run_row,
    set_registry_rate,
    set_slo,
    stop_other_scenarios,
    teardown,
    workload_node,
)
from evals.expectations import DEFAULT_RATE_MBPS, SLO_S, Row, expect_for
from kubesage.audit import audit
from kubesage.collectors.kube import new_admin_client
from kubesage.config import get_settings
from kubesage.db.models import ActionPlan, Experiment
from kubesage.db.session import init_db, session_scope
from kubesage.experiments.runner import ExperimentService
from kubesage.optimizer.service import OptimizerService

HERE = Path(__file__).parent
DATA = HERE / "data"
APPROVER = "evaluation-harness"
TRIALS = 8

# fault -> fixes an SRE would accept (the answer key for action-selection accuracy); empty = no automated fix exists
EXPECTED_ACTIONS = {
    "large_image": {"switch_image_variant", "prepull_daemonset"},
    "slow_registry": {"prepull_daemonset"},
    "model_loading": {"model_cache_pvc"},
    "app_init": {"adjust_cpu_resources"},
    "slow_readiness_probe": {"tune_probes"},
}

MAIN = [  # (fault, severity label, knobs)
    ("large_image", "20MBps", {"image": "fat", "registry_mbps": 20}),
    ("large_image", "8MBps", {"image": "fat", "registry_mbps": 8}),
    ("slow_registry", "2MBps", {"image": "slim", "registry_mbps": 2}),
    ("slow_registry", "1MBps", {"image": "slim", "registry_mbps": 1}),
    ("model_loading", "250MB", {"model_mb": 250}),
    ("model_loading", "500MB", {"model_mb": 500}),
    ("app_init", "250u", {"cpu_work": 250}),
    ("app_init", "400u", {"cpu_work": 400}),
    ("slow_readiness_probe", "delay45", {"probe": {"initial_delay": 45, "period": 20}}),
    ("slow_readiness_probe", "delay90", {"probe": {"initial_delay": 90, "period": 20}}),
]

# control -> (knobs of the base incident, forced fix, how many experiments, the verdict the harness must return)
CONTROLS = {
    # the dependency answers at once in the sandbox, so the baseline starts fast and this changes nothing
    "aa": (
        {"dependency_delay_s": 40},
        ("tune_probes", {"initial_delay_seconds": 0, "period_seconds": 1}),
        6,
        "no_change",
    ),
    "harmful": (
        {"dependency_delay_s": 40},
        ("tune_probes", {"initial_delay_seconds": 30, "period_seconds": 10}),
        3,
        "regressed",
    ),
    "broken": ({"image": "fat", "registry_mbps": 20}, ("switch_image_variant", {"tag": "doesnotexist"}), 3, "failed"),
}


def main_rows() -> list[Row]:
    return [
        Row(f"rq3-{fault}-{sev}", fault, "rq3", "single", sev, 1, True, knobs, expect_for(knobs, True))
        for fault, sev, knobs in MAIN
    ]


def control_row(name: str) -> Row:
    knobs = CONTROLS[name][0]
    return Row(f"rq3-control-{name}", f"control-{name}", "rq3", "single", name, 1, True, knobs, expect_for(knobs, True))


def read_jsonl(path: Path) -> list[dict]:
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if path.exists()
        else []
    )


def snapshot_experiment(experiment_id: int) -> dict:
    with session_scope() as session:
        e = session.get(Experiment, experiment_id)
        p = e.plan
        return {
            "experiment_id": e.id,
            "plan_id": p.id,
            "status": e.status,
            "verdict": e.verdict,
            "error": e.error,
            "trials_per_arm": e.trials_per_arm,
            "wall_s": round((e.finished_at - e.started_at).total_seconds(), 1) if e.finished_at else None,
            "prep": e.prep,
            "comparison": e.comparison,
            "action": p.action,
            "params": p.params,
            "plan_source": p.source,
            "rationale": p.rationale,
            "policy_passed": all(c["passed"] for c in p.policy_checks),
            "trials": [
                {
                    "index": t.index, "arm": t.arm, "warmup": t.warmup, "status": t.status, "total_s": t.total_s,
                    "phases": t.phases, "prep_s": t.prep_s, "guardrails": t.guardrails, "error": t.error,
                }
                for t in e.trials
            ],
        }  # fmt: skip


def wait_experiment(experiment_id: int, timeout_s: float = 4 * 3600) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        with session_scope() as session:
            if session.get(Experiment, experiment_id).status != "running":
                return
        if time.monotonic() > deadline:
            raise TimeoutError(f"experiment {experiment_id} still running after {timeout_s / 3600:.0f} h")
        time.sleep(10)


def plan_state(plan_id: int) -> dict:
    with session_scope() as session:
        p = session.get(ActionPlan, plan_id)
        return {
            "plan_id": p.id, "status": p.status, "action": p.action, "params": p.params, "source": p.source,
            "rationale": p.rationale, "llm_error": p.llm_error, "policy_checks": p.policy_checks,
        }  # fmt: skip


def prepare_environment(args):
    init_db()
    preflight(args.api)
    settings = get_settings()
    if not settings.lab_mode:
        raise SystemExit("KUBESAGE_LAB_MODE must be true (cold-start trials evict images on the sandbox node)")
    if getattr(args, "model", None):
        settings = settings.model_copy(update={"llm_model": args.model})
    api = new_admin_client(settings.kube_context)
    node = workload_node(api)
    set_slo(SLO_S)
    stop_other_scenarios(api)
    experiments = ExperimentService(settings)
    return settings, api, node, experiments, OptimizerService(settings, experiments)


def cmd_main(args) -> None:
    settings, api, node, experiments, optimizer = prepare_environment(args)
    out = DATA / "rq3-main.jsonl"
    done = {r["row"]["id"] for r in read_jsonl(out) if r["status"] == "ok"}
    rows = [r for r in main_rows() if r.id not in done and (not args.only or r.id in args.only)]
    print(f"{len(rows)} experiment(s) to run; model {settings.llm_model}")
    keep_awake(True)
    audit("rq3.started", "main", rows=len(rows))
    try:
        for row in rows:
            started = time.monotonic()
            record: dict = {"row": row.to_dict(), "status": "failed", "error": None}
            try:
                base = run_row(api, node, row.to_dict(), keep=True)
                record["incident"] = {
                    k: base["record"].get(k) for k in ("incident_id", "total_s", "top_category", "startup_id")
                }
                if base["status"] != "ok" or not base["record"].get("incident_id"):
                    raise RuntimeError(f"no incident for the base run: {base['error']}")
                plan_id = optimizer.create_plan(base["record"]["incident_id"])
                record["plan"] = plan_state(plan_id)
                record["action_correct"] = record["plan"]["action"] in EXPECTED_ACTIONS[row.fault]
                if record["plan"]["status"] != "awaiting_approval":
                    raise RuntimeError(f"plan is {record['plan']['status']}")
                experiment_id = optimizer.approve(plan_id, APPROVER, trials_per_arm=TRIALS)
                wait_experiment(experiment_id)
                record["experiment"] = snapshot_experiment(experiment_id)
                record["status"] = "ok" if record["experiment"]["status"] == "completed" else "failed"
                record["error"] = record["experiment"]["error"]
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                teardown(api)
                set_registry_rate(DEFAULT_RATE_MBPS)
            record["wall_s"] = round(time.monotonic() - started, 1)
            with out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
            exp = record.get("experiment") or {}
            print(f"{row.id:<34} {record['status']:<7} action={record.get('plan', {}).get('action')} "
                  f"verdict={exp.get('verdict')} delta={exp.get('comparison', {}).get('delta_s')} "
                  f"({record['wall_s'] / 60:.0f} min) {record['error'] or ''}", flush=True)  # fmt: skip
    finally:
        keep_awake(False)
        experiments.stop()
        audit("rq3.finished", "main")


def cmd_controls(args) -> None:
    _, api, node, experiments, optimizer = prepare_environment(args)
    out = DATA / "rq3-controls.jsonl"
    existing = [r for r in read_jsonl(out) if r["status"] == "ok"]
    keep_awake(True)
    audit("rq3.started", "controls")
    try:
        for name in args.only or CONTROLS:
            _, forced, repeats, expected = CONTROLS[name]
            repeats = min(repeats, args.repeats or repeats)  # --repeats 1 = a quick sanity run of the protocol
            have = sum(1 for r in existing if r["control"] == name)
            if have >= repeats:
                continue
            base = run_row(api, node, control_row(name).to_dict(), keep=True)
            incident_id = base["record"].get("incident_id")
            if base["status"] != "ok" or not incident_id:
                print(f"{name}: no incident for the base run: {base['error']}")
                teardown(api)
                continue
            try:
                for k in range(have + 1, repeats + 1):
                    started = time.monotonic()
                    record: dict = {
                        "control": name,
                        "repeat": k,
                        "expected": expected,
                        "forced": forced,
                        "status": "failed",
                    }
                    try:
                        plan_id = optimizer.create_plan(incident_id, forced=forced)
                        record["plan"] = plan_state(plan_id)
                        if record["plan"]["status"] != "awaiting_approval":
                            raise RuntimeError(f"plan is {record['plan']['status']}: {record['plan']['policy_checks']}")
                        experiment_id = optimizer.approve(plan_id, APPROVER, trials_per_arm=TRIALS)
                        wait_experiment(experiment_id)
                        record["experiment"] = snapshot_experiment(experiment_id)
                        record["status"] = "ok" if record["experiment"]["status"] == "completed" else "failed"
                        record["verdict"] = record["experiment"]["verdict"]
                        record["correct"] = record["verdict"] == expected
                    except Exception as exc:
                        record["error"] = f"{type(exc).__name__}: {exc}"
                    record["wall_s"] = round(time.monotonic() - started, 1)
                    with out.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(record, default=str) + "\n")
                    print(f"{name:<8} #{k}  expected={expected:<10} got={record.get('verdict')} "
                          f"({record['wall_s'] / 60:.0f} min) {record.get('error') or ''}", flush=True)  # fmt: skip
            finally:
                teardown(api)
                set_registry_rate(DEFAULT_RATE_MBPS)
    finally:
        keep_awake(False)
        experiments.stop()
        audit("rq3.finished", "controls")


def cmd_summary(args) -> None:
    print("main experiments")
    for r in read_jsonl(DATA / "rq3-main.jsonl"):
        exp = r.get("experiment") or {}
        c = exp.get("comparison") or {}
        print(f"  {r['row']['id']:<34} {r['status']:<7} {r.get('plan', {}).get('action')!s:<22} correct={r.get('action_correct')} "
              f"verdict={exp.get('verdict')} base={c.get('baseline', {}).get('median_s')} cand={c.get('candidate', {}).get('median_s')}")  # fmt: skip
    print("controls (expected -> got)")
    for r in read_jsonl(DATA / "rq3-controls.jsonl"):
        print(
            f"  {r['control']:<8} #{r['repeat']} {r['expected']:<10} -> {r.get('verdict')}  {'ok' if r.get('correct') else 'MISMATCH'}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("main", cmd_main), ("controls", cmd_controls), ("summary", cmd_summary)):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if name != "summary":
            p.add_argument("--api", default="http://127.0.0.1:8000")
            p.add_argument("--only", nargs="+")
        if name == "main":
            p.add_argument("--model", help="model for the fix choice (default: KUBESAGE_LLM_MODEL)")
        if name == "controls":
            p.add_argument("--repeats", type=int, help="run at most this many experiments per control")
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
