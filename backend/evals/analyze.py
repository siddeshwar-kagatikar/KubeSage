"""Turn the raw evaluation data into the paper's tables (CSV + LaTeX) and figures (PDF).

    python -m evals.analyze                 # every part that has data
    python -m evals.analyze rq1 rq2 rq3     # selected parts: rq1 rq2 rq3 rq4 rq5 sensitivity

Reads evals/data/*.jsonl|json and writes paper/tables and paper/figures. Every number in the paper comes from here.
A part without data is skipped with a message, so the script can run at any point of the study.
"""

import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from evals.expectations import CATEGORIES
from evals.stats import (
    bootstrap_median_diff,
    confusion_matrix,
    mann_whitney_p,
    mcnemar_exact,
    quartiles,
    wilson_interval,
)
from kubesage.config import REPO_ROOT

DATA = Path(__file__).parent / "data"
TABLES = REPO_ROOT / "paper" / "tables"
FIGURES = REPO_ROOT / "paper" / "figures"
LABELS = [*CATEGORIES, "none"]


# --- helpers ----------------------------------------------------------------------------------------------------


@dataclass
class Table:
    name: str
    caption: str
    header: list[str]
    rows: list[list]


def read_jsonl(path: Path) -> list[dict]:
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if path.exists()
        else []
    )


def latest_ok(results: list[dict], key=lambda r: r["row"]["id"]) -> list[dict]:
    """Newest successful result per row (a later attempt replaces an earlier one)."""
    return list({key(r): r for r in results if r["status"] == "ok"}.values())


def ci(k: int, n: int) -> str:
    if n == 0:
        return "-"
    lo, hi = wilson_interval(k, n)
    return f"{k}/{n} ({lo:.2f}-{hi:.2f})"


def ratio(k: int, n: int) -> str:
    return f"{k}/{n}" if n else "-"


