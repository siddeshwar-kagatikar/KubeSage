# Evaluation runbook

How the numbers for the paper are produced. Every table and figure is regenerated from raw data by
`python -m evals.analyze`; nothing is typed in by hand. Raw data is committed so the study can be audited and the
LLM parts replayed without an API key.

## Research questions

| RQ | Question | Needs | Code |
|---|---|---|---|
| RQ1 | Does the profiler attribute startup time correctly, and do the detectors pick the right cause? | cluster, backend (no LLM) | `evals/campaign.py`, `evals/expectations.py`, `evals/sensitivity.py` |
| RQ2 | Does tool-using, citation-validated diagnosis beat simpler designs, and does validation matter? | LLM | `evals/rq2.py`, `evals/faithfulness.py` |
| RQ3 | Do the chosen fixes help, and does the A/B harness tell good fixes from no-ops and harmful ones? | cluster, backend, 1 LLM call per experiment | `evals/rq3.py` |
| RQ4 | Is it safe and robust? | none (authz, policy, redaction); LLM (injection) | `evals/rq4.py` |
| RQ5 | What does it cost? | backend running | `scripts/measure-overhead.ps1` |

## Prerequisites

```powershell
docker compose up -d postgres
.\scripts\setup-cluster.ps1                 # once; idempotent
.\scripts\build-images.ps1                  # after any change to the demo app
cd backend; .\.venv\Scripts\pip install -e ".[dev,eval]"
# start the backend (it profiles every pod the studies start) and leave it running:
.\.venv\Scripts\python -m uvicorn kubesage.main:app --port 8000
```

While a campaign or experiment runs: keep the laptop awake and plugged in (the drivers ask Windows not to sleep,
but Modern Standby can still pause the cluster), do not run other heavy work (image unpacking and CPU-bound
phases are timing-sensitive), and **do not restart the backend during RQ3** (its startup recovery marks a running
experiment failed and cleans the sandbox).

## Data layout

| Path | Content |
|---|---|
| `backend/evals/campaigns/*.yaml` | the exact rows each campaign runs (generated, committed) |
| `backend/evals/data/rq1.jsonl`, `rq4.jsonl` | one line per run: row, what was measured, ground-truth scoring, tool snapshots |
| `backend/evals/data/rq1-pilot.jsonl` | the pilot run used to debug the driver; **not** evaluated |
| `backend/evals/incident_sets/rq2.yaml` | the frozen incident set |
| `backend/evals/data/rq2/<system>__<model>.jsonl` | RQ2 results; `evals/recordings/rq2/…` the recorded model responses |
| `backend/evals/data/rq3-main.jsonl`, `rq3-controls.jsonl` | every experiment with all trials |
| `backend/evals/data/rq4-*.json`, `rq5-overhead.json`, `sensitivity.csv` | safety matrices, overhead, sweep |
| `paper/tables`, `paper/figures` | generated CSV/LaTeX tables and PDF/PNG figures |

## Step by step

All commands run from `backend/` with `.\.venv\Scripts\python`.

**1. RQ1: fault-injection campaign (no LLM, about 3 hours).**
```powershell
python -m evals.campaign generate rq1 ; python -m evals.campaign generate rq4
python -m evals.campaign run rq1 --dry-run      # every manifest accepted by the API server
python -m evals.campaign run rq1 --smoke        # one row per fault configuration (about 25 minutes)
python -m evals.campaign run rq1                # all 90 rows; resumable (rerun the same command)
python -m evals.campaign run rq4                # 4 adversarial rows
python -m evals.campaign summary rq1
python -m evals.sensitivity                     # offline threshold sweep
```
90 rows = 7 single faults x 3 severities x 3 repeats (63), 6 compound faults x 3 (18), 3 controls x 3 (9). Rows
run in a fixed shuffled order. Each row evicts the demo images from the workload node (cold) or ensures them (warm),
sets the registry bandwidth, injects the fault knobs and records what the profiler measured next to the expected
ground truth. The SLO of the evaluation workload is 10 s, so every injected fault raises an incident and healthy
starts do not.

