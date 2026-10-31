<#
.SYNOPSIS
    Creates the KubeSage lab: kind cluster, throttled local registry, model hub, Jaeger and Prometheus.
.DESCRIPTION
    Idempotent: existing cluster, containers and releases are reused.
#>
. "$PSScriptRoot\lib.ps1"

# --- Cluster -----------------------------------------------------------------------------------
$clusters = kind get clusters 2>$null
if ($clusters -notcontains $ClusterName) {
    Invoke-Checked 'create kind cluster' { kind create cluster --config "$RepoRoot\deploy\kind\cluster.yaml" --wait 180s }
} else {
    Write-Host "Cluster '$ClusterName' already exists"
}
Invoke-Checked 'select kube context' { kubectl config use-context $KubeContext }

# --- Local registry and throttled proxies (on the 'kind' Docker network) ------------------------
if (-not (Test-ContainerExists $RegistryName)) {
    Invoke-Checked 'start registry' {
        docker run -d --restart=always --name $RegistryName --network kind -p 127.0.0.1:5001:5000 registry:2
    }
}

Invoke-Checked 'build registry proxy image' { docker build -q -t $ProxyImage "$RepoRoot\deploy\infra\registry-proxy" }
foreach ($p in $Proxies) {
    if (-not (Test-ContainerExists $p.Name)) {
        Invoke-Checked "start $($p.Name)" {
            docker run -d --restart=always --name $p.Name --network kind --cap-add NET_ADMIN -e "RATE=$($p.Rate)" $ProxyImage
        }
    }
}

# containerd on every node resolves each mirror host name to its proxy (plain HTTP).
foreach ($node in (kind get nodes --name $ClusterName)) {
    foreach ($p in $Proxies) {
        $dir = "/etc/containerd/certs.d/$($p.Mirror)"
        $toml = "[host.`"http://$($p.Name):80`"]`n  capabilities = [`"pull`", `"resolve`"]`n"
        # Base64 avoids PowerShell 5.1 adding a BOM when piping text to a native command.
        $b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($toml))
        Invoke-Checked "write $dir/hosts.toml on $node" {
            docker exec $node sh -c "mkdir -p '$dir' && echo $b64 | base64 -d > '$dir/hosts.toml'"
        }
    }
}

# --- Namespaces and infrastructure -------------------------------------------------------------
Invoke-Checked 'apply namespaces' { kubectl apply -f "$RepoRoot\deploy\kubesage\namespaces.yaml" }
Invoke-Checked 'apply model hub' { kubectl apply -f "$RepoRoot\deploy\infra\model-hub.yaml" }
Invoke-Checked 'apply jaeger' { kubectl apply -f "$RepoRoot\deploy\infra\jaeger.yaml" }

Invoke-Checked 'add prometheus helm repo' {
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
}
Invoke-Checked 'install prometheus' {
    helm upgrade --install prometheus prometheus-community/prometheus --version 29.40.1 `
        --namespace kubesage-infra -f "$RepoRoot\deploy\infra\prometheus-values.yaml" --wait --timeout 5m
}

Invoke-Checked 'wait for model hub' { kubectl -n kubesage-infra rollout status deploy/model-hub --timeout=180s }
Invoke-Checked 'wait for jaeger' { kubectl -n kubesage-infra rollout status deploy/jaeger --timeout=180s }

Write-Host ''
Write-Host 'KubeSage lab ready:'
Write-Host "  Registry (push):  $RegistryHost"
Write-Host '  Prometheus:       http://127.0.0.1:30090'
Write-Host '  Jaeger UI/API:    http://127.0.0.1:30686'
