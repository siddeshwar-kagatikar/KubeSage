# KubeSage

An LLMOps platform for cold-start optimisation and incident diagnosis in Kubernetes.

KubeSage profiles pod startups, detects cold-start problems, diagnoses root causes with an evidence-grounded AI agent, proposes an allow-listed optimisation, tests it in a sandbox namespace and reports measured before/after results.

## Status

Phase 1 complete — Kubernetes lab environment with reproducible cold-start scenarios.

## Prerequisites

- Docker Desktop (WSL2 backend, `memory=10GB` in `%USERPROFILE%\.wslconfig`)
- kind ≥ v0.27, kubectl, helm
- Python 3.12, Node 22
- A Gemini API key (paid tier recommended; free-tier quotas are too small for repeated evaluations)

## Quick start

```powershell
Copy-Item .env.example .env   # then set GOOGLE_API_KEY
docker compose up -d postgres
```

## Lab environment

```powershell
.\scripts\setup-cluster.ps1          # kind cluster, throttled registry, model hub, Jaeger, Prometheus (idempotent)
.\scripts\build-images.ps1           # build + push demo-ai-service:slim and :fat
.\scripts\run-scenario.ps1 -Scenario s1-large-image -Cold
.\scripts\teardown.ps1 [-KeepRegistry]
```

| Component | Where |
|---|---|
| kind cluster `kubesage` | control-plane = infra, `kubesage-worker` = demo workloads, `kubesage-worker2` = sandbox experiments |
| Registry (push) | `localhost:5001` |
| Node pulls via `localhost:5001` | throttled proxy, ~20 MB/s (measured 18.4 MiB/s) |
| Node pulls via `localhost:5002` | degraded proxy, ~2 MB/s (measured 1.85 MiB/s) |
| Model hub | `http://model-hub.kubesage-infra.svc.cluster.local/weights.bin` (250 MB, 10 MB/s per connection) |
| Prometheus | http://127.0.0.1:30090 (5 s scrape, incl. cAdvisor) |
| Jaeger v2 | http://127.0.0.1:30686 (UI and `/api/v3/traces`) |

### Scenarios (measured on the reference machine)

| Scenario | What happens | Measured |
|---|---|---|
| `s1-large-image` | 618 MB (compressed) image with unused torch | cold pull 48.8 s (≈34 s transfer + unpack), Ready at 51 s |
| `s3-model-weights` | 250 MB weights downloaded on every start | `model.download` 25–28 s, Ready at 32–34 s |
| `s6a-slow-readiness` | probes `initialDelaySeconds: 45`, `periodSeconds: 20` | app ready in ~1 s, Ready condition at 61 s |

`-Cold` stops every demo scenario and removes the demo images from the workload node (`crictl rmi`) so the next start is a true cold pull. This is root access to the node outside Kubernetes RBAC — local lab use only.

### Notes and limitations

- The kind v0.33 node image leaves containerd's registry `config_path` empty; `deploy/kind/cluster.yaml` enables `/etc/containerd/certs.d` explicitly.
- The kubelet `Pulled` duration includes layer unpacking, not just network transfer.
- FastAPI ≥ 0.16x has native OpenTelemetry; the sample app disables it so only startup spans are exported.
- Kubernetes condition timestamps have 1 s resolution.
