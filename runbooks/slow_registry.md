# Runbook: slow registry or network

## Symptoms
- Long `image_pull` phase even though the image is small.
- Pull throughput well below normal (under ~5 MB/s), or `ErrImagePull` / `ImagePullBackOff` events and retries.

## Evidence to check
- Startup timeline signals: `throughput_bytes_per_s`, `image_bytes`, `pull_s`.
- Pod events: `Pulling`/`Pulled` timing, `Failed` or `BackOff` events.
- Workload history: did the same image pull faster before (regression in the registry path)?

## Likely causes
- Registry or mirror under load, rate limiting, or a degraded network path.
- Pulling across regions/clouds; no node-local or regional mirror.
- Many nodes pulling the same image simultaneously during scale-up.

## Remediations
- `prepull_daemonset` — pre-pull the image so scale-up does not depend on registry throughput.
- Advisory: use a registry mirror or pull-through cache close to the cluster; check registry rate limits.

## Do not
- Do not shrink the image as the primary fix when throughput is the problem; it helps, but the path stays slow.
