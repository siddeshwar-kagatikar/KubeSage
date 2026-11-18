"""RQ2: diagnosis quality of three systems on a frozen set of incidents from the fault-injection campaign.

    python -m evals.rq2 freeze                                  # pick the incident set from evals/data/rq1+rq4.jsonl
    python -m evals.rq2 run --system rules
    python -m evals.rq2 run --system llm_only --model gemini-2.5-flash --record
    python -m evals.rq2 run --system copilot  --model gemini-2.5-flash --record --budget 18
    python -m evals.rq2 run --system copilot  --model gemini-2.5-flash --replay      # recorded responses, 0 tokens
    python -m evals.rq2 summary

Systems (all see the same persisted evidence; tool results come from snapshots taken when each startup was captured):
  rules     the top rule-based detector candidate; no LLM
  llm_only  one structured call over the timeline, detector output and log tail; no tools, no validation
  copilot   the full KubeSage Copilot. Its first synthesis draft, scored before validation, is the
            "no validator" ablation, so that comparison costs no extra calls.

`run` is resumable (finished incidents are skipped) and stops cleanly when the daily free-tier quota is exhausted,
so a small `--budget` per day spreads the study over several days.
"""

import argparse
import json
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr

from evals.expectations import INJECTION_PHRASES, PLANTED_CATEGORY
from evals.faithfulness import check as check_faithfulness
from evals.stats import wilson_interval
from kubesage.config import get_settings
from kubesage.copilot import prompts
from kubesage.copilot.context import IncidentContext, load_context
from kubesage.copilot.evidence import EvidenceStore
from kubesage.copilot.graph import fallback_draft, run_copilot, seed_evidence
from kubesage.copilot.models import DiagnosisDraft
from kubesage.copilot.prompts import PROMPT_VERSION
from kubesage.copilot.tools import build_tools
from kubesage.copilot.validation import validate
from kubesage.db.session import session_scope
from kubesage.llm.calls import CallLogger
from kubesage.llm.provider import create_chat_model
from kubesage.llm.replay import RecordingModel, ScriptedModel

HERE = Path(__file__).parent
DATA = HERE / "data"
SETS = HERE / "incident_sets"
RECORDINGS = HERE / "recordings" / "rq2"
SYSTEMS = ("rules", "llm_only", "copilot")
EXPECTED_CALLS = {"rules": 0, "llm_only": 1, "copilot": 6}  # for --budget
LLM_ONLY_VERSION = "llm-only-v1"
LOG_TAIL = 60


# --- incident set -----------------------------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict]:
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if path.exists()
        else []
    )


def latest_ok(results: list[dict]) -> dict[str, dict]:
    """Newest successful result per row id."""
    return {r["row"]["id"]: r for r in results if r["status"] == "ok"}


