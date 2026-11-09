"""Before/after comparison of experiment trials. Small samples: report every trial and medians, no significance."""

from statistics import median

MIN_DELTA_S = 2.0
MIN_DELTA_SHARE = 0.10


def _arm_summary(trials: list[dict]) -> dict:
    ok = [t for t in trials if t["status"] == "ok" and t["total_s"] is not None]
    phase_names = sorted({name for t in ok for name in t["phases"]})
    return {
        "trials": len(trials),
        "succeeded": len(ok),
        "failed": len(trials) - len(ok),
        "median_s": round(median(t["total_s"] for t in ok), 3) if ok else None,
        "min_s": min((t["total_s"] for t in ok), default=None),
        "max_s": max((t["total_s"] for t in ok), default=None),
        "phase_median_s": {name: round(median(t["phases"].get(name, 0.0) for t in ok), 3) for name in phase_names},
        "prep_median_s": round(median(p), 3)
        if (p := [t["prep_s"] for t in ok if t.get("prep_s") is not None])
        else None,
    }


def compare(trials: list[dict]) -> dict:
    """trials: {arm, warmup, status, total_s, phases, prep_s}. Warm-up trials are excluded.

    Verdict: 'failed' if any candidate trial failed its guardrails or an arm has no successful trial;
    otherwise 'improved' / 'regressed' when the median total moves by at least max(2 s, 10 % of baseline),
    else 'no_change'.
    """
    measured = [t for t in trials if not t["warmup"]]
    baseline = _arm_summary([t for t in measured if t["arm"] == "baseline"])
    candidate = _arm_summary([t for t in measured if t["arm"] == "candidate"])
    result = {"baseline": baseline, "candidate": candidate, "delta_s": None, "delta_pct": None, "threshold_s": None}

    if candidate["failed"]:
        return {**result, "verdict": "failed", "reason": f"candidate failed in {candidate['failed']} trial(s)"}
    if baseline["median_s"] is None or candidate["median_s"] is None:
        return {**result, "verdict": "failed", "reason": "an arm has no successful trial"}

    delta = round(baseline["median_s"] - candidate["median_s"], 3)
    threshold = round(max(MIN_DELTA_S, MIN_DELTA_SHARE * baseline["median_s"]), 3)
    result.update(delta_s=delta, delta_pct=round(100 * delta / baseline["median_s"], 1), threshold_s=threshold)
    if delta >= threshold:
        verdict, reason = "improved", f"median startup {delta:.1f} s faster (threshold {threshold:.1f} s)"
    elif -delta >= threshold:
        verdict, reason = "regressed", f"median startup {-delta:.1f} s slower (threshold {threshold:.1f} s)"
    else:
        verdict, reason = "no_change", f"median difference {delta:+.1f} s is within {threshold:.1f} s"
    return {**result, "verdict": verdict, "reason": reason}
