# Runbook: slow application initialisation

## Symptoms
- Long `app_init` phase not explained by model download or dependency waits.
- Spans such as `init.compute` or `import` are long; CPU-bound work at startup.

## Evidence to check
- Startup timeline: child spans of `app_init` and their durations.
- Metric `cpu_throttling_ratio` during startup: a high ratio (e.g. > 0.3) means the CPU limit is slowing init.
- Metric `cpu_usage_cores` vs the container CPU limit in the pod spec.
- Container logs for startup steps.

## Likely causes
- CPU limit too low for startup work (throttling), though fine for steady state.
- Heavy imports, cache warm-up or index building at startup.

## Remediations (approved catalogue actions)
- `adjust_cpu_resources` — raise the CPU limit/request within approved bounds when throttling is evident.

## Advisory (not automated)
- Move expensive work to build time or make it lazy; warm caches asynchronously after readiness.

## Do not
- Do not raise CPU without throttling evidence; if usage is well below the limit, CPU is not the bottleneck.