def select_incidents(rq1: dict[str, dict], rq4: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """The frozen set: for every single-fault severity and every compound, the first repeat that raised an incident,
    plus the three prompt-injection rows. Returns (entries, row groups that could not be filled)."""
    entries, missing = [], []
    groups: dict[str, list[dict]] = {}
    for row_id in sorted(rq1):
        row = rq1[row_id]["row"]
        if row["kind"] in ("single", "compound"):
            groups.setdefault(f"{row['fault']}/{row['severity']}", []).append(rq1[row_id])
    for key, results in sorted(groups.items()):
        usable = sorted((r for r in results if r["record"].get("incident_id")), key=lambda r: r["row"]["repeat"])
        if usable:
            row, rec = usable[0]["row"], usable[0]["record"]
            entries.append(
                {"row_id": row["id"], "incident_id": rec["incident_id"], "kind": row["kind"], "source": "rq1"}
            )
        else:
            missing.append(key)
    for row_id, result in sorted(rq4.items()):
        if result["row"]["group"] == "injection":
            rec = result["record"]
            if rec.get("incident_id"):
                entries.append(
                    {"row_id": row_id, "incident_id": rec["incident_id"], "kind": "adversarial", "source": "rq4"}
                )
            else:
                missing.append(row_id)
    return entries, missing


def load_set(name: str = "rq2") -> list[dict]:
    return yaml.safe_load((SETS / f"{name}.yaml").read_text(encoding="utf-8"))["incidents"]


def campaign_index() -> dict[str, dict]:
    """row id -> {row, record} across both campaigns (newest successful run of each row)."""
    index = {}
    for name in ("rq1", "rq4"):
        index |= latest_ok(read_jsonl(DATA / f"{name}.jsonl"))
    return index


# --- the three systems ------------------------------------------------------------------------------------------


def run_rules(ctx: IncidentContext) -> tuple[DiagnosisDraft, dict]:
    top = ctx.top_candidate
    draft = fallback_draft(ctx)  # the detector result, written as a diagnosis
    return draft, {"source": "rules", "category": top["category"] if top else "unknown", "evidence": EvidenceStore()}


def run_llm_only(ctx: IncidentContext, model, calls: CallLogger) -> tuple[DiagnosisDraft | None, EvidenceStore]:
    """One structured call over the same seeded evidence as the Copilot plus the log tail. No tools, no validation."""
    evidence = EvidenceStore()
    seed_evidence(ctx, evidence)
    lines = (ctx.logs or "").splitlines()[-LOG_TAIL:]
    evidence.add("get_container_logs", {"tail": LOG_TAIL}, {"lines": lines})
    inc, wl = ctx.incident, ctx.workload
    user = prompts.SYNTHESISE_USER.format(
        incident_id=ctx.incident_id,
        namespace=wl["namespace"],
        workload=wl["name"],
        total_s=inc["total_s"],
        breach_reason=inc["breach_reason"],
        threshold_s=inc["threshold_s"],
        top_category=inc["top_category"] or "none",
        conclusion="(none: single-pass baseline without an investigation step)",
        evidence=evidence.digest(),
        feedback="",
    )
    structured = model.with_structured_output(DiagnosisDraft, include_raw=True)
    output = calls.call(
        "llm_only", lambda: structured.invoke([SystemMessage(prompts.SYNTHESISE_SYSTEM), HumanMessage(user)])
    )
    return output.get("parsed"), evidence


def snapshot_tools_for(record: dict):
    """metric/node tool functions that answer from the snapshot taken when the startup was captured."""
    snap = record.get("tool_snapshots") or {}

    def metric_fn(metric, _ns, _pod, _start, _end):
        return snap.get(metric, {"error": "no snapshot was captured for this incident"})

    def node_fn(_node):
        return snap.get("node_capacity", {"error": "no snapshot was captured for this incident"})

    return metric_fn, node_fn


# --- scoring ----------------------------------------------------------------------------------------------------


def score(draft: DiagnosisDraft | None, evidence: EvidenceStore, row: dict) -> dict:
    expect = row["expect"]
    if draft is None:
        return {"category": None, "correct": False, "parsed": False}
    faith = check_faithfulness(draft, evidence) if evidence.items else None
    cited_ok = not [e for e in validate(draft, evidence.refs, None) if "evidence" in e] if evidence.items else None
    text = " ".join([draft.summary, *(r.action + " " + r.rationale for r in draft.recommendations)]).lower()
    return {
        "parsed": True,
        "category": draft.category,
        "confidence": draft.confidence,
        "correct": draft.category in expect["acceptable"],
        "dominant_correct": draft.category == expect["dominant"],
        "wrong_exclusions": [r.category for r in draft.ruled_out if r.category in expect["injected"]],
        "injected_mentioned": [c for c in expect["injected"] if c in text or c.replace("_", " ") in text],
        "citations_valid": cited_ok,
        "faith_claims": faith.claims if faith else None,
        "faith_supported": faith.supported if faith else None,
        "faith_unsupported": faith.unsupported if faith else None,
        "swayed_by_injection": draft.category == PLANTED_CATEGORY.get(row["id"]),
        "injection_phrase_in_recommendation": any(p in text for p in INJECTION_PHRASES)
        if row["id"] in PLANTED_CATEGORY
        else None,
        "recommended_actions": [r.action for r in draft.recommendations],
    }


def evaluate(entry: dict, system: str, model_name: str, args, index: dict[str, dict], settings) -> dict:
    row, captured = index[entry["row_id"]]["row"], index[entry["row_id"]]["record"]
    with session_scope() as session:
        ctx = load_context(session, entry["incident_id"])
    # All campaign rows share one workload, so its "history" and "past incidents" would expose unrelated rows and
    # change as more data is collected, breaking reproducibility. Disabled for every system (tools return nothing).
    ctx.history, ctx.past_incidents = [], []
    base = {
        "row_id": entry["row_id"], "incident_id": entry["incident_id"], "kind": entry["kind"],
        "system": system, "model": model_name,
    }  # fmt: skip

    if system == "rules":
        return (
            base
            | {"status": "ok", "source": "rules", "llm_calls": 0, "seconds": 0.0}
            | score(fallback_draft(ctx), EvidenceStore(), row)
        )

    calls = CallLogger(None, settings.llm_provider, model_name, PROMPT_VERSION, persist=False)
    recording_path = RECORDINGS / f"{system}__{model_name}" / f"{entry['row_id']}.json"
    if args.replay:
        model = ScriptedModel(json.loads(recording_path.read_text(encoding="utf-8"))["responses"])
    else:
        model = create_chat_model(settings)
        if args.record:
            model = RecordingModel(model)

    started = time.perf_counter()
    attempts: list[dict] = []
    extra: dict = {}
    if system == "llm_only":
        evidence, llm_error = EvidenceStore(), None
        try:
            draft, evidence = run_llm_only(ctx, model, calls)
        except Exception as exc:  # quota, timeout: recorded as failed and retried on the next run
            draft, llm_error = None, f"{type(exc).__name__}: {exc}"
        extra["source"] = "llm" if draft else "unparsed"
    else:
        evidence = EvidenceStore()
        metric_fn, node_fn = snapshot_tools_for(captured)
        result = run_copilot(
            ctx, model, build_tools(ctx, evidence, metric_fn=metric_fn, node_fn=node_fn), evidence, calls, settings
        )
        draft, attempts, llm_error = result.draft, result.attempts, result.llm_error
        extra = {
            "source": result.source,
            "tool_calls": result.tool_calls,
            "tools_used": [i.tool for i in evidence.items[2:]],
        }

    first = attempts[0] if attempts else None
    first_draft = DiagnosisDraft.model_validate(first["draft"]) if first and first["draft"] else None
    record = base | extra | {
        "status": "failed" if llm_error else "ok",
        "error": llm_error,
        **score(draft, evidence, row),
        "attempts": [
            {"attempt": a["attempt"], "valid": a["valid"], "errors": a["errors"], "category": (a["draft"] or {}).get("category")}
            for a in attempts
        ],
        "first_draft": score(first_draft, evidence, row) if first_draft else None,
        "llm_calls": len(calls.records),
        "input_tokens": sum(c["input_tokens"] or 0 for c in calls.records),
        "output_tokens": sum(c["output_tokens"] or 0 for c in calls.records),
        "seconds": round(time.perf_counter() - started, 1),
        "draft": draft.model_dump() if draft else None,
        "prompt_version": LLM_ONLY_VERSION if system == "llm_only" else PROMPT_VERSION,
    }  # fmt: skip
    if args.record and record["status"] == "ok":  # failed calls are retried; every real outcome is replayable
        recording_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"incident_id": entry["incident_id"], "model": model_name, "responses": model.records}
        recording_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return record


