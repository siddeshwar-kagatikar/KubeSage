<#
.SYNOPSIS
    Removes matching images from a kind node's containerd store so the next start is a true cold pull.
.DESCRIPTION
    LAB ONLY: this runs crictl as root inside the node container, outside Kubernetes RBAC.
    Delete the pods using the image first; exited containers still referencing it are removed here.
#>
param(
    [string]$Node,
    [string]$Match = 'demo-ai-service',
    [int]$TimeoutSeconds = 120
)
. "$PSScriptRoot\lib.ps1"
if (-not $Node) { $Node = $WorkloadNode }

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$reported = @{}
while ($true) {
    $images = @((docker exec $Node crictl images -o json | ConvertFrom-NativeJson).images | Where-Object {
        (@($_.repoTags) + @($_.repoDigests)) -match [regex]::Escape($Match)
    })
    if ($images.Count -eq 0) { break }
    if ((Get-Date) -gt $deadline) { throw "Timed out evicting '$Match' on $Node" }

    $containers = @((docker exec $Node crictl ps -a -o json | ConvertFrom-NativeJson).containers)
    foreach ($img in $images) {
        $users = @($containers | Where-Object { $_.imageRef -eq $img.id -or $_.image.image -eq $img.id })
        $running = @($users | Where-Object { $_.state -ne 'CONTAINER_EXITED' })
        if ($running.Count) {
            if (-not $reported[$img.id]) {
                $names = ($running | ForEach-Object { $_.labels.'io.kubernetes.pod.name' }) -join ', '
                Write-Host "Waiting: $($img.repoTags -join ',') is still used by running pod(s): $names"
                $reported[$img.id] = $true
            }
            continue
        }
        foreach ($c in $users) { docker exec $Node crictl rm $c.id | Out-Null }
        docker exec $Node crictl rmi $img.id | Out-Null
        Write-Host "Evicted $($img.repoTags -join ',') ($($img.id.Substring(0, 19))) from $Node"
    }
    Start-Sleep -Seconds 2
}
Write-Host "No images matching '$Match' remain on $Node"
