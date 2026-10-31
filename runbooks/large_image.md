# Runbook: large container image

## Symptoms
- Cold starts dominated by the `image_pull` phase; warm starts (image cached on the node) are fast.
- `Pulled` event reports a large image size (hundreds of MB or more compressed) and a long pull duration.

## Evidence to check
- Startup timeline: `image_pull` duration and share of total; `cold_start` true.
- Pod events: `Pulled` message (duration, `Image size`).
- Workload history: warm vs cold startup totals for the same image.
- Pull throughput (`throughput_bytes_per_s`): if it is normal (≥ 5 MB/s) the problem is size, not the registry.

## Likely causes
- Full OS base images (e.g. `python:3.x` instead of `-slim`), build toolchains left in the final stage.
- Large unused dependencies (ML frameworks such as torch/tensorflow installed but not imported).
- Model weights or datasets baked into the image.

## Remediations (approved catalogue actions)
- `switch_image_variant` — move the workload to a slimmer, pre-built image variant (multi-stage build, slim base,
  only runtime dependencies). Removes the cost permanently. Verify the variant runs the same code.
- `prepull_daemonset` — pre-pull the image on nodes ahead of scale-up. Hides the pull from startup but moves the
  cost (bandwidth, disk) rather than removing it; report the pre-pull time alongside the startup gain.

## Advisory (not automated)
- Restructure the Dockerfile: multi-stage build, `--no-cache-dir`, remove unused packages, order layers so that
  rarely-changing layers are cached.

## Do not
- Do not set `imagePullPolicy: IfNotPresent` and call it a fix: it does nothing for a genuinely cold node.
