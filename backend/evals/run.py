"""Evaluate the Incident Copilot against ground-truth scenarios.

Run from backend/ (needs Postgres with the scenario incidents; live/record also need GOOGLE_API_KEY):

    python -m evals.run                  # live: real model, nothing saved
    python -m evals.run --mode record    # live, and save model responses to evals/recordings/
    python -m evals.run --mode replay    # replay saved responses: no API calls, deterministic

Scores: category accuracy, whether the LLM diagnosis passed grounding validation (vs fallback), citation validity,
tool calls, tokens and latency. Results are written to evals/results/.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import select

from kubesage.collectors.kube import new_api_client, node_capacity
from kubesage.collectors.prometheus import PrometheusClient
from kubesage.config import get_settings
from kubesage.copilot.context import load_context
from kubesage.copilot.evidence import EvidenceStore
from kubesage.copilot.graph import run_copilot
from kubesage.copilot.prompts import PROMPT_VERSION
from kubesage.copilot.tools import build_tools
from kubesage.copilot.validation import validate
from kubesage.db.models import Incident, Workload
from kubesage.db.session import session_scope
from kubesage.llm.calls import CallLogger
from kubesage.llm.provider import create_chat_model
from kubesage.llm.replay import RecordingModel, ScriptedModel

HERE = Path(__file__).parent
RECORDINGS = HERE / "recordings"
RESULTS = HERE / "results"


def latest_incident_id(namespace: str, workload: str) -> int | None:
    with session_scope() as session:
        return session.scalar(
            select(Incident.id)
            .join(Workload)
            .where(Workload.namespace == namespace, Workload.name == workload)
            .order_by(Incident.detected_at.desc())
            .limit(1)
        )


def evaluate_scenario(scenario: dict, mode: str, settings) -> dict:
    recording_path = RECORDINGS / f"{scenario['name']}.json"
    if mode == "replay":
        recording = json.loads(recording_path.read_text(encoding="utf-8"))
        incident_id = recording["incident_id"]
        model = ScriptedModel(recording["responses"])
    else:
        incident_id = latest_incident_id(scenario["namespace"], scenario["workload"])
        model = create_chat_model(settings)
        if mode == "record":
            model = RecordingModel(model)
    if incident_id is None:
        return {"scenario": scenario["name"], "error": "no incident found; run the scenario cold first"}

    with session_scope() as session:
        ctx = load_context(session, incident_id)
    evidence = EvidenceStore()
    api_client = new_api_client(settings.kube_context)
    tools = build_tools(
        ctx,
        evidence,
        metric_fn=PrometheusClient(settings.prometheus_url).pod_metric,
        node_fn=lambda node: node_capacity(api_client, node),
    )
    calls = CallLogger(None, settings.llm_provider, settings.llm_model, PROMPT_VERSION, persist=False)
    started = time.perf_counter()
    result = run_copilot(ctx, model, tools, evidence, calls, settings)
    elapsed = time.perf_counter() - started

    # Only a validated LLM run is worth replaying; never overwrite a good recording with a failed one.
    if mode == "record" and result.source == "llm":
        RECORDINGS.mkdir(exist_ok=True)
        recording_path.write_text(
            json.dumps({"incident_id": incident_id, "model": settings.llm_model, "responses": model.records}, indent=1),
            encoding="utf-8",
        )

    citation_errors = [e for e in validate(result.draft, evidence.refs, None) if "evidence" in e]
    return {
        "scenario": scenario["name"],
        "incident_id": incident_id,
        "expected": scenario["expected"],
        "category": result.draft.category,
        "correct": result.draft.category == scenario["expected"],
        "source": result.source,
        "confidence": result.draft.confidence,
        "citations_valid": not citation_errors,
        "validation_errors": result.validation_errors,
        "llm_error": result.llm_error,
        "tool_calls": result.tool_calls,
        "tools_used": [i.tool for i in evidence.items[2:]],
        "llm_calls": len(calls.records),
        "input_tokens": sum(c["input_tokens"] or 0 for c in calls.records),
        "output_tokens": sum(c["output_tokens"] or 0 for c in calls.records),
        "seconds": round(elapsed, 1),
        "recorded": mode == "record" and result.source == "llm",
        "summary": result.draft.summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["live", "record", "replay"], default="live")
    parser.add_argument("--scenario", action="append", help="limit to these scenario names")
    args = parser.parse_args()

    settings = get_settings()
    scenarios = yaml.safe_load((HERE / "scenarios.yaml").read_text(encoding="utf-8"))["scenarios"]
    if args.scenario:
        scenarios = [s for s in scenarios if s["name"] in args.scenario]

    rows = [evaluate_scenario(s, args.mode, settings) for s in scenarios]
    scored = [r for r in rows if "error" not in r]
    print(f"\nCopilot eval ({args.mode}, {settings.llm_model}, {PROMPT_VERSION})\n")
    print(
        f"{'scenario':<22}{'expected':<16}{'got':<16}{'ok':<5}{'source':<10}{'cites':<7}{'tools':<7}{'tokens':<14}{'s':>6}"
    )
    for r in rows:
        if "error" in r:
            print(f"{r['scenario']:<22}{r['error']}")
            continue
        tokens = f"{r['input_tokens']}/{r['output_tokens']}"
        print(
            f"{r['scenario']:<22}{r['expected']:<16}{r['category']:<16}{'yes' if r['correct'] else 'NO':<5}"
            f"{r['source']:<10}{'ok' if r['citations_valid'] else 'BAD':<7}{r['tool_calls']:<7}{tokens:<14}{r['seconds']:>6}"
        )
    if scored:
        accuracy = sum(r["correct"] for r in scored) / len(scored)
        grounded = sum(r["source"] == "llm" for r in scored) / len(scored)
        print(
            f"\naccuracy {accuracy:.0%}   validated LLM diagnoses {grounded:.0%}   scenarios {len(scored)}/{len(rows)}"
        )

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"{stamp}-{args.mode}.json"
    out.write_text(
        json.dumps(
            {"mode": args.mode, "model": settings.llm_model, "prompt_version": PROMPT_VERSION, "results": rows},
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"results: {out}")


if __name__ == "__main__":
    main()
