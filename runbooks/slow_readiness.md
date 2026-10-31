# Runbook: slow readiness

## Symptoms
- The app reports ready (its `startup` span ends) well before Kubernetes marks the pod Ready — a long `readiness`
  phase. Or the app waits on a dependency (`dependency.connect` span).

## Evidence to check
- Startup timeline: `readiness` phase duration vs `app_init`; `dependency.connect` span.
- Pod spec: `readinessProbe` (`initialDelaySeconds`, `periodSeconds`, `failureThreshold`), presence of a `startupProbe`.
- Pod events: `Unhealthy` readiness failures after the app was ready (dependency or endpoint issue) vs none
  (probe simply not checked yet — configuration delay).
- Container logs: `startup_complete` time and `dependency_retry` lines.

## Likely causes
- Over-conservative probe configuration: large `initialDelaySeconds` and/or `periodSeconds` (configuration delay).
- A slow or unavailable dependency the app waits for before reporting ready.

## Remediations (approved catalogue actions)
- `tune_probes` — for configuration delay: use a `startupProbe` with a short period for the startup window and a
  tight `readinessProbe` (`initialDelaySeconds: 0`, short `periodSeconds`). This improves how quickly readiness is
  detected; it does not make the application itself faster, and results must be reported that way.

## Advisory (not automated)
- Dependency waits: fix or warm the dependency, add retries with backoff, or decouple startup from it.

## Do not
- Do not shorten probes so aggressively that a slow-but-healthy pod is never marked Ready.
