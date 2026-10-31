"""Prometheus range queries for a pod's startup window. Only fixed, templated queries are exposed to the Copilot."""

from datetime import UTC, datetime

import httpx

STEP_SECONDS = 5

# {ns} and {pod} come from the incident context, never from the model.
TEMPLATES = {
    "cpu_throttling_ratio": (
        'sum(rate(container_cpu_cfs_throttled_periods_total{{namespace="{ns}",pod="{pod}",container!=""}}[30s]))'
        ' / clamp_min(sum(rate(container_cpu_cfs_periods_total{{namespace="{ns}",pod="{pod}",container!=""}}[30s])), 1e-9)'
    ),
    "cpu_usage_cores": 'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}",pod="{pod}",container!=""}}[30s]))',
    "memory_working_set_bytes": 'sum(container_memory_working_set_bytes{{namespace="{ns}",pod="{pod}",container!=""}})',
}


class PrometheusClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def pod_metric(self, metric: str, namespace: str, pod: str, start: datetime, end: datetime) -> dict:
        """Summary (samples, min, max, mean) of a templated metric over [start, end]."""
        query = TEMPLATES[metric].format(ns=namespace, pod=pod)
        response = self._client.get(
            "/api/v1/query_range",
            params={
                "query": query,
                "start": start.astimezone(UTC).timestamp(),
                "end": end.astimezone(UTC).timestamp(),
                "step": STEP_SECONDS,
            },
        )
        response.raise_for_status()
        series = response.json()["data"]["result"]
        values = [float(v) for s in series for _, v in s["values"] if v not in ("NaN", "+Inf", "-Inf")]
        if not values:
            return {"metric": metric, "samples": 0, "note": "no data in the startup window"}
        return {
            "metric": metric,
            "samples": len(values),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "mean": round(sum(values) / len(values), 4),
        }
