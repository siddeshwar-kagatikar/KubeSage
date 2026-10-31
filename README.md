# KubeSage

An LLMOps platform for cold-start optimisation and incident diagnosis in Kubernetes.

KubeSage profiles pod startups, detects cold-start problems, diagnoses root causes with an evidence-grounded AI agent, proposes an allow-listed optimisation, tests it in a sandbox namespace and reports measured before/after results.

## Status

Phase 0 — prerequisites and skeleton.

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

Further setup is added phase by phase.