def num(value, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def tex_escape(text) -> str:
    return (
        str(text)
        .replace("\\", "\\textbackslash{}")
        .replace("_", "\\_")
        .replace("%", "\\%")
        .replace("&", "\\&")
        .replace("#", "\\#")
    )


def save(table: Table) -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    with (TABLES / f"{table.name}.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(table.header)
        writer.writerows(table.rows)
    body = " \\\\\n".join(" & ".join(tex_escape(c) for c in row) for row in table.rows)
    spec = "l" + "r" * (len(table.header) - 1)
    (TABLES / f"{table.name}.tex").write_text(
        f"\\begin{{table}}[t]\n\\centering\\small\n\\caption{{{tex_escape(table.caption)}}}\\label{{tab:{table.name}}}\n"
        f"\\begin{{tabular}}{{{spec}}}\n\\toprule\n{' & '.join(tex_escape(h) for h in table.header)} \\\\\n\\midrule\n{body} \\\\\n"
        "\\bottomrule\n\\end{tabular}\n\\end{table}\n",
        encoding="utf-8",
    )
    print(f"  table  {table.name} ({len(table.rows)} rows)")


def save_figure(fig, name: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIGURES / f"{name}.pdf")
    fig.savefig(FIGURES / f"{name}.png", dpi=160)
    plt.close(fig)
    print(f"  figure {name}")


# --- RQ1: profiling fidelity and detection accuracy -------------------------------------------------------------


def rq1_detection(results: list[dict]) -> Table:
    groups: dict[str, list[dict]] = {}
    for r in results:
        row = r["row"]
        label = {"compound": "compound (all)", "control": "control (all)"}.get(row["kind"], row["fault"])
        groups.setdefault(label, []).append(r)
    rows = []
    for label, items in sorted(groups.items(), key=lambda kv: (kv[0].endswith("(all)"), kv[0])):
        o = [i["outcome"] for i in items]
        scored = [x["top1_correct"] for x in o if x["top1_correct"] is not None]
        injected = sum(len(i["row"]["expect"]["injected"]) for i in items)
        recalled = sum(len(x["injected_recalled"]) for x in o)
        inc = [x["incident_raised"] == x["incident_expected"] for x in o if x["incident_expected"] is not None]
        checks = [c["inside"] for x in o for c in x["interval_checks"]]
        quiet = sum(1 for x in o if not x["false_positive"])
        controls = label == "control (all)"
        rows.append([
            label, len(items),
            ci(sum(scored), len(scored)) if scored else "-",
            ratio(recalled, injected) if injected else "-",
            ratio(sum(inc), len(inc)),
            ratio(sum(checks), len(checks)) if checks else "-",
            f"{len(items) - quiet}/{len(items)}" if controls else "-",
        ])  # fmt: skip
    return Table(
        "rq1_detection",
        "Detection per fault configuration: top-1 accuracy (Wilson 95% CI), recall of injected causes, incident "
        "decision, measurements inside their expected interval, and false alarms on controls.",
        ["fault", "runs", "top-1", "recall", "incident", "in range", "false alarms"],
        rows,
    )


def confusion_pairs(results: list[dict]) -> list[tuple[str, str]]:
    pairs = []
    for r in results:
        expect, rec = r["row"]["expect"], r["record"]
        predicted = rec.get("top_category") or "none"
        if not expect["injected"]:
            pairs.append(("none", predicted))
        else:  # a predicted acceptable answer counts as the expected label, otherwise the dominant cause is expected
            pairs.append((predicted if predicted in expect["acceptable"] else expect["dominant"], predicted))
    return pairs


def rq1_confusion(results: list[dict]) -> tuple[Table, list[list[int]]]:
    matrix = confusion_matrix(confusion_pairs(results), LABELS)
    rows = [[expected, *counts] for expected, counts in zip(LABELS, matrix, strict=True)]
    return Table("rq1_confusion", "Confusion matrix of the top detector candidate (rows: expected, columns: predicted).",
                 ["expected \\ predicted", *LABELS], rows), matrix  # fmt: skip


def rq1_fidelity(results: list[dict]) -> Table:
    by_name: dict[str, list[dict]] = {}
    for r in results:
        for c in r["outcome"]["interval_checks"]:
            by_name.setdefault(c["name"], []).append(c)
    rows = []
    for name, checks in sorted(by_name.items()):
        mids = [abs(c["value"] - (c["lo"] + c["hi"]) / 2) for c in checks]
        rows.append([name, len(checks), ratio(sum(c["inside"] for c in checks), len(checks)),
                     num(median(c["miss_s"] for c in checks), 2), num(sum(mids) / len(mids), 2)])  # fmt: skip
    return Table("rq1_fidelity", "Measured phase values against the intervals expected from the injected knobs "
                 "(miss = distance outside the interval; deviation = distance from its midpoint).",
                 ["signal", "n", "in range", "median miss", "mean |dev.|"], rows)  # fmt: skip


def rq1_agreement(results: list[dict]) -> tuple[Table, list[float]]:
    errors = [
        abs(r["record"]["total_s"] - r["record"]["observed_total_s"])
        for r in results
        if r["record"].get("observed_total_s") is not None
    ]
    lags = [r["record"]["finalise_lag_s"] for r in results if r["record"].get("finalise_lag_s") is not None]
    rows = [
        ["|profiler total - driver wall clock| (s)", len(errors), num(median(errors), 2) if errors else "-", num(percentile(errors, 0.95), 2), num(max(errors), 2) if errors else "-"],
        ["Ready -> startup recorded (s)", len(lags), num(median(lags), 1) if lags else "-", num(percentile(lags, 0.95), 1), num(max(lags), 1) if lags else "-"],
    ]  # fmt: skip
    return Table("rq1_agreement", "Agreement of the profiler's startup total with an independent wall-clock measurement, and recording latency.",
                 ["measure", "n", "median", "p95", "max"], rows), errors  # fmt: skip


def figure_confusion(matrix: list[list[int]]) -> None:
    fig, ax = plt.subplots(figsize=(5.2, 4.4))
    ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(len(LABELS)), LABELS, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(LABELS)), LABELS, fontsize=7)
    ax.set_xlabel("predicted")
    ax.set_ylabel("expected")
    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            if value:
                ax.text(
                    j,
                    i,
                    value,
                    ha="center",
                    va="center",
                    fontsize=7,
                    color="white" if value > max(map(max, matrix)) / 2 else "black",
                )
    save_figure(fig, "rq1_confusion")


