/**
 * TypeScript mirror of backend/kubesage/schemas.py (the source of truth).
 * Datetimes are ISO-8601 UTC strings.
 */

export type ISODateTime = string

export type Category =
  | 'large_image'
  | 'slow_registry'
  | 'model_loading'
  | 'app_init'
  | 'capacity'
  | 'slow_readiness'

export type PhaseSource = 'k8s' | 'event' | 'span'

export type SignalValue = number | boolean | string | null

export interface PhaseOut {
  name: string
  start: ISODateTime
  end: ISODateTime
  duration_s: number
  source: PhaseSource
  parent: string | null
  attributes: Record<string, unknown>
}

export interface CandidateOut {
  category: Category
  /** Share of total startup time explained, 0..1 */
  score: number
  seconds: number
  summary: string
  evidence: Record<string, unknown>
}

export interface StartupSummary {
  id: number
  workload_id: number
  namespace: string
  pod_name: string
  node: string | null
  image: string | null
  created_at: ISODateTime
  ready_at: ISODateTime | null
  total_s: number | null
  cold: boolean | null
  status: 'starting' | 'ready'
  top_category: Category | null
  incident_id: number | null
}

export interface StartupDetail extends StartupSummary {
  spans_found: boolean
  phases: PhaseOut[]
  /** Free-form detector inputs: mostly numbers, booleans or strings (probe settings may nest). */
  signals: Record<string, SignalValue | Record<string, unknown>>
  candidates: CandidateOut[]
}

export interface WorkloadOut {
  id: number
  namespace: string
  kind: string
  name: string
  slo_seconds: number
  startup_count: number
  median_total_s: number | null
  open_incidents: number
  last_startup: StartupSummary | null
}

export interface SloUpdate {
  slo_seconds: number
}

export type IncidentStatus = 'open' | 'diagnosed' | 'resolved'

export interface IncidentOut {
  id: number
  workload_id: number
  workload_name: string
  namespace: string
  startup_id: number
  pod_name: string
  status: IncidentStatus
  detected_at: ISODateTime
  total_s: number
  threshold_s: number
  breach_reason: 'slo' | 'regression'
  baseline_s: number | null
  top_category: Category | null
  candidates: CandidateOut[]
}

export interface HealthOut {
  status: 'ok' | 'degraded'
  database: boolean
  kubernetes: boolean
  jaeger: boolean
  prometheus: boolean
  watchers_running: boolean
  clock_offset_s: number | null
}

// ---- Phase 3: AI diagnosis ----

/** A Category, or "unknown" when the diagnosis could not determine one. */
export type DiagnosisCategory = Category | 'unknown'

export interface EvidenceOut {
  /** Stable reference cited by findings, e.g. "E1". */
  ref: string
  tool: string
  args: Record<string, unknown>
  result: Record<string, unknown>
  created_at: ISODateTime
}

export interface FindingOut {
  statement: string
  evidence_ids: string[]
}

export interface RuledOutOut {
  /** A Category (or other string the model produced). */
  category: string
  reason: string
  evidence_ids: string[]
}

export interface RecommendationOut {
  action: string
  rationale: string
  evidence_ids: string[]
}

export type DiagnosisStatus = 'running' | 'completed' | 'failed'
export type DiagnosisSource = 'llm' | 'fallback'
export type Confidence = 'high' | 'medium' | 'low'

export interface DiagnosisRunOut {
  id: number
  incident_id: number
  status: DiagnosisStatus
  /** 'llm' = validated AI diagnosis; 'fallback' = deterministic detector result. */
  source: DiagnosisSource | null
  provider: string | null
  model: string | null
  prompt_version: string | null
  category: string | null
  summary: string | null
  confidence: Confidence | null
  findings: FindingOut[]
  ruled_out: RuledOutOut[]
  recommendations: RecommendationOut[]
  validation_errors: string[]
  tool_calls: number
  error: string | null
  started_at: ISODateTime
  finished_at: ISODateTime | null
  llm_calls: number
  input_tokens: number | null
  output_tokens: number | null
  evidence: EvidenceOut[]
}

