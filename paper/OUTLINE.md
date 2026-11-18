# Paper outline (workshop, 4-6 pages)

Working title: *KubeSage: evidence-grounded diagnosis and verified remediation of Kubernetes cold starts for AI services*

**Thesis.** An LLM is useful for cold-start incidents only if its claims are checked in code and its fixes are verified
by measurement. KubeSage combines deterministic phase profiling, a citation-validated LLM diagnosis, an allow-listed
optimiser and sandbox A/B verification, evaluated on a reproducible fault-injection testbed.

**Contributions to claim** (and only these):
1. A phase-level cold-start profiler joining Kubernetes events/conditions with application OpenTelemetry spans.
2. Diagnosis whose every finding must cite stored evidence, checked in code (citations, numeric faithfulness, consistency with strong detector findings).
3. Closed-loop remediation limited to an allow-list, enforced by RBAC (impersonated ServiceAccount), server-side dry-run, human approval and a sandbox A/B with guardrails.
4. An open testbed and a record/replay evaluation artefact (replay needs no API key).

## Sections and where the results go

| Section | Content | Generated artefacts |
|---|---|---|
| 1 Introduction | cold starts of AI services; why LLM-only RCA is risky; thesis; contributions | - |
| 2 Background | startup phase model (scheduling, sandbox, pull, init, app spans, readiness) | waterfall screenshot of one incident (dashboard) |
| 3 Design | profiler; evidence store + validator; optimiser, policy, RBAC, approval; A/B protocol and verdict rule | architecture diagram (draw by hand) |
| 4 Implementation | stack, testbed (throttled registry, model hub, fault knobs), record/replay | - |
| 5.1 RQ1 profiling and detection | accuracy, confusion, fidelity, agreement, sensitivity | `rq1_detection`, `rq1_confusion`, `rq1_fidelity`, `rq1_agreement`, figures `rq1_confusion`, `rq1_total_error_cdf`, `rq1_sensitivity` |
| 5.2 RQ2 diagnosis | rules vs LLM-only vs full, validator ablation, faithfulness, cost | `rq2_quality`, `rq2_validator`, `rq2_pairwise`, `rq2_cost`, figure `rq2_accuracy` |
| 5.3 RQ3 remediation | main experiments, controls, action selection | `rq3_main`, `rq3_controls`, `rq3_action_selection`, figure `rq3_before_after` |
| 5.4 RQ4 safety | authorisation, policy, redaction, prompt injection | `rq4_authz`, `rq4_policy`, `rq4_redaction`, `rq4_injection` |
| 5.5 RQ5 overhead | CPU/memory/DB growth, latency | `rq5_overhead`, `rq1_agreement` |
| 6 Discussion and limitations | see below | - |
| 7 Related work | see below | - |
| 8 Conclusion | findings, artefact link | - |

## Claims to make, and not to make

Make: results exactly as generated, with intervals; what the validator caught (first-draft invalid rate, retry
recovery, fallback rate); that the allow-list and policy held under crafted and injected inputs; that the A/B
harness returned the right verdict on its controls.

Do not make: generalisation to production clusters, other clouds or other fault types; "the LLM is better than
rules" unless RQ2 shows it with intervals that exclude no-difference; statistical significance from n=8 trials;
anything about models other than the ones named.

If a result is unfavourable (the LLM-only baseline matches the full system on single faults, the validator rarely
fires), report it: the compound and adversarial incidents and the safety results carry the argument.

## Limitations to state plainly

Synthetic faults injected into one demo service; single-host kind cluster; author-defined ground truth derived from
the injection knobs; small n; free-tier model quotas; two of five actions rely on the demo app's conventions
(`MODEL_URL`/`MODEL_DIR`/`MODEL_CACHE`, `GET /predict`); no node autoscaler, so capacity is detection plus advice;
page-cache effects in RQ3; pilot-calibrated pull-time interval.

## Related work to survey (verify every citation before using it)

Container image pull acceleration (lazy pulling, snapshotters, image streaming); serverless and container
cold-start studies; LLM-based root-cause analysis and AIOps incident management; hallucination and grounding checks
for LLM outputs (attribution, citation verification); safe or verified automated remediation and canary or A/B
verification of operational changes; LLM agent safety (tool allow-listing, prompt injection). Do not cite from memory:
find and read each paper, and check the venue's scope, page limit and deadline in its current call for papers.

## Writing checklist

- Every number has a source file under `backend/evals/data` and a generator in `evals/analyze.py`.
- The abstract states n, the models, the testbed and the limitation in one sentence each.
- Artefact link: the repository, the commit hash of the evaluated run (`evals/data/*.meta.json`), and the replay command.
