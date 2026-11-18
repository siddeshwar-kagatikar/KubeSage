<#
.SYNOPSIS
    Samples the KubeSage backend's CPU and memory and the database growth for RQ5 (overhead).
.DESCRIPTION
    Finds the process listening on the API port, samples it every few seconds, and merges flat keys prefixed with
    -Label into backend/evals/data/rq5-overhead.json (read by `python -m evals.analyze rq5`).
    Run once while the backend is idle and once during a campaign:
        .\scripts\measure-overhead.ps1 -Label idle -Seconds 120
        .\scripts\measure-overhead.ps1 -Label campaign -Seconds 600
#>
param(
    [Parameter(Mandatory)][string]$Label,
    [int]$Seconds = 120,
    [int]$IntervalSeconds = 5,
    [int]$Port = 8000
)
. "$PSScriptRoot\lib.ps1"

$connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $connection) { throw "Nothing is listening on port ${Port}; start the backend first" }
$process = Get-Process -Id $connection.OwningProcess
$cores = [Environment]::ProcessorCount

function Get-Db {
    $q = "select pg_database_size('kubesage'), (select count(*) from pod_startups), (select count(*) from k8s_events)"
    $row = (docker exec kubesage-postgres psql -U kubesage -d kubesage -tAc $q) -split '\|'
    return @{ Bytes = [double]$row[0]; Startups = [int]$row[1]; Events = [int]$row[2] }
}

$dbStart = Get-Db
$cpuSamples = New-Object System.Collections.Generic.List[double]
$rssSamples = New-Object System.Collections.Generic.List[double]
$deadline = (Get-Date).AddSeconds($Seconds)
$previous = $process.TotalProcessorTime
$previousAt = Get-Date
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds $IntervalSeconds
    $process.Refresh()
    $now = Get-Date
    $cpu = $process.TotalProcessorTime
    # percentage of ONE core: 100 means one core fully busy
    $cpuSamples.Add((($cpu - $previous).TotalSeconds / ($now - $previousAt).TotalSeconds) * 100)
    $rssSamples.Add($process.WorkingSet64 / 1MB)
    $previous = $cpu
    $previousAt = $now
}
$dbEnd = Get-Db

function Round($v, $d = 1) { [math]::Round($v, $d) }
$newStartups = $dbEnd.Startups - $dbStart.Startups
$result = [ordered]@{
    "${Label}_seconds"            = $Seconds
    "${Label}_cpu_mean_pct_of_one_core" = Round (($cpuSamples | Measure-Object -Average).Average)
    "${Label}_cpu_max_pct_of_one_core"  = Round (($cpuSamples | Measure-Object -Maximum).Maximum)
    "${Label}_rss_mean_mb"        = Round (($rssSamples | Measure-Object -Average).Average)
    "${Label}_rss_max_mb"         = Round (($rssSamples | Measure-Object -Maximum).Maximum)
    # Windows trims idle working sets, so also report the process peak and its private (committed) memory
    "${Label}_peak_working_set_mb" = Round ($process.PeakWorkingSet64 / 1MB)
    "${Label}_private_mb"         = Round ($process.PrivateMemorySize64 / 1MB)
    "${Label}_new_startups"       = $newStartups
    "${Label}_new_events"         = $dbEnd.Events - $dbStart.Events
    "${Label}_db_growth_kb"       = Round (($dbEnd.Bytes - $dbStart.Bytes) / 1KB)
    "${Label}_db_total_mb"        = Round ($dbEnd.Bytes / 1MB)
    "host_logical_cores"          = $cores
}
if ($newStartups -gt 0) { $result["${Label}_db_kb_per_startup"] = Round ((($dbEnd.Bytes - $dbStart.Bytes) / 1KB) / $newStartups) }

$path = "$RepoRoot\backend\evals\data\rq5-overhead.json"
New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null
$merged = [ordered]@{}
if (Test-Path $path) { (Get-Content $path -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $merged[$_.Name] = $_.Value } }
foreach ($k in $result.Keys) { $merged[$k] = $result[$k] }
[IO.File]::WriteAllText($path, ($merged | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))
$result.GetEnumerator() | ForEach-Object { "{0,-40} {1}" -f $_.Key, $_.Value }