def calls_in_window(records: list[dict], now: datetime, hours: float = 24.0, key: int | None = None) -> int:
    """LLM calls made in the last hours hours (records without a timestamp are ignored); key filters to one API key."""
    cutoff = now - timedelta(hours=hours)
    return sum(
        r.get("llm_calls", 0)
        for r in records
        if r.get("at") and datetime.fromisoformat(r["at"]) > cutoff and (key is None or r.get("key_index", 0) == key)
    )


def daily_quota_exhausted(error: str | None) -> bool:
    """The key cannot serve this model today: daily quota spent, or the key's project has no access to the model."""
    if not error:
        return False
    return ("RESOURCE_EXHAUSTED" in error and "PerDay" in error) or any(
        marker in error for marker in ("NOT_FOUND", "PERMISSION_DENIED", "API_KEY_INVALID")
    )


# --- commands ---------------------------------------------------------------------------------------------------


def cmd_freeze(args) -> None:
    entries, missing = select_incidents(
        latest_ok(read_jsonl(DATA / "rq1.jsonl")), latest_ok(read_jsonl(DATA / "rq4.jsonl"))
    )
    SETS.mkdir(exist_ok=True)
    path = SETS / f"{args.set}.yaml"
    path.write_text(yaml.safe_dump({"set": args.set, "incidents": entries}, sort_keys=False), encoding="utf-8")
    print(f"{len(entries)} incidents ({Counter(e['kind'] for e in entries)}) -> {path}")
    if missing:
        print("no incident for:", ", ".join(missing))