def figure_error_cdf(errors: list[float]) -> None:
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    ordered = sorted(errors)
    ax.step(ordered, [(i + 1) / len(ordered) for i in range(len(ordered))], where="post")
    ax.set_xlabel("|profiler total - wall clock| (s)")
    ax.set_ylabel("share of runs")
    ax.grid(alpha=0.3)
    save_figure(fig, "rq1_total_error_cdf")


def run_rq1() -> None:
    results = latest_ok(read_jsonl(DATA / "rq1.jsonl"))
    if not results:
        return print("rq1: no data")
    print(f"rq1: {len(results)} runs")
    save(rq1_detection(results))
    table, matrix = rq1_confusion(results)
    save(table)
    save(rq1_fidelity(results))
    agreement, errors = rq1_agreement(results)
    save(agreement)
    figure_confusion(matrix)
    if errors:
        figure_error_cdf(errors)


def run_sensitivity() -> None:
    path = DATA / "sensitivity.csv"
    if not path.exists():
        return print("sensitivity: no data (run python -m evals.sensitivity)")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    parameters = list(dict.fromkeys(r["parameter"] for r in rows))
    fig, axes = plt.subplots(1, len(parameters), figsize=(3.0 * len(parameters), 2.8), sharey=True)
    for ax, parameter in zip(axes, parameters, strict=True):
        items = [r for r in rows if r["parameter"] == parameter]
        xs = [float(r["value"]) for r in items]
        for metric, style in (("top1", "-o"), ("recall", "--s"), ("fp_rate", ":^")):
            ax.plot(xs, [float(r[metric]) for r in items], style, label=metric, markersize=3)
        default = next((float(r["value"]) for r in items if r["default"] == "True"), None)
        if default is not None:
            ax.axvline(default, color="grey", alpha=0.5)
        ax.set_xlabel(parameter, fontsize=7)
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=6)
    save_figure(fig, "rq1_sensitivity")
    print("sensitivity: done")


# --- RQ2: diagnosis quality -------------------------------------------------------------------------------------


def rq2_system_rows(label: str, rows: list[dict], view=lambda r: r) -> list:
    """One table row for a system. `view` selects the draft's score fields (the final draft, or the first draft)."""
    n = len(rows)
    scored = [view(r) for r in rows if view(r)]
    compounds = [view(r) for r in rows if r["kind"] == "compound" and view(r)]
    correct = sum(bool(s["correct"]) for s in scored)
    claims = sum(s.get("faith_claims") or 0 for s in scored)
    supported = sum(s.get("faith_supported") or 0 for s in scored)
    cited = [s["citations_valid"] for s in scored if s.get("citations_valid") is not None]
    exclusions = sum(bool(s.get("wrong_exclusions")) for s in compounds)
    return [label, n, ci(correct, n), ci(sum(bool(s["correct"]) for s in compounds), len(compounds)) if compounds else "-",
            ratio(exclusions, len(compounds)) if compounds and any("wrong_exclusions" in s for s in compounds) else "-",
            ratio(sum(cited), len(cited)) if cited else "-",
            f"{supported / claims:.2f}" if claims else "-"]  # fmt: skip


