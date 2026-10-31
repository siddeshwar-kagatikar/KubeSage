"""Incident rule: a startup breaches its workload SLO, or regresses against the workload's own baseline."""

from dataclasses import dataclass
from statistics import median

from kubesage.config import Settings


@dataclass(frozen=True)
class Breach:
    reason: str  # slo | regression
    threshold_s: float
    baseline_s: float | None


def evaluate(total_s: float, slo_s: float, previous_totals: list[float], settings: Settings) -> Breach | None:
    """`previous_totals` should be earlier startups of the same workload with the same cold/warm state."""
    window = previous_totals[: settings.baseline_window]
    baseline = round(median(window), 3) if len(window) >= settings.baseline_min_samples else None
    if total_s > slo_s:
        return Breach("slo", slo_s, baseline)
    if baseline is not None and total_s > baseline * settings.regression_factor:
        return Breach("regression", round(baseline * settings.regression_factor, 3), baseline)
    return None
