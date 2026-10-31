"""Reads app startup spans from Jaeger v2's stable query API (/api/v3, OTLP JSON)."""

from datetime import UTC, datetime, timedelta

import httpx

from kubesage.profiler.timeline import Span


def _attribute_value(value: dict):
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        return int(value["intValue"])  # OTLP JSON encodes int64 as a string
    return None


def _attributes(items: list[dict]) -> dict:
    return {a["key"]: _attribute_value(a.get("value", {})) for a in items or []}


def _from_unix_nano(value: str | int) -> datetime:
    nanos = int(value)
    return datetime.fromtimestamp(nanos // 1_000_000_000, tz=UTC) + timedelta(
        microseconds=(nanos % 1_000_000_000) // 1000
    )


def spans_from_otlp(payload: dict, pod_name: str) -> list[Span]:
    """Spans whose resource has k8s.pod.name == pod_name."""
    spans = []
    for resource_spans in (payload.get("result") or {}).get("resourceSpans", []):
        resource = _attributes(resource_spans.get("resource", {}).get("attributes", []))
        if resource.get("k8s.pod.name") != pod_name:
            continue
        for scope in resource_spans.get("scopeSpans", []):
            for span in scope.get("spans", []):
                spans.append(
                    Span(
                        name=span["name"],
                        start=_from_unix_nano(span["startTimeUnixNano"]),
                        end=_from_unix_nano(span["endTimeUnixNano"]),
                        attributes=_attributes(span.get("attributes", [])),
                    )
                )
    return spans


class JaegerClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def startup_spans(self, service: str, pod_name: str, start: datetime, end: datetime) -> list[Span]:
        response = self._client.get(
            "/api/v3/traces",
            params={
                "query.service_name": service,
                "query.start_time_min": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "query.start_time_max": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            },
        )
        if response.status_code == 404:  # Jaeger returns 404 when no traces match
            return []
        response.raise_for_status()
        return spans_from_otlp(response.json(), pod_name)

    def ping(self) -> bool:
        try:
            return self._client.get("/api/v3/services").status_code == 200
        except httpx.HTTPError:
            return False