def rq2_tables(files: dict[tuple[str, str], list[dict]]) -> list[Table]:
    quality, cost, ablation = [], [], []
    for (system, model), rows in sorted(files.items()):
        rows = list({r["row_id"]: r for r in rows if r["status"] == "ok"}.values())
        if not rows:
            continue
        label = f"{system} ({model})" if system != "rules" else "rules"
        quality.append(rq2_system_rows(label, rows))
        if system == "copilot":
            first = [r for r in rows if r.get("first_draft")]
            ablation.append(rq2_system_rows(f"copilot, first draft ({model})", first, view=lambda r: r["first_draft"]))
            ablation.append(rq2_system_rows(f"copilot, validated ({model})", first))
            invalid = sum(1 for r in first if not r["attempts"][0]["valid"])
            retried = [r for r in first if not r["attempts"][0]["valid"]]
            recovered = sum(1 for r in retried if r["source"] == "llm")
            ablation.append([f"validator activity ({model})", len(first), f"first draft invalid: {ratio(invalid, len(first))}",
                             f"retry recovered: {ratio(recovered, len(retried))}", f"fallback: {ratio(sum(r['source'] == 'fallback' for r in first), len(first))}", "-", "-"])  # fmt: skip
        if system != "rules":
            calls = [r["llm_calls"] for r in rows]
            cost.append([label, len(rows), num(sum(calls) / len(calls), 1), num(sum(r["input_tokens"] for r in rows) / len(rows), 0),
                         num(sum(r["output_tokens"] for r in rows) / len(rows), 0), num(median(r["seconds"] for r in rows), 1)])  # fmt: skip
    tables = [
        Table(
            "rq2_quality",
            "Diagnosis quality on the frozen incident set: top-1 accuracy (Wilson 95% CI), compound-fault accuracy, "
            "compounds where a truly injected cause was ruled out, valid citations, numeric faithfulness.",
            ["system", "n", "top-1", "compound top-1", "wrong exclusion", "citations valid", "faithful"],
            quality,
        )
    ]
    if ablation:
        tables.append(
            Table(
                "rq2_validator",
                "Effect of the validator: the first synthesis draft scored before validation, against the final answer.",
                ["system", "n", "top-1", "compound top-1", "wrong exclusion", "citations valid", "faithful"],
                ablation,
            )
        )
    if cost:
        tables.append(
            Table(
                "rq2_cost",
                "Cost per diagnosis (averages; median latency).",
                ["system", "n", "LLM calls", "input tokens", "output tokens", "median s"],
                cost,
            )
        )
    return tables


def rq2_pairwise(files: dict[tuple[str, str], list[dict]]) -> Table | None:
    best = {k: {r["row_id"]: bool(r["correct"]) for r in rows if r["status"] == "ok"} for k, rows in files.items()}
    names = sorted(best)
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            shared = sorted(set(best[a]) & set(best[b]))
            if len(shared) < 5:
                continue
            only_a, only_b, p = mcnemar_exact([best[a][r] for r in shared], [best[b][r] for r in shared])
            out.append(
                [f"{a[0]} ({a[1]})", f"{b[0]} ({b[1]})", len(shared), only_a, only_b, "-" if p is None else f"{p:.3f}"]
            )
    return Table("rq2_pairwise", "Paired comparison on shared incidents (exact McNemar): incidents only the first / only the second system diagnosed correctly.",
                 ["system A", "system B", "n", "only A", "only B", "p"], out) if out else None  # fmt: skip


def figure_rq2(table: Table) -> None:
    labels = [r[0] for r in table.rows]
    values, errors = [], []
    for r in table.rows:
        k, n = (int(x) for x in r[2].split(" ")[0].split("/"))
        lo, hi = wilson_interval(k, n)
        values.append(k / n)
        errors.append([max(0.0, k / n - lo), max(0.0, hi - k / n)])  # k = n leaves -1e-16 of rounding
    fig, ax = plt.subplots(figsize=(5.4, 3.0))
    ax.bar(range(len(labels)), values, yerr=list(zip(*errors, strict=True)), capsize=3, color="#4477aa")
    ax.set_xticks(range(len(labels)), labels, rotation=30, ha="right", fontsize=7)
    ax.set_ylabel("top-1 accuracy")
    ax.set_ylim(0, 1.05)
    save_figure(fig, "rq2_accuracy")


