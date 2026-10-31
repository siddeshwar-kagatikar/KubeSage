# Shared settings and helpers for the KubeSage lab scripts.
# Dot-source from a script:  . "$PSScriptRoot\lib.ps1"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ClusterName = 'kubesage'
$KubeContext = 'kind-kubesage'
$RegistryName = 'kubesage-registry'
$RegistryHost = 'localhost:5001'
$ProxyImage = 'kubesage/registry-proxy:local'
# Each proxy is a containerd mirror for one registry host name, with its own egress cap.
$Proxies = @(
    @{ Name = 'kubesage-registry-proxy';      Mirror = 'localhost:5001'; Rate = '160mbit' }  # ~20 MB/s "normal"
    @{ Name = 'kubesage-registry-proxy-slow'; Mirror = 'localhost:5002'; Rate = '16mbit' }   # ~2 MB/s "degraded"
)
$WorkloadNode = 'kubesage-worker'   # kubesage.io/pool=workload
$SandboxNode = 'kubesage-worker2'   # kubesage.io/pool=sandbox
$DemoNamespace = 'demo'

# Pick up PATH changes from recent installs, and prefer a current kubectl over Docker Desktop's bundled copy.
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$wingetKubectl = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter kubectl.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($wingetKubectl) { $env:Path = "$($wingetKubectl.DirectoryName);$env:Path" }

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$What,
        [Parameter(Mandatory)][scriptblock]$Command
    )
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Failed to $What (exit code $LASTEXITCODE)" }
}

function Test-ContainerExists {
    param([Parameter(Mandatory)][string]$Name)
    $found = docker ps -a --filter "name=^$Name$" --format '{{.Names}}'
    return [bool]$found
}

function ConvertFrom-NativeJson {
    # Native commands return one string per line; join before parsing (Windows PowerShell 5.1).
    param([Parameter(ValueFromPipeline)][string[]]$Line)
    begin { $buffer = New-Object System.Collections.Generic.List[string] }
    process { foreach ($l in $Line) { $buffer.Add($l) } }
    end { if ($buffer.Count) { ($buffer -join "`n") | ConvertFrom-Json } }
}

function Get-ImageCompressedBytes {
    # Sum of compressed layer sizes for linux/amd64, read from the local registry.
    param([Parameter(Mandatory)][string]$Repository, [Parameter(Mandatory)][string]$Tag)
    $accept = 'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, ' +
              'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'
    $base = "http://$RegistryHost/v2/$Repository/manifests"
    $manifest = Invoke-RestMethod -Uri "$base/$Tag" -Headers @{ Accept = $accept }
    if ($manifest.manifests) {
        $platform = $manifest.manifests | Where-Object { $_.platform.os -eq 'linux' -and $_.platform.architecture -eq 'amd64' } |
            Select-Object -First 1
        $manifest = Invoke-RestMethod -Uri "$base/$($platform.digest)" -Headers @{ Accept = $accept }
    }
    return ($manifest.layers | Measure-Object -Property size -Sum).Sum
}