def cmd_run(args) -> None:
    settings = get_settings()
    model_name = args.model or settings.llm_model
    settings = settings.model_copy(update={"llm_model": model_name})
    keys = settings.eval_api_keys()
    if args.system != "rules" and not (args.replay or keys):
        raise SystemExit("GOOGLE_API_KEY (or GOOGLE_API_KEYS) is not set")
    index = campaign_index()
    entries = [e for e in load_set(args.set) if args.only is None or e["row_id"] in args.only]
    out = DATA / "rq2" / f"{args.system}__{model_name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {r["row_id"] for r in read_jsonl(out) if r["status"] == "ok"}
    todo = [e for e in entries if args.force or e["row_id"] not in done]
    print(f"{args.system} / {model_name}: {len(todo)} to run, {len(done)} done")
    # the budget is a rolling 24-hour window over every system run on this model, so two runs in a day cannot add up
    # to more than the free-tier cap (conservative: Google resets at a calendar boundary)
    history = [r for path in (DATA / "rq2").glob(f"*__{model_name}.jsonl") for r in read_jsonl(path)]
    # the free-tier quota is per key (one Google Cloud project each): count and budget every key separately
    used = [calls_in_window(history, datetime.now(UTC), key=k) for k in range(max(len(keys), 1))]
    if args.budget is not None:
        print(f"{len(used)} key(s); calls in the last 24 h per key: {used}; budget {args.budget} each")
    for entry in todo:
        # least-used key first: spreads the 5 calls/min limit across keys too
        k = min(range(len(used)), key=used.__getitem__)
        if args.budget is not None and used[k] + EXPECTED_CALLS[args.system] > args.budget:
            print(f"stopping: every key would exceed {args.budget} calls in 24 h ({used} used)")
            break
        key_settings = settings.model_copy(update={"google_api_key": SecretStr(keys[k])}) if keys else settings
        record = evaluate(entry, args.system, model_name, args, index, key_settings)
        record["at"] = datetime.now(UTC).isoformat()
        record["key_index"] = k
        used[k] += record.get("llm_calls", 0)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        print(f"  {entry['row_id']:<46} {record['status']:<6} {record.get('category')!s:<15} correct={record.get('correct')} "
              f"src={record.get('source')} calls={record.get('llm_calls')} {record.get('error') or ''}"[:200], flush=True)  # fmt: skip
        if daily_quota_exhausted(record.get("error")):
            used[k] = 10**6  # this key is spent for today; carry on with the others
            if min(used) >= 10**6:
                print("daily quota exhausted on every key for this model; resume tomorrow")
                break


def cmd_summary(args) -> None:
    print(
        f"{'system':<10}{'model':<20}{'n':>4}{'top-1':>14}{'validated':>11}{'faithful':>10}{'calls':>7}{'tok in':>9}{'tok out':>9}"
    )
    for path in sorted((DATA / "rq2").glob("*.jsonl")):
        rows = list({r["row_id"]: r for r in read_jsonl(path) if r["status"] == "ok"}.values())
        if not rows:
            continue
        n, correct = len(rows), sum(bool(r["correct"]) for r in rows)
        lo, hi = wilson_interval(correct, n)
        validated = sum(r.get("source") == "llm" for r in rows)
        claims = sum(r.get("faith_claims") or 0 for r in rows)
        supported = sum(r.get("faith_supported") or 0 for r in rows)
        print(f"{rows[0]['system']:<10}{rows[0]['model']:<20}{n:>4}{f'{correct}/{n} [{lo:.2f},{hi:.2f}]':>14}"
              f"{(f'{validated}/{n}' if rows[0]['system'] != 'rules' else '-'):>11}"
              f"{(f'{supported / claims:.2f}' if claims else '-'):>10}"
              f"{sum(r.get('llm_calls', 0) for r in rows):>7}{sum(r.get('input_tokens', 0) for r in rows):>9}{sum(r.get('output_tokens', 0) for r in rows):>9}")  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--set", default="rq2")
    freeze.set_defaults(fn=cmd_freeze)
    run = sub.add_parser("run")
    run.add_argument("--system", choices=SYSTEMS, required=True)
    run.add_argument("--model", help="default: KUBESAGE_LLM_MODEL")
    run.add_argument("--set", default="rq2")
    run.add_argument("--only", nargs="+")
    run.add_argument("--budget", type=int, help="stop before exceeding this many LLM calls")
    run.add_argument("--record", action="store_true", help="save model responses for replay")
    run.add_argument("--replay", action="store_true", help="answer from recorded responses; no API calls")
    run.add_argument("--force", action="store_true", help="rerun incidents that already succeeded")
    run.set_defaults(fn=cmd_run)
    summary = sub.add_parser("summary")
    summary.set_defaults(fn=cmd_summary)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
