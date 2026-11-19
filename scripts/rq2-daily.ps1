<#
.SYNOPSIS
    One day's share of the RQ2 diagnosis study within the Gemini free-tier quota. Safe to run repeatedly.
.DESCRIPTION
    For each model: the LLM-only baseline first, then the full Copilot, recording every response for offline replay.
    The budget is a rolling 24-hour window per model (all systems together), so running this twice in a day
    cannot exceed the cap. Finished incidents are skipped; run it daily until `python -m evals.rq2 summary` shows
    every system complete. The rules baseline costs nothing and is always run.
#>
param(
    [string[]]$Models = @('gemini-2.5-flash', 'gemini-3.1-flash-lite'),
    [int]$Budget = 18   # free tier: 20 requests per model per day; leave a margin for other work
)
. "$PSScriptRoot\lib.ps1"
Set-Location "$RepoRoot\backend"
$py = ".\.venv\Scripts\python.exe"

if (-not (Test-Path "evals\incident_sets\rq2.yaml")) { & $py -m evals.rq2 freeze }
& $py -m evals.rq2 run --system rules
foreach ($model in $Models) {
    & $py -m evals.rq2 run --system llm_only --model $model --record --budget $Budget
    & $py -m evals.rq2 run --system copilot --model $model --record --budget $Budget
}
& $py -m evals.rq2 summary
