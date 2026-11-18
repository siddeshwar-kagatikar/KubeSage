"""Detector threshold sensitivity (RQ1), offline: re-run the rule-based detectors over the stored startups of a
campaign while sweeping one threshold at a time. No cluster and no LLM; the same code path as production
(`kubesage.profiler.detectors.detect`).

    python -m evals.sensitivity            # writes evals/data/sensitivity.csv

For each value of each swept setting it reports, over the campaign rows:
  top1       share of single/compound rows whose top candidate is an acceptable answer
  recall     share of injected causes present among the candidates
  fp_rate    share of control rows (nothing injected) that produce any candidate
"""

import csv
import json
from pathlib import Path
from types import SimpleNamespace

from kubesage.config import Settings
from kubesage.profiler.detectors import detect

HERE = Path(__file__).parent
DATA = HERE / "data"

SWEEPS: dict[str, list[float]] = {
    "min_phase_share": [0.05, 0.10, 0.15, 0.25, 0.35, 0.50, 0.65],
    "min_phase_seconds": [1, 2, 3, 5, 8, 12, 20],
    "large_image_bytes": [100e6, 200e6, 300e6, 500e6, 650e6, 800e6],
    "slow_registry_bytes_per_s": [1e6, 2e6, 3e6, 5e6, 8e6, 12e6, 20e6],
}
DEFAULTS = Settings(_env_file=None)


def candidates_for(record: dict, settings: Settings) -> list[str]:
    """Categories the detectors report for a stored startup, best first."""
    signals = record["signals"]
    timeline = SimpleNamespace(signals=signals, total_s=signals.get("total_s"))
    return [c.category for c in detect(timeline, settings)]


def score(results: list[dict], settings: Settings) -> dict:
    top1_hits = top1_n = injected = recalled = controls = false_alarms = 0
    for result in results:
        if result["status"] != "ok":
            continue
        expect = result["row"]["expect"]
        found = candidates_for(result["record"], settings)
        if expect["injected"]:
            top1_n += 1
            top1_hits += bool(found) and found[0] in expect["acceptable"]
            injected += len(expect["injected"])
            recalled += sum(c in found for c in expect["injected"])
        else:
            controls += 1
            false_alarms += bool(found)
    return {
        "top1": top1_hits / top1_n if top1_n else None,
        "recall": recalled / injected if injected else None,
        "fp_rate": false_alarms / controls if controls else None,
        "rows": top1_n + controls,
    }


def sweep(results: list[dict], parameter: str, values: list[float]) -> list[dict]:
    rows = []
    for value in values:
        settings = DEFAULTS.model_copy(update={parameter: type(getattr(DEFAULTS, parameter))(value)})
        rows.append({"parameter": parameter, "value": value, "default": getattr(DEFAULTS, parameter) == value}
                    | score(results, settings))  # fmt: skip
    return rows


def run(results: list[dict]) -> list[dict]:
    return [row for parameter, values in SWEEPS.items() for row in sweep(results, parameter, values)]


def main() -> None:
    path = DATA / "rq1.jsonl"
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            result = json.loads(line)
            latest[result["row"]["id"]] = result  # a later attempt of a row replaces an earlier one
    rows = run(list(latest.values()))
    out = DATA / "sensitivity.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["parameter", "value", "default", "top1", "recall", "fp_rate", "rows"])
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        mark = "*" if row["default"] else " "
        print(
            f"{mark} {row['parameter']:<28}{row['value']:>14g}  top1={row['top1']:.2f} recall={row['recall']:.2f} fp={row['fp_rate']:.2f}"
        )
    print(f"-> {out}")


if __name__ == "__main__":
    main()
