# Runbook: insufficient capacity or delayed scaling

## Symptoms
- Long `scheduling` phase; `FailedScheduling` events ("Insufficient cpu/memory", "didn't match node selector").
- Pods Pending during scale-up until capacity frees up or nodes are added.

## Evidence to check
- Pod events: `FailedScheduling` messages and counts.
- Node capacity: allocatable vs requested CPU/memory on candidate nodes.
- Pod spec: resource requests, node selectors, affinity, tolerations.

## Likely causes
- Requests larger than any node's free capacity (over-requesting).
- Node selector/affinity restricting placement to a small pool.
- Cluster autoscaler lag (not present in the kind lab).

## Remediations
- Advisory: right-size requests from observed usage; relax placement constraints; keep headroom with
  low-priority placeholder pods where a node autoscaler exists.
- No automated action in the current catalogue; changes to requests affect scheduling cluster-wide.

## Do not
- Do not lower requests below observed usage to force scheduling; it trades startup time for OOM kills.
