import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from './client'
import type {
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