def run_rq2() -> None:
    files = {}
    for path in sorted((DATA / "rq2").glob("*.jsonl")) if (DATA / "rq2").exists() else []:
        system, model = path.stem.split("__", 1)
        files[(system, model)] = read_jsonl(path)
    if not files:
        return print("rq2: no data")
    print(f"rq2: {sum(len(v) for v in files.values())} diagnoses")
    tables = rq2_tables(files)
    for table in tables:
        save(table)
    figure_rq2(tables[0])
    if (pair := rq2_pairwise(files)) is not None:
        save(pair)
    injection = []
    for (system, model), rows in sorted(files.items()):
        for r in rows:
            if r["status"] == "ok" and r["row_id"].startswith("inject-"):
                injection.append([r["row_id"], f"{system} ({model})", r.get("category"), r.get("swayed_by_injection"),
                                  r.get("injection_phrase_in_recommendation"), r.get("correct")])  # fmt: skip
    if injection:
        save(
            Table(
                "rq4_injection",
                "Prompt-injection incidents: whether the diagnosis followed the planted instruction.",
                ["incident", "system", "category", "swayed", "unsafe recommendation", "correct"],
                injection,
            )
        )


# --- RQ3: remediation effectiveness and harness correctness -----------------------------------------------------


def arm_values(experiment: dict, arm: str) -> list[float]:
    return [
        t["total_s"]
        for t in experiment["trials"]
        if t["arm"] == arm and not t["warmup"] and t["status"] == "ok" and t["total_s"] is not None
    ]


def rq3_main_table(records: list[dict]) -> Table:
    rows = []
    for r in records:
        e = r["experiment"]
        base, cand = arm_values(e, "baseline"), arm_values(e, "candidate")
        if not base or not cand:
            continue
        diff, lo, hi = bootstrap_median_diff(base, cand)
        p = mann_whitney_p(base, cand)
        qb, qc = quartiles(base), quartiles(cand)
        prep = e["comparison"].get("candidate", {}).get("prep_median_s")
        one_off = sum(v for v in (e.get("prep") or {}).values() if isinstance(v, int | float))
        rows.append([r["row"]["id"].removeprefix("rq3-"), e["action"], "yes" if r.get("action_correct") else "no", e["verdict"],
                     f"{qb[1]:.1f} [{qb[0]:.1f}-{qb[2]:.1f}]", f"{qc[1]:.1f} [{qc[0]:.1f}-{qc[2]:.1f}]",
                     f"{diff:.1f} ({lo:.1f}-{hi:.1f})", "-" if p is None else f"{p:.4f}",
                     f"{len(base)}/{len(cand)}", num(one_off) if one_off else "-", num(prep) if prep else "-", num((e["wall_s"] or 0) / 60, 0)])  # fmt: skip
    return Table("rq3_main", "Before/after experiments: medians with interquartile range (s), median-difference with bootstrap 95% CI, "
                 "Mann-Whitney p, successful trials per arm, one-off and per-trial preparation (s), wall minutes.",
                 ["experiment", "fix", "right fix", "verdict", "baseline", "candidate", "gain (s)", "p", "n b/c", "one-off", "per trial", "min"], rows)  # fmt: skip


def rq3_controls_table(records: list[dict]) -> Table:
    rows = []
    for name in ("aa", "harmful", "broken"):
        items = [r for r in records if r["control"] == name and r["status"] == "ok"]
        if not items:
            continue
        verdicts = {}
        for r in items:
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        correct = sum(bool(r["correct"]) for r in items)
        rows.append(
            [
                name,
                items[0]["expected"],
                len(items),
                ", ".join(f"{k}: {v}" for k, v in sorted(verdicts.items())),
                ci(correct, len(items)),
            ]
        )
    return Table(
        "rq3_controls",
        "Harness controls: forced fixes with a known outcome (A/A placebo, deliberately harmful, broken).",
        ["control", "expected verdict", "experiments", "verdicts", "correct (Wilson 95% CI)"],
        rows,
    )


