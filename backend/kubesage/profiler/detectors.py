"""Rule-based cold-start detectors.

Each detector attributes part of a startup's total time to one root-cause category. The score is the share
of total startup time that category explains, so scores are comparable across detectors. A category is only
reported when it explains at least `min_phase_seconds` and `min_phase_share` of the startup.
These are deterministic signals for the Incident Copilot to investigate, not a final diagnosis.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from kubesage.config import Settings
from kubesage.profiler.timeline import Timeline

MB = 1_000_000


@dataclass
class Candidate:
    category: str
    score: float
    seconds: float
    summary: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def detect(timeline: Timeline, settings: Settings) -> list[Candidate]:
    total = timeline.total_s
    if not total:
        return []
    found = []
    for detector in DETECTORS:
        result = detector(timeline.signals, total, settings)
        if result is None:
            continue
        category, seconds, summary, evidence = result
        share = seconds / total
        if seconds >= settings.min_phase_seconds and share >= settings.min_phase_share:
            found.append(Candidate(category, round(min(share, 1.0), 3), round(seconds, 3), summary, evidence))
    return sorted(found, key=lambda c: c.score, reverse=True)


DetectorResult = tuple[str, float, str, dict] | None


def _large_image(s: dict, total: float, cfg: Settings) -> DetectorResult:
    if not s["cold"] or not s["pull_s"] or not s["image_bytes"] or s["image_bytes"] < cfg.large_image_bytes:
        return None
    mb, rate = s["image_bytes"] / MB, s["throughput_bytes_per_s"] / MB
    return (
        "large_image",
        s["pull_s"],
        (
            f"Cold pull of a {mb:.0f} MB image took {s['pull_s']:.1f} s ({rate:.1f} MB/s), "
            f"{s['pull_s'] / total:.0%} of startup."
        ),
        {"image_bytes": s["image_bytes"], "pull_s": s["pull_s"], "throughput_bytes_per_s": s["throughput_bytes_per_s"]},
    )


def _slow_registry(s: dict, total: float, cfg: Settings) -> DetectorResult:
    rate = s["throughput_bytes_per_s"]
    if not s["cold"] or not s["pull_s"] or rate is None or rate >= cfg.slow_registry_bytes_per_s:
        return None
    return (
        "slow_registry",
        s["pull_s"],
        (
            f"Image pulled at only {rate / MB:.1f} MB/s (threshold {cfg.slow_registry_bytes_per_s / MB:.0f} MB/s); "
            f"the pull took {s['pull_s']:.1f} s for {s['image_bytes'] / MB:.0f} MB."
        ),
        {"image_bytes": s["image_bytes"], "pull_s": s["pull_s"], "throughput_bytes_per_s": rate},
    )


def _model_loading(s: dict, total: float, cfg: Settings) -> DetectorResult:
    if not s["model_s"]:
        return None
    spans = {k: v for k, v in s["span_seconds"].items() if k.startswith("model.")}
    return (
        "model_loading",
        s["model_s"],
        f"Model download and load took {s['model_s']:.1f} s ({', '.join(f'{k} {v:.1f} s' for k, v in spans.items())}).",
        {"spans": spans},
    )


def _app_init(s: dict, total: float, cfg: Settings) -> DetectorResult:
    if s["app_init_s"] is not None:
        other = s["app_init_s"] - s["model_s"] - s["dependency_s"]
        spans = {k: v for k, v in s["span_seconds"].items() if not k.startswith(("model.", "dependency."))}
        slowest = max(spans.items(), key=lambda kv: kv[1], default=None)
        detail = f"; slowest step {slowest[0]} {slowest[1]:.1f} s" if slowest else ""
        return (
            "app_init",
            other,
            f"Application initialisation (excluding model and dependency waits) took {other:.1f} s{detail}.",
            {"spans": spans, "cpu_limit": s["cpu_limit"]},
        )
    if s["app_init_and_readiness_s"] is not None:
        return (
            "app_init",
            s["app_init_and_readiness_s"],
            (
                f"Container start to Ready took {s['app_init_and_readiness_s']:.1f} s. The app emitted no startup "
                f"spans, so initialisation cannot be separated from readiness-probe delay."
            ),
            {"cpu_limit": s["cpu_limit"], "readiness_probe": s["readiness_probe"]},
        )
    return None


def _capacity(s: dict, total: float, cfg: Settings) -> DetectorResult:
    if not s["scheduling_s"] and not s["failed_scheduling_events"]:
        return None
    return (
        "capacity",
        s["scheduling_s"] or 0.0,
        (
            f"Pod waited {s['scheduling_s'] or 0:.1f} s to be scheduled "
            f"({s['failed_scheduling_events']} FailedScheduling event(s))."
        ),
        {"scheduling_s": s["scheduling_s"], "failed_scheduling_events": s["failed_scheduling_events"]},
    )


def _slow_readiness(s: dict, total: float, cfg: Settings) -> DetectorResult:
    readiness = s["readiness_s"] or 0.0
    dependency = s["dependency_s"]
    if not readiness and not dependency:
        return None
    parts = []
    if readiness:
        parts.append(f"Kubernetes marked the pod Ready {readiness:.1f} s after the app reported ready")
    if dependency:
        parts.append(f"the app waited {dependency:.1f} s for a dependency")
    probe = s["readiness_probe"] or {}
    probe_text = (
        f" (readiness probe initialDelaySeconds={probe.get('initialDelaySeconds')}, "
        f"periodSeconds={probe.get('periodSeconds')})"
        if probe
        else ""
    )
    sentence = "; ".join(parts)
    return (
        "slow_readiness",
        readiness + dependency,
        sentence[0].upper() + sentence[1:] + probe_text + ".",
        {
            "readiness_s": readiness,
            "dependency_s": dependency,
            "readiness_probe": s["readiness_probe"],
            "has_startup_probe": s["has_startup_probe"],
            "probe_failures_after_app_ready": s["probe_failures_after_app_ready"],
        },
    )


DETECTORS: list[Callable[[dict, float, Settings], DetectorResult]] = [
    _large_image,
    _slow_registry,
    _model_loading,
    _app_init,
    _capacity,
    _slow_readiness,
]
