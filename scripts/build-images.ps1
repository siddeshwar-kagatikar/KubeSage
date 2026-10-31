<#
.SYNOPSIS
    Builds the demo-ai-service image variants and pushes them directly to the local registry.
.PARAMETER Variant
    slim (small, production-style) | fat (bloated AI image with unused torch) | all
#>
param(
    [ValidateSet('all', 'slim', 'fat')][string]$Variant = 'all'
)
. "$PSScriptRoot\lib.ps1"

$context = "$RepoRoot\sample-apps\demo-ai-service"
$variants = if ($Variant -eq 'all') { @('slim', 'fat') } else { @($Variant) }

foreach ($v in $variants) {
    $image = "$RegistryHost/demo-ai-service:$v"
    Invoke-Checked "build $image" {
        docker build --provenance=false --sbom=false -f "$context\Dockerfile.$v" -t $image $context
    }
    Invoke-Checked "push $image" { docker push $image }
    $bytes = Get-ImageCompressedBytes -Repository 'demo-ai-service' -Tag $v
    Write-Host ("{0}: {1:N1} MB compressed" -f $image, ($bytes / 1MB))
}
