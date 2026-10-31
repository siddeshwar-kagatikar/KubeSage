<#
.SYNOPSIS
    Deletes the KubeSage kind cluster and, unless -KeepRegistry, the registry and proxy containers.
#>
param([switch]$KeepRegistry)
. "$PSScriptRoot\lib.ps1"

kind delete cluster --name $ClusterName
if (-not $KeepRegistry) {
    foreach ($name in @($RegistryName) + ($Proxies | ForEach-Object { $_.Name })) {
        if (Test-ContainerExists $name) { docker rm -f $name | Out-Null; Write-Host "Removed $name" }
    }
}
