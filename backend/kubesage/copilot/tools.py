"""Read-only investigation tools for one incident.

Tools are closures over the incident context, so the model can only inspect this incident's pod, workload and node.
Each call stores its result as evidence and returns it to the model with its reference (E1, E2, ...).
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from langchain_core.tools import BaseTool, tool

from kubesage.copilot.context import IncidentContext
from kubesage.copilot.evidence import EvidenceStore, render
from kubesage.llm.redact import redact_text

RUNBOOK_DIR = Path(__file__).resolve().parents[3] / "runbooks"
METRIC_WINDOW_PADDING = timedelta(seconds=30)

Category = Literal["large_image", "slow_registry", "model_loading", "app_init", "capacity", "slow_readiness"]
Metric = Literal["cpu_throttling_ratio", "cpu_usage_cores", "memory_working_set_bytes"]


def _offset(start: str, origin: datetime) -> float:
    return round((datetime.fromisoformat(start) - origin).total_seconds(), 3)


def timeline_result(ctx: IncidentContext) -> dict:
    origin = datetime.fromisoformat(ctx.startup["created_at"])
    signals = ctx.startup["signals"]
    return {
        "pod": ctx.startup["pod_name"],
        "node": ctx.startup["node"],
        "image": ctx.startup["image"],
        "cold_start": ctx.startup["cold"],
        "total_s": ctx.startup["total_s"],
        "slo_s": ctx.workload["slo_seconds"],
        "phases": [
            {
                "name": p["name"],
                "parent": p["parent"],
                "starts_at_s": _offset(p["start"], origin),
                "duration_s": p["duration_s"],
                "source": p["source"],
                **({"attributes": p["attributes"]} if p.get("attributes") else {}),
            }
            for p in ctx.startup["phases"]
        ],
        "signals": {k: v for k, v in signals.items() if k != "span_seconds"},
    }


def detector_result(ctx: IncidentContext) -> dict:
    return {
        "breach": {k: ctx.incident[k] for k in ("total_s", "threshold_s", "breach_reason", "baseline_s")},
        "candidates": ctx.incident["candidates"],
    }


def build_tools(
    ctx: IncidentContext,
    evidence: EvidenceStore,
    metric_fn: Callable[[str, str, str, datetime, datetime], dict] | None = None,
    node_fn: Callable[[str], dict] | None = None,
) -> list[BaseTool]:
    def record(name: str, args: dict, result: dict) -> str:
        ref = evidence.add(name, args, result)
        return f"evidence_id: {ref}\n{render(result)}"

    @tool
    def get_pod_events(reason: str | None = None) -> str:
        """Kubernetes events recorded for the pod (scheduling, image pulls, probe failures).
        Optionally filter by event reason, e.g. 'Pulled', 'Unhealthy', 'FailedScheduling'."""
        events = [
            {**e, "message": redact_text(e["message"])} for e in ctx.events if reason is None or e["reason"] == reason
        ]
        return record("get_pod_events", {"reason": reason}, {"count": len(events), "events": events})

    @tool
    def get_pod_spec() -> str:
        """The pod's container image, pull policy, resources, probes, env (secrets redacted), volumes and node
        placement, as captured when the pod became Ready."""
        if not ctx.pod_snapshot:
            return record("get_pod_spec", {}, {"error": "pod spec was not captured for this startup"})
        spec = ctx.pod_snapshot["spec"]
        containers = [
            {
                k: c.get(k)
                for k in (
                    "name",
                    "image",
                    "imagePullPolicy",
                    "resources",
                    "readinessProbe",
                    "startupProbe",
                    "livenessProbe",
                    "env",
                    "volumeMounts",
                )
                if c.get(k) is not None
            }
            for c in spec.get("containers", [])
        ]
        result = {
            "containers": containers,
            "init_containers": [{"name": c["name"], "image": c["image"]} for c in spec.get("initContainers", [])],
            "volumes": [{k: v for k, v in vol.items() if k != "projected"} for vol in spec.get("volumes", [])],
            "node_selector": spec.get("nodeSelector"),
            "node_name": spec.get("nodeName"),
        }
        return record("get_pod_spec", {}, result)

    @tool
    def get_container_logs(contains: str | None = None, tail: int = 60) -> str:
        """Last lines of the app container's log (secrets redacted). The demo app logs JSON lines with startup
        phases ('phase_start', 'phase_end', 'startup_complete'). Optionally keep only lines containing a substring."""
        if not ctx.logs:
            return record(
                "get_container_logs", {"contains": contains, "tail": tail}, {"error": "logs were not captured"}
            )
        # Logs are redacted at capture; redact again in case of older rows or new patterns.
        lines = [redact_text(line) for line in ctx.logs.splitlines() if contains is None or contains in line]
        tail = max(1, min(tail, 200))
        return record("get_container_logs", {"contains": contains, "tail": tail}, {"lines": lines[-tail:]})

    @tool
    def get_workload_history() -> str:
        """Earlier startups of the same workload (total time, cold/warm, detected category) for comparison."""
        return record("get_workload_history", {}, {"startups": ctx.history})

    @tool
    def query_metric(metric: Metric) -> str:
        """A container metric for this pod over its startup window, from Prometheus (cAdvisor):
        'cpu_throttling_ratio' (share of CFS periods throttled), 'cpu_usage_cores', 'memory_working_set_bytes'."""
        if metric_fn is None:
            return record("query_metric", {"metric": metric}, {"error": "metrics are not available"})
        start = datetime.fromisoformat(ctx.startup["created_at"])
        end = datetime.fromisoformat(ctx.startup["ready_at"] or ctx.startup["created_at"]) + METRIC_WINDOW_PADDING
        try:
            result = metric_fn(metric, ctx.startup["namespace"], ctx.startup["pod_name"], start, end)
        except Exception as exc:
            result = {"error": f"query failed: {exc}"}
        return record("query_metric", {"metric": metric}, result)

    @tool
    def get_node_capacity() -> str:
        """Allocatable vs requested CPU and memory on the node the pod ran on (current state, not historical)."""
        if node_fn is None or not ctx.startup["node"]:
            return record("get_node_capacity", {}, {"error": "node capacity is not available"})
        try:
            result = node_fn(ctx.startup["node"])
        except Exception as exc:
            result = {"error": f"lookup failed: {exc}"}
        return record("get_node_capacity", {}, result)

    @tool
    def get_runbook(category: Category) -> str:
        """The operations runbook for a cold-start category: symptoms, evidence to check and approved remediations."""
        path = RUNBOOK_DIR / f"{category}.md"
        if not path.exists():
            return record("get_runbook", {"category": category}, {"error": "no runbook for this category"})
        return record("get_runbook", {"category": category}, {"runbook": path.read_text(encoding="utf-8")})

    @tool
    def search_past_incidents(category: Category) -> str:
        """Previously diagnosed incidents of a category (any workload), with their summaries and recommendations."""
        matches = [p for p in ctx.past_incidents if p["category"] == category]
        return record("search_past_incidents", {"category": category}, {"incidents": matches})

    return [
        get_pod_events,
        get_pod_spec,
        get_container_logs,
        get_workload_history,
        query_metric,
        get_node_capacity,
        get_runbook,
        search_past_incidents,
    ]
