import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from './client'
import type {
  ActionPlanOut,
  AuditOut,
  DecisionIn,
  ExperimentOut,
  LlmOpsOut,
  DiagnosisRunOut,
  HealthOut,
  LlmCallOut,
  IncidentOut,
  IncidentStatus,
  SloUpdate,
  StartupDetail,
  StartupSummary,
  WorkloadOut,
} from './types'

/** Lists poll so new startups and incidents appear live. */
export const LIST_POLL_MS = 5_000

export const queryKeys = {
  health: ['health'] as const,
  workloads: ['workloads'] as const,
  workloadStartups: (id: number, limit: number) => ['workloads', id, 'startups', limit] as const,
  startup: (id: number) => ['startups', id] as const,
  incidents: (status?: IncidentStatus) => ['incidents', { status: status ?? null }] as const,
  incident: (id: number) => ['incident', id] as const,
  diagnosis: (incidentId: number) => ['diagnosis', incidentId] as const,
  llmCalls: (limit: number) => ['llm', 'calls', limit] as const,
  incidentPlans: (incidentId: number) => ['plans', 'incident', incidentId] as const,
  plan: (id: number) => ['plans', id] as const,
  experiments: ['experiments'] as const,
  experiment: (id: number) => ['experiments', id] as const,
  audit: (limit: number) => ['audit', limit] as const,
  llmops: ['llmops'] as const,
}

/** Poll interval while a diagnosis run is in progress. */
export const DIAGNOSIS_POLL_MS = 2_000

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => api.get<HealthOut>('/health'),
    refetchInterval: LIST_POLL_MS,
  })
}

export function useWorkloads() {
  return useQuery({
    queryKey: queryKeys.workloads,
    queryFn: () => api.get<WorkloadOut[]>('/workloads'),
    refetchInterval: LIST_POLL_MS,
  })
}

/** There is no single-workload endpoint; derive it from the list. */
export function useWorkload(id: number) {
  const q = useWorkloads()
  return { ...q, data: q.data?.find((w) => w.id === id) }
}

export function useWorkloadStartups(id: number, limit = 50) {
  return useQuery({
    queryKey: queryKeys.workloadStartups(id, limit),
    queryFn: () => api.get<StartupSummary[]>(`/workloads/${id}/startups?limit=${limit}`),
    refetchInterval: LIST_POLL_MS,
    enabled: Number.isFinite(id),
  })
}

export function useStartup(id: number) {
  return useQuery({
    queryKey: queryKeys.startup(id),
    queryFn: () => api.get<StartupDetail>(`/startups/${id}`),
    enabled: Number.isFinite(id),
    // Keep polling while the pod is still starting; stop once it is ready.
    refetchInterval: (query) => (query.state.data?.status === 'ready' ? false : LIST_POLL_MS),
  })
}

export function useIncidents(status?: IncidentStatus) {
  return useQuery({
    queryKey: queryKeys.incidents(status),
    queryFn: () => api.get<IncidentOut[]>(status ? `/incidents?status=${status}` : '/incidents'),
    refetchInterval: LIST_POLL_MS,
  })
}

export function useIncident(id: number) {
  return useQuery({
    queryKey: queryKeys.incident(id),
    queryFn: () => api.get<IncidentOut>(`/incidents/${id}`),
    enabled: Number.isFinite(id),
  })
}

export function useUpdateSlo(workloadId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SloUpdate) => api.put<WorkloadOut>(`/workloads/${workloadId}/slo`, body),
    onSuccess: (updated) => {
      qc.setQueryData<WorkloadOut[]>(queryKeys.workloads, (prev) =>
        prev?.map((w) => (w.id === updated.id ? updated : w)),
      )
      void qc.invalidateQueries({ queryKey: queryKeys.workloads })
      void qc.invalidateQueries({ queryKey: ['workloads', workloadId, 'startups'] })
    },
  })
}

// ---- Phase 3: AI diagnosis ----

/**
 * Latest diagnosis run for an incident. Resolves to `null` when none exists yet (API 404).
 * Polls every 2 s while the run is `running`; when it finishes, refreshes the incident
 * (its status moves to "diagnosed") and the lists that show incident status.
 */
export function useDiagnosis(incidentId: number) {
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: queryKeys.diagnosis(incidentId),
    queryFn: async (): Promise<DiagnosisRunOut | null> => {
      try {
        return await api.get<DiagnosisRunOut>(`/incidents/${incidentId}/diagnosis`)
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null
        throw e
      }
    },
    enabled: Number.isFinite(incidentId),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? DIAGNOSIS_POLL_MS : false),
  })

  const status = query.data?.status
  const prev = useRef(status)
  useEffect(() => {
    if (prev.current === 'running' && status && status !== 'running') {
      void qc.invalidateQueries({ queryKey: queryKeys.incident(incidentId) })
      void qc.invalidateQueries({ queryKey: ['incidents'] })
      void qc.invalidateQueries({ queryKey: queryKeys.workloads })
    }
    prev.current = status
  }, [status, incidentId, qc])

  return query
}