def figure_rq3(records: list[dict]) -> None:
    usable = [
        r for r in records if arm_values(r["experiment"], "baseline") and arm_values(r["experiment"], "candidate")
    ]
    if not usable:
        return
    fig, ax = plt.subplots(figsize=(7.0, 3.2))
    positions, data, colors = [], [], []
    for i, r in enumerate(usable):
        for j, (arm, color) in enumerate((("baseline", "#cc6677"), ("candidate", "#44aa99"))):
            positions.append(i * 3 + j)
            data.append(arm_values(r["experiment"], arm))
            colors.append(color)
    boxes = ax.boxplot(data, positions=positions, widths=0.8, patch_artist=True, showfliers=True)
    for patch, color in zip(boxes["boxes"], colors, strict=True):
        patch.set_facecolor(color)
    ax.set_xticks(
        [i * 3 + 0.5 for i in range(len(usable))],
        [r["row"]["id"].removeprefix("rq3-") for r in usable],
        rotation=40,
        ha="right",
        fontsize=6,
    )
    ax.set_ylabel("startup time (s)")
    ax.set_yscale("log")
    ax.grid(alpha=0.3, axis="y")
    ax.legend([boxes["boxes"][0], boxes["boxes"][1]], ["baseline", "candidate"], fontsize=7)
    save_figure(fig, "rq3_before_after")


def run_rq3() -> None:
    main = latest_ok(read_jsonl(DATA / "rq3-main.jsonl"), key=lambda r: r["row"]["id"])
    controls = [r for r in read_jsonl(DATA / "rq3-controls.jsonl") if r["status"] == "ok"]
    if not main and not controls:
        return print("rq3: no data")
    print(f"rq3: {len(main)} experiments, {len(controls)} control experiments")
    if main:
        save(rq3_main_table(main))
        figure_rq3(main)
        correct = sum(bool(r.get("action_correct")) for r in main)
        save(
            Table(
                "rq3_action_selection",
                "The agent's chosen fix against the fixes an SRE would accept.",
                ["experiments", "right fix"],
                [[len(main), ci(correct, len(main))]],
            )
        )
    if controls:
        save(rq3_controls_table(controls))


# --- RQ4 / RQ5 --------------------------------------------------------------------------------------------------


def run_rq4() -> None:
    authz, policy, redaction = (DATA / f"rq4-{n}.json" for n in ("authz", "policy", "redaction"))
    if authz.exists():
        rows = json.loads(authz.read_text(encoding="utf-8"))
        save(
            Table(
                "rq4_authz",
                "What KubeSage's ServiceAccount may do (SubjectAccessReview against the cluster).",
                ["verb", "resource", "namespace", "allowed", "as designed", "purpose"],
                [[r["verb"], r["resource"], r["namespace"], r["allowed"], r["ok"], r["why"]] for r in rows],
            )
        )
    if policy.exists():
        rows = json.loads(policy.read_text(encoding="utf-8"))
        save(
            Table(
                "rq4_policy",
                "Crafted unsafe proposals and the layer that rejected each.",
                ["proposal", "rejected", "blocked by"],
                [[r["case"], r["rejected"], ", ".join(r["blocked_by"])] for r in rows],
            )
        )
    if redaction.exists():
        report = json.loads(redaction.read_text(encoding="utf-8"))
        save(
            Table(
                "rq4_redaction",
                f"Planted fake secrets ({report['secrets_planted']}) found on each surface the Copilot can read.",
                ["surface", "leaked secrets"],
                [[k, len(v)] for k, v in report["leaks"].items()],
            )
        )
    if not any(p.exists() for p in (authz, policy, redaction)):
        print("rq4: no data")


def run_rq5() -> None:
    path = DATA / "rq5-overhead.json"
    if not path.exists():
        return print("rq5: no data (run scripts/measure-overhead.ps1)")
    data = json.loads(path.read_text(encoding="utf-8"))
    save(
        Table(
            "rq5_overhead",
            "Resource use of the KubeSage backend.",
            ["measure", "value"],
            [[k, v] for k, v in data.items()],
        )
    )


PARTS = {"rq1": run_rq1, "sensitivity": run_sensitivity, "rq2": run_rq2, "rq3": run_rq3, "rq4": run_rq4, "rq5": run_rq5}


def main() -> None:
    selected = sys.argv[1:] or list(PARTS)
    for name in selected:
        PARTS[name]()


if __name__ == "__main__":
    main()
