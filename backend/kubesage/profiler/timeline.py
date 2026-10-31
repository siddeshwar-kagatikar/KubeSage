"""Builds a pod startup timeline from Kubernetes objects and OpenTelemetry spans.

Inputs are plain dicts in Kubernetes API form (camelCase), as produced by `kubectl get -o json` or
`ApiClient.sanitize_for_serialization`, so live data and recorded fixtures share one code path.

Timing sources, most to least precise:
  - app spans (ns, process start from /proc),
  - the kubelet `Pulled` message duration (ms),
  - pod conditions, container states and event timestamps (truncated to whole seconds).
Phases are built so that 1 s truncation never produces negative durations.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

PULLED_RE = re.compile(
    r'Successfully pulled image "(?P<image>[^"]+)" in (?P<duration>(?:[0-9.]+(?:h|ms|us|µs|ns|m|s))+)'
)
IMAGE_SIZE_RE = re.compile(r"Image size: (?P<bytes>\d+) bytes")
ALREADY_PRESENT = "already present on machine"
GO_DURATION_PART_RE = re.compile(r"([0-9.]+)(h|ms|us|µs|ns|m|s)")
GO_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "ns": 1e-9}

MODEL_SPANS = ("model.download", "model.load")
DEPENDENCY_SPANS = ("dependency.connect",)


@dataclass(frozen=True)
class Span:
    name: str
    start: datetime
    end: datetime
    attributes: dict = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass
class Phase:
    name: str
    start: datetime
    end: datetime
    source: str  # k8s | event | span
    parent: str | None = None
    attributes: dict = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return round((self.end - self.start).total_seconds(), 3)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_s": self.duration_s,
            "source": self.source,
            "parent": self.parent,
            "attributes": self.attributes,
        }


@dataclass
class Timeline:
    pod_name: str
    pod_uid: str
    namespace: str
    node: str | None
    image: str
    created_at: datetime
    ready_at: datetime | None
    phases: list[Phase]
    signals: dict
    spans_found: bool

    @property
    def total_s(self) -> float | None:
        return self.signals.get("total_s")

    @property
    def cold(self) -> bool | None:
        return self.signals.get("cold")

    def phase(self, name: str) -> Phase | None:
        return next((p for p in self.phases if p.name == name), None)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def parse_go_duration(text: str) -> float:
    """'56.239s', '850ms', '1m2.5s' -> seconds."""
    parts = GO_DURATION_PART_RE.findall(text)
    if not parts:
        raise ValueError(f"not a Go duration: {text!r}")
    return sum(float(value) * GO_UNIT_SECONDS[unit] for value, unit in parts)


def event_time(event: dict) -> datetime | None:
    return parse_time(
        event.get("eventTime") or event.get("firstTimestamp") or event["metadata"].get("creationTimestamp")
    )


def event_last_time(event: dict) -> datetime | None:
    series = event.get("series") or {}
    return parse_time(event.get("lastTimestamp") or series.get("lastObservedTime")) or event_time(event)


def build_timeline(pod: dict, events: list[dict], spans: list[Span]) -> Timeline:
    meta, spec, status = pod["metadata"], pod["spec"], pod.get("status", {})
    uid = meta["uid"]
    created = parse_time(meta["creationTimestamp"])
    conditions = {
        c["type"]: parse_time(c.get("lastTransitionTime"))
        for c in status.get("conditions", [])
        if c.get("status") == "True"
    }
    scheduled = conditions.get("PodScheduled")
    sandbox_ready = conditions.get("PodReadyToStartContainers")
    ready = conditions.get("Ready")

    container = spec["containers"][0]
    image = container["image"]
    container_status = next((s for s in status.get("containerStatuses", []) if s["name"] == container["name"]), {})
    started = parse_time(container_status.get("state", {}).get("running", {}).get("startedAt"))

    pod_events = [e for e in events if e.get("involvedObject", {}).get("uid") in (uid, None)]
    cold, pull_s, image_bytes, pull_start = _image_pull(pod_events, image)

    phases: list[Phase] = []

    def add(name: str, start: datetime | None, end: datetime | None, source: str, parent: str | None = None, **attrs):
        if start is None or end is None:
            return None
        phase = Phase(name, start, max(start, end), source, parent, {k: v for k, v in attrs.items() if v is not None})
        phases.append(phase)
        return phase

    add("scheduling", created, scheduled, "k8s")
    add("pod_sandbox", scheduled, sandbox_ready, "k8s")
    cursor = sandbox_ready or scheduled or created

    init_window = _init_container_window(status)
    if init_window:
        add("init_containers", init_window[0], init_window[1], "k8s")
        cursor = max(cursor, init_window[1])

    if cold and pull_s is not None:
        # The Pulling event is truncated to the second; never start the pull before the previous phase.
        pull_begin = max(pull_start, cursor) if pull_start else cursor
        pull = add("image_pull", pull_begin, pull_begin + timedelta(seconds=pull_s), "event", bytes=image_bytes)
        cursor = pull.end

    startup_span = _latest_startup_span(spans, ready)
    app_start = startup_span.start if startup_span else started
    span_clock_skew_s = 0.0
    if startup_span and app_start < cursor:
        # The app derives its process start from /proc (btime has 1 s resolution), so it can appear to start
        # before the image pull finished. That is impossible; clamp it and record the skew.
        span_clock_skew_s = round((cursor - app_start).total_seconds(), 3)
        app_start = cursor
    add("container_start", cursor, app_start, "k8s")

    app_ready = None
    if startup_span:
        app_ready = startup_span.end
        add("app_init", app_start, app_ready, "span")
        for span in sorted(spans, key=lambda s: s.start):
            if span is not startup_span and startup_span.start <= span.start <= app_ready:
                add(
                    span.name,
                    max(span.start, app_start),
                    max(span.end, app_start),
                    "span",
                    parent="app_init",
                    **_scalar_attributes(span.attributes),
                )
        add("readiness", app_ready, ready, "k8s")
    else:
        add("app_init_and_readiness", app_start, ready, "k8s")

    signals = _signals(
        phases=phases,
        created=created,
        ready=ready,
        cold=cold,
        pull_s=pull_s,
        image_bytes=image_bytes,
        app_ready=app_ready,
        container=container,
        container_status=container_status,
        pod_events=pod_events,
    )
    signals["span_clock_skew_s"] = span_clock_skew_s
    return Timeline(
        pod_name=meta["name"],
        pod_uid=uid,
        namespace=meta["namespace"],
        node=spec.get("nodeName"),
        image=image,
        created_at=created,
        ready_at=ready,
        phases=phases,
        signals=signals,
        spans_found=startup_span is not None,
    )


def _image_pull(events: list[dict], image: str) -> tuple[bool | None, float | None, int | None, datetime | None]:
    """(cold, pull seconds, compressed image bytes, pull start) for the main container image."""
    cold = pull_s = image_bytes = pull_start = None
    for event in events:
        message = event.get("message") or ""
        if event.get("reason") != "Pulled" or image not in message:
            continue
        if ALREADY_PRESENT in message:
            cold = False
        elif match := PULLED_RE.search(message):
            cold = True
            pull_s = parse_go_duration(match["duration"])
            if size := IMAGE_SIZE_RE.search(message):
                image_bytes = int(size["bytes"])
    if cold:
        pulling = [e for e in events if e.get("reason") == "Pulling" and image in (e.get("message") or "")]
        pull_start = min((event_time(e) for e in pulling), default=None)
    return cold, pull_s, image_bytes, pull_start


def _init_container_window(status: dict) -> tuple[datetime, datetime] | None:
    windows = []
    for s in status.get("initContainerStatuses", []):
        terminated = s.get("state", {}).get("terminated") or s.get("lastState", {}).get("terminated")
        if terminated and terminated.get("startedAt") and terminated.get("finishedAt"):
            windows.append((parse_time(terminated["startedAt"]), parse_time(terminated["finishedAt"])))
    if not windows:
        return None
    return min(w[0] for w in windows), max(w[1] for w in windows)


def _latest_startup_span(spans: list[Span], ready: datetime | None) -> Span | None:
    """The startup span of the container start that produced Ready (the latest one, if it restarted)."""
    candidates = [s for s in spans if s.name == "startup"]
    if ready:
        candidates = [s for s in candidates if s.start <= ready] or candidates
    return max(candidates, key=lambda s: s.start, default=None)


def _scalar_attributes(attributes: dict) -> dict:
    return {k: v for k, v in attributes.items() if isinstance(v, (str, int, float, bool)) and k != "phase"}


def _signals(
    *,
    phases: list[Phase],
    created: datetime,
    ready: datetime | None,
    cold: bool | None,
    pull_s: float | None,
    image_bytes: int | None,
    app_ready: datetime | None,
    container: dict,
    container_status: dict,
    pod_events: list[dict],
) -> dict:
    by_name = {p.name: p for p in phases if p.parent is None}
    span_seconds = {p.name: p.duration_s for p in phases if p.parent == "app_init"}

    def seconds(name: str) -> float | None:
        return by_name[name].duration_s if name in by_name else None

    unhealthy = [e for e in pod_events if e.get("reason") == "Unhealthy"]
    probe_failures_after_ready = sum(
        e.get("count", 1) for e in unhealthy if app_ready and (event_last_time(e) or created) >= app_ready
    )
    readiness_probe = container.get("readinessProbe")
    probe_config = (
        {
            k: readiness_probe.get(k)
            for k in ("initialDelaySeconds", "periodSeconds", "timeoutSeconds", "failureThreshold")
        }
        if readiness_probe
        else None
    )

    return {
        "total_s": round((ready - created).total_seconds(), 3) if ready else None,
        "cold": cold,
        "pull_s": pull_s,
        "image_bytes": image_bytes,
        "throughput_bytes_per_s": round(image_bytes / pull_s) if image_bytes and pull_s else None,
        "scheduling_s": seconds("scheduling"),
        "init_containers_s": seconds("init_containers"),
        "app_init_s": seconds("app_init"),
        "readiness_s": seconds("readiness"),
        "app_init_and_readiness_s": seconds("app_init_and_readiness"),
        "model_s": round(sum(span_seconds.get(n, 0.0) for n in MODEL_SPANS), 3),
        "dependency_s": round(sum(span_seconds.get(n, 0.0) for n in DEPENDENCY_SPANS), 3),
        "span_seconds": span_seconds,
        "failed_scheduling_events": sum(e.get("count", 1) for e in pod_events if e.get("reason") == "FailedScheduling"),
        "probe_failures_after_app_ready": probe_failures_after_ready,
        "readiness_probe": probe_config,
        "has_startup_probe": "startupProbe" in container,
        "cpu_limit": container.get("resources", {}).get("limits", {}).get("cpu"),
        "restarts": container_status.get("restartCount", 0),
    }


def median_or_none(values: list[float]) -> float | None:
    return round(median(values), 3) if values else None
