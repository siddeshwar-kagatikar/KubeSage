<#
.SYNOPSIS
    Deploys a cold-start scenario, restarts its pod and prints a startup timeline.
.PARAMETER Scenario
    Folder name under deploy/scenarios.
.PARAMETER Cold
    Evict the demo-ai-service images from the workload node first, forcing a cold image pull (lab only).
.EXAMPLE
    .\scripts\run-scenario.ps1 -Scenario s1-large-image -Cold
#>
param(
    [Parameter(Mandatory)]
    [ValidateSet('s1-large-image', 's3-model-weights', 's6a-slow-readiness')]
    [string]$Scenario,
    [switch]$Cold,
    [int]$TimeoutSeconds = 600
)
. "$PSScriptRoot\lib.ps1"

function ConvertTo-Utc {
    param($Value)
    if ($null -eq $Value -or $Value -eq '') { return $null }
    if ($Value -is [datetime]) { return $Value.ToUniversalTime() }
    $styles = [Globalization.DateTimeStyles]::AdjustToUniversal -bor [Globalization.DateTimeStyles]::AssumeUniversal
    return [datetime]::Parse($Value, [Globalization.CultureInfo]::InvariantCulture, $styles)
}

$selector = "kubesage.io/scenario=$Scenario"
Invoke-Checked 'select kube context' { kubectl config use-context $KubeContext | Out-Null }
Invoke-Checked "apply $Scenario" { kubectl apply -f "$RepoRoot\deploy\scenarios\$Scenario" }

# Stop the current pod so the next start is a fresh one. A cold start needs the images unused on the
# node, and other scenarios share them, so -Cold stops every scenario in the namespace.
$stopSelector = if ($Cold) { 'kubesage.io/scenario' } else { $selector }
if ($Cold) { Write-Host 'Cold start: stopping all demo scenarios so the images can be evicted' }
Invoke-Checked 'scale to 0' { kubectl -n $DemoNamespace scale deploy -l $stopSelector --replicas=0 }
Invoke-Checked 'wait for pod deletion' { kubectl -n $DemoNamespace wait --for=delete pod -l $stopSelector --timeout=180s }

if ($Cold) { & "$PSScriptRoot\evict-image.ps1" -Node $WorkloadNode }

Invoke-Checked 'scale to 1' { kubectl -n $DemoNamespace scale deploy -l $selector --replicas=1 }

$podName = $null
$deadline = (Get-Date).AddSeconds(60)
while (-not $podName) {
    if ((Get-Date) -gt $deadline) { throw 'No pod was created' }
    Start-Sleep -Milliseconds 500
    $podName = kubectl -n $DemoNamespace get pod -l $selector -o jsonpath='{.items[0].metadata.name}' 2>$null
}
Write-Host "Pod $podName created; waiting for Ready (timeout ${TimeoutSeconds}s)..."
Invoke-Checked 'wait for Ready' { kubectl -n $DemoNamespace wait --for=condition=Ready "pod/$podName" "--timeout=${TimeoutSeconds}s" }

# --- Timeline ----------------------------------------------------------------------------------
$pod = kubectl -n $DemoNamespace get pod $podName -o json | ConvertFrom-NativeJson
$created = ConvertTo-Utc $pod.metadata.creationTimestamp
$conditions = @{}
foreach ($c in $pod.status.conditions) { $conditions[$c.type] = ConvertTo-Utc $c.lastTransitionTime }
$started = ConvertTo-Utc $pod.status.containerStatuses[0].state.running.startedAt

$events = (kubectl -n $DemoNamespace get events --field-selector "involvedObject.name=$podName" -o json | ConvertFrom-NativeJson).items |
    Where-Object { $_.reason -in 'Scheduled', 'Pulling', 'Pulled', 'Created', 'Started', 'Unhealthy' }

Write-Host ''
Write-Host "Startup timeline for $podName (node $($pod.spec.nodeName)), seconds after creation:"
$rows = @(
    @{ Name = 'PodScheduled'; At = $conditions['PodScheduled'] }
    @{ Name = 'PodReadyToStartContainers'; At = $conditions['PodReadyToStartContainers'] }
    @{ Name = 'Container started'; At = $started }
    @{ Name = 'Ready'; At = $conditions['Ready'] }
)
foreach ($r in $rows) {
    if ($r.At) { Write-Host ('  {0,-28} {1,6:N0}' -f $r.Name, ($r.At - $created).TotalSeconds) }
}
Write-Host ''
Write-Host 'Events:'
foreach ($e in $events) { Write-Host ("  {0,-9} {1}" -f $e.reason, $e.message) }

$report = kubectl get --raw "/api/v1/namespaces/$DemoNamespace/pods/${podName}:8080/proxy/startup-report" 2>$null | ConvertFrom-NativeJson
if ($report) {
    Write-Host ''
    Write-Host 'App startup phases (from /startup-report):'
    foreach ($p in $report.phases) { Write-Host ('  {0,-20} {1,8:N2} s' -f $p.phase, $p.duration_s) }
}
