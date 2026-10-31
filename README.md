# KubeSage

An LLMOps platform for cold-start optimisation and incident diagnosis in Kubernetes.

KubeSage profiles pod startups, detects cold-start problems, diagnoses root causes with an evidence-grounded AI agent, proposes an allow-listed optimisation, tests it in a sandbox namespace and reports measured before/after results.

## Status

Phase 3 — Incident Copilot (evidence-grounded LLM diagnosis) built; final live evaluation of prompt v2 pending
Gemini quota.

## Running KubeSage

```powershell
docker compose up -d postgres

# Backend (from backend/)
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python -m uvicorn kubesage.main:app --port 8000
.\.venv\Scripts\python -m pytest            # unit tests on recorded lab fixtures

# Frontend (from frontend/)
npm install
npm run dev                                 # http://localhost:5173, proxies /api to :8000
```

The backend watches the `demo` and `kubesage-sandbox` namespaces. Each pod start is finalised once Ready:
events and OTel startup spans are joined into a phase timeline, rule-based detectors attribute the time to a
category (large image, slow registry, model loading, app init, capacity, slow readiness), and an incident is
opened when the startup exceeds the workload SLO (default 30 s) or 1.5× its baseline (median of ≥ 3 earlier
startups with the same cold/warm state). Incidents are raised for `demo` only; sandbox startups are profiled
but never raise incidents.

`scripts/capture-fixture.ps1` records a live startup (pod, events, trace) as a test fixture.

## Incident Copilot

"Run diagnosis" on an incident starts a LangGraph agent (Gemini Flash by default; `KUBESAGE_LLM_PROVIDER` /
`KUBESAGE_LLM_MODEL` select another model). It investigates with read-only tools scoped to that incident — pod
events, pod spec, logs, workload history, Prometheus metrics, node capacity, runbooks (`runbooks/`) and past
incidents — all read from data persisted when the pod became Ready, with secrets redacted.

Grounding is enforced in code: every tool result is stored as numbered evidence (E1, E2, ...), every finding must
cite existing evidence, and a diagnosis that contradicts a strong detector result must rule it out. Invalid output
gets one retry with the errors as feedback; if it is still invalid, or the model is unavailable, the run completes
as a clearly labelled detector fallback. Every model call is logged (`/api/llm/calls`) with tokens, latency and
prompt version.

```powershell
# from backend/
python -m evals.run                   # live evaluation against ground truth (evals/scenarios.yaml)
python -m evals.run --mode record     # live, and save validated runs to evals/recordings/
python -m evals.run --mode replay     # replay recordings: no API calls
```

Quota: the Gemini free tier allows 20 requests per model per day; one diagnosis uses about 5–6. A paid key is
needed for regular use; on quota errors the Copilot degrades to the detector fallback.

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
- The app's process start comes from `/proc` (`btime` has 1 s resolution). When it would place the app start
  before the image pull ended, the profiler clamps it and records `span_clock_skew_s`.
- Pods deleted before becoming Ready (aborted starts) are not recorded; pods that never become Ready
  (e.g. stuck Pending) are not profiled yet.
