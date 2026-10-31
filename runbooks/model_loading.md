# Runbook: slow model-weight loading

## Symptoms
- The app's `model.download` and/or `model.load` spans dominate the `app_init` phase.
- Startup time scales with model size; every restart downloads the weights again.

## Evidence to check
- Startup timeline: `model.download` / `model.load` durations and attributes (`bytes`, `cache_hit`).
- Container logs: `phase_end` lines for `model.download` (bytes, attempts, cache_hit).
- Pod spec: is there a persistent volume for model files? Is `MODEL_DIR` on an `emptyDir` (lost on restart)?
- Workload history: warm starts still slow (cache not reused) vs only cold starts slow.

## Likely causes
- Weights fetched from a remote model store on every start with no local cache.
- Cache on an `emptyDir`, which does not survive pod replacement.
- Throttled or distant model store.

## Remediations (approved catalogue actions)
- `model_cache_pvc` — keep weights on a persistent volume, pre-populated by a prefetch job, and enable the app's
  cache reuse. Subsequent starts load from local disk. Report the one-off prefetch time separately.

## Advisory (not automated)
- Bake small models into the image (trade-off: larger image, see large_image runbook).
- Use lazy loading or a model server that keeps weights resident.

## Do not
- Do not raise CPU limits to fix a download-bound startup; check the download span first.