**2. RQ4 (no LLM).**
```powershell
python -m evals.rq4 all                         # authorisation matrix, policy matrix, redaction scan
```

**3. RQ2: diagnosis quality (LLM; the scarce resource).**
```powershell
python -m evals.rq2 freeze                      # picks 30 incidents from the RQ1/RQ4 data; commit the YAML
python -m evals.rq2 run --system rules
python -m evals.rq2 run --system llm_only --model gemini-2.5-flash --record
python -m evals.rq2 run --system copilot  --model gemini-2.5-flash --record --budget 18   # repeat daily
python -m evals.rq2 summary
```
The free tier allows 20 requests per model per day and 5 per minute; the full system needs about 5 requests per
incident, so 30 incidents take about 8 days per model. `--budget` is a rolling 24-hour window over all systems run on
that model, so repeated runs cannot exceed the cap. `.\scripts\rq2-daily.ps1` runs one day's share for both models;
run it once a day until `python -m evals.rq2 summary` shows every system complete. Runs are resumable and stop
cleanly when the daily quota is exhausted. Each model has its own quota, so a second model can run in parallel. A paid key removes the constraint
(a full study then costs tens of dollars). Without one, shrink the set (`freeze` then delete entries) and say so in
the paper. All results can be replayed offline with `--replay` (same command, no API calls).

**4. RQ3: before/after experiments (cluster; about 4 hours plus about 1.6 hours of controls).**
```powershell
python -m evals.rq3 main --model gemini-3.5-flash       # 10 experiments, the agent picks the fix, n=8 per arm
python -m evals.rq3 controls                            # A/A x6, harmful x3, broken x3 (forced fixes, no LLM)
python -m evals.rq3 summary
```

**5. RQ5 and the paper artefacts.**
```powershell
.\scripts\measure-overhead.ps1 -Label idle -Seconds 120
.\scripts\measure-overhead.ps1 -Label campaign -Seconds 600     # while a campaign runs
python -m evals.analyze                                          # writes paper/tables and paper/figures
```

## Method notes and threats to validity (state these in the paper)

- **Ground truth comes from the injected knobs**, not from the profiler: expected seconds are derived from bytes and
  rates, kubelet probe semantics, the hog release time and the dependency pod's own start time
  (`evals/expectations.py`). CPU-bound init has no analytic expectation; it is reported as a calibration curve.
- **Pilot calibration.** The pilot run showed that the unpacking part of a cold pull (about 30 s for the 648 MB image)
  exceeded the first allowance, so the upper bound of the pull-time interval was widened per image size before the
  evaluated campaign. The pilot rows are excluded from the data.
- **Synthetic faults on one laptop.** All seven faults are injected into one demo service on a single-host kind
  cluster with a two-node worker pool. This supports a feasibility and safety argument, not claims about production.
- **Small samples.** Three repeats per cell in RQ1; 8 trials per arm in RQ3 (bootstrap CIs and Mann-Whitney U are
  reported; treat as demonstration, not proof). RQ2 uses 30 incidents; intervals are Wilson 95 %.
- **Evidence snapshots.** Prometheus keeps 2 days and node capacity is a current-state view, so the driver snapshots
  both at capture time; all RQ2 systems see identical evidence however late they run.
- **Workload history and past incidents are disabled in RQ2.** All campaign rows share one workload, so those two
  tools would return unrelated rows and change as data accumulates; they return empty results for every system.
- **Model versions.** Results are tied to the named Gemini models and `copilot-v2` / `llm-only-v1` prompts; any prompt
  change bumps the version and needs a re-run on a held-out set. Do not tune prompts on the evaluation incidents.
- **Page cache and registry caching** can flatter candidate arms in RQ3; the lab evicts images from the node before
  every trial but not the page cache.
- **Faithfulness checker** counts arithmetic-derived numbers as unsupported, so it is a lower bound.
