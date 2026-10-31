<#
.SYNOPSIS
    Saves the current pod, its events and its Jaeger startup trace for a scenario as backend test fixtures.
.EXAMPLE
    .\scripts\run-scenario.ps1 -Scenario s3-model-weights -Cold; .\scripts\capture-fixture.ps1 -Scenario s3-model-weights
#>
param(
    [Parameter(Mandatory)][string]$Scenario,
    [string]$Name
)
. "$PSScriptRoot\lib.ps1"
if (-not $Name) { $Name = $Scenario }

$out = "$RepoRoot\backend\tests\fixtures\$Name"
New-Item -ItemType Directory -Force $out | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding $false

$podName = kubectl -n $DemoNamespace get pod -l "kubesage.io/scenario=$Scenario" -o jsonpath='{.items[0].metadata.name}'
if (-not $podName) { throw "No pod found for $Scenario" }
$podJson = (kubectl -n $DemoNamespace get pod $podName -o json) -join "`n"
$eventsJson = (kubectl -n $DemoNamespace get events --field-selector "involvedObject.name=$podName" -o json) -join "`n"
[IO.File]::WriteAllText("$out\pod.json", $podJson, $utf8)
[IO.File]::WriteAllText("$out\events.json", $eventsJson, $utf8)

$pod = $podJson | ConvertFrom-Json
$service = ($pod.spec.containers[0].env | Where-Object name -eq 'OTEL_SERVICE_NAME').value
$created = [datetime]::Parse($pod.metadata.creationTimestamp).ToUniversalTime()
$min = $created.AddMinutes(-1).ToString('o')
$max = $created.AddMinutes(15).ToString('o')
$uri = "http://127.0.0.1:30686/api/v3/traces?query.service_name=$service&query.start_time_min=$min&query.start_time_max=$max"
$trace = (Invoke-WebRequest -Uri $uri -UseBasicParsing).Content
[IO.File]::WriteAllText("$out\trace.json", $trace, $utf8)

Write-Host "Captured $podName -> $out"
