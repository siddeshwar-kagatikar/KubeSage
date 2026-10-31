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
