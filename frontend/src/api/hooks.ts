import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import type {
  HealthOut,
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
}

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