export interface LlmCallOut {
  id: number
  run_id: number | null
  purpose: string
  provider: string
  model: string
  prompt_version: string | null
  input_tokens: number | null
  output_tokens: number | null
  latency_ms: number
  status: 'ok' | 'error'
  error: string | null
  created_at: ISODateTime
}

// ---- Phases 4–6: fix plans, experiments, audit, LLMOps ----

export interface PolicyCheckOut {
  name: string
  passed: boolean
  detail: string
}

export type PlanStatus =
  | 'awaiting_approval'
  | 'rejected_by_policy'
  | 'rejected'
  | 'running'
  | 'completed'
  | 'failed'
  | 'aborted'

/** Patch for the original workload. Never applied automatically. */
export interface PlanExport {
  patch?: Record<string, unknown>
  objects?: Record<string, unknown>[]
  command?: string
}

export interface ActionPlanOut {
  id: number
  incident_id: number
  diagnosis_run_id: number | null
  category: string
  action: string
  action_title: string
  action_note: string
  params: Record<string, unknown>
  rationale: string
  /** 'llm' = the agent chose the action; 'fallback' = deterministic default. */
  source: 'llm' | 'fallback'
  llm_error: string | null
  risk: string
  status: PlanStatus
  policy_checks: PolicyCheckOut[]
  /** Unified diff of the sandbox pod spec (+ YAML of additional objects). */
  diff: string
  export: PlanExport
  decided_by: string | null
  decision_note: string | null
  decided_at: ISODateTime | null
  created_at: ISODateTime
  experiment_id: number | null
}

export interface DecisionIn {
  approver: string
  note?: string | null
}

export type Arm = 'baseline' | 'candidate'

export interface TrialOut {
  index: number
  arm: Arm
  warmup: boolean
  status: 'ok' | 'failed'
  pod_name: string | null
  startup_id: number | null
  total_s: number | null
  phases: Record<string, number>
  /** e.g. {ready, restarts, smoke_ok, smoke} */
  guardrails: Record<string, unknown>
  prep_s: number | null
  error: string | null
}

export interface ArmSummary {
  trials: number
  succeeded: number
  failed: number
  median_s: number | null
  min_s: number | null
  max_s: number | null
  phase_median_s: Record<string, number>
  prep_median_s: number | null
}

export type Verdict = 'improved' | 'no_change' | 'regressed' | 'failed'

export interface ExperimentComparison {
  baseline: ArmSummary
  candidate: ArmSummary
  delta_s: number | null
  delta_pct: number | null
  threshold_s: number | null
  verdict: Verdict
  reason: string
}

export interface ExperimentOut {
  id: number
  plan_id: number
  incident_id: number
  action: string
  action_note: string
  status: 'running' | 'completed' | 'failed' | 'aborted'
  trials_per_arm: number
  /** 1 warm-up + 2 x trials_per_arm */
  planned_trials: number
  node: string | null
  /** One-off prep costs, e.g. {"<job>-model-prefetch_s": 41.2} */
  prep: Record<string, number>
  /** Empty object until the experiment finishes. */
  comparison: Partial<ExperimentComparison>
  verdict: Verdict | null
  error: string | null
  started_at: ISODateTime
  finished_at: ISODateTime | null
  trials: TrialOut[]
}

export interface AuditOut {
  id: number
  at: ISODateTime
  actor: string
  event: string
  target: string | null
  details: Record<string, unknown>
}

export interface LlmUsageOut {
  purpose: string
  model: string
  calls: number
  errors: number
  input_tokens: number
  output_tokens: number
  avg_latency_ms: number
}

export interface EvalResult {
  scenario: string
  incident_id?: number
  expected: string
  category: string | null
  correct: boolean
  source: string | null
  confidence?: string | null
  citations_valid: boolean
  validation_errors?: string[]
  llm_error?: string | null
  tool_calls: number
  llm_calls?: number
  input_tokens: number | null
  output_tokens: number | null
  seconds: number
  summary?: string
}

/** Contents of the newest backend/evals/results/*.json */
export interface LatestEval {
  mode?: string
  model?: string
  prompt_version?: string
  file?: string
  results: EvalResult[]
}

export interface LlmOpsOut {
  usage: LlmUsageOut[]
  latest_eval: LatestEval | null
}