/**
 * Starts a diagnosis run (202 → running). On 409 (already running) the caller shows the
 * message; either way we refetch so polling picks up the in-flight run.
 */
export function useRunDiagnosis(incidentId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<DiagnosisRunOut>(`/incidents/${incidentId}/diagnose`),
    onSuccess: (run) => {
      qc.setQueryData(queryKeys.diagnosis(incidentId), run)
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        void qc.invalidateQueries({ queryKey: queryKeys.diagnosis(incidentId) })
      }
    },
  })
}

/** Recent LLM calls (for the Phase 6 observability page). */
export function useLlmCalls(limit = 100) {
  return useQuery({
    queryKey: queryKeys.llmCalls(limit),
    queryFn: () => api.get<LlmCallOut[]>(`/llm/calls?limit=${limit}`),
    refetchInterval: LIST_POLL_MS,
  })
}

// ---- Phases 4–6: fix plans, experiments, audit, LLMOps ----

/** Poll interval while an experiment is running. */
export const EXPERIMENT_POLL_MS = 3_000

const hasRunning = (plans: ActionPlanOut[] | undefined) => plans?.some((p) => p.status === 'running') ?? false

export function useIncidentPlans(incidentId: number) {
  return useQuery({
    queryKey: queryKeys.incidentPlans(incidentId),
    queryFn: () => api.get<ActionPlanOut[]>(`/incidents/${incidentId}/plans`),
    enabled: Number.isFinite(incidentId),
    refetchInterval: (q) => (hasRunning(q.state.data) ? LIST_POLL_MS : false),
  })
}

export function usePlan(id: number) {
  return useQuery({
    queryKey: queryKeys.plan(id),
    queryFn: () => api.get<ActionPlanOut>(`/plans/${id}`),
    enabled: Number.isFinite(id),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? LIST_POLL_MS : false),
  })
}

/** Everything that shows plan or experiment state. */
function invalidatePlanState(qc: ReturnType<typeof useQueryClient>, incidentId: number) {
  void qc.invalidateQueries({ queryKey: ['plans'] })
  void qc.invalidateQueries({ queryKey: queryKeys.experiments })
  void qc.invalidateQueries({ queryKey: queryKeys.incident(incidentId) })
  void qc.invalidateQueries({ queryKey: ['audit'] })
}

/** POST /incidents/{id}/plans — one LLM call + dry-run, 5–90 s. 422 = no automated action. */
export function useProposePlan(incidentId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<ActionPlanOut>(`/incidents/${incidentId}/plans`),
    onSuccess: () => invalidatePlanState(qc, incidentId),
  })
}

export function useApprovePlan(plan: ActionPlanOut) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: DecisionIn) => api.post<ExperimentOut>(`/plans/${plan.id}/approve`, body),
    onSuccess: (exp) => {
      qc.setQueryData(queryKeys.experiment(exp.id), exp)
      invalidatePlanState(qc, plan.incident_id)
    },
  })
}

export function useRejectPlan(plan: ActionPlanOut) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: DecisionIn) => api.post<ActionPlanOut>(`/plans/${plan.id}/reject`, body),
    onSuccess: (updated) => {
      qc.setQueryData(queryKeys.plan(updated.id), updated)
      invalidatePlanState(qc, plan.incident_id)
    },
  })
}

export function useExperiments() {
  return useQuery({
    queryKey: queryKeys.experiments,
    queryFn: () => api.get<ExperimentOut[]>('/experiments'),
    refetchInterval: LIST_POLL_MS,
  })
}

export function useExperiment(id: number) {
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: queryKeys.experiment(id),
    queryFn: () => api.get<ExperimentOut>(`/experiments/${id}`),
    enabled: Number.isFinite(id),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? EXPERIMENT_POLL_MS : false),
  })

  // When the run finishes, the plan and incident change status too.
  const status = query.data?.status
  const incidentId = query.data?.incident_id
  const prev = useRef(status)
  useEffect(() => {
    if (prev.current === 'running' && status && status !== 'running' && incidentId != null)
      invalidatePlanState(qc, incidentId)
    prev.current = status
  }, [status, incidentId, qc])

  return query
}

export function useAbortExperiment(experiment: ExperimentOut) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: DecisionIn) => api.post<unknown>(`/experiments/${experiment.id}/abort`, body),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.experiment(experiment.id) })
      invalidatePlanState(qc, experiment.incident_id)
    },
  })
}

export function useAudit(limit = 200) {
  return useQuery({
    queryKey: queryKeys.audit(limit),
    queryFn: () => api.get<AuditOut[]>(`/audit?limit=${limit}`),
    refetchInterval: LIST_POLL_MS,
  })
}

export function useLlmOps() {
  return useQuery({
    queryKey: queryKeys.llmops,
    queryFn: () => api.get<LlmOpsOut>('/llmops/summary'),
    refetchInterval: LIST_POLL_MS,
  })
}
