import { Link } from 'react-router-dom'
import { useHealth, useIncidents, useWorkloads } from '../api/hooks'
import type { HealthOut, IncidentOut, WorkloadOut } from '../api/types'
import { ColdWarmBadge, IncidentStatusBadge, StartupStatusBadge } from '../components/Badges'
import CategoryBadge from '../components/CategoryBadge'
import { ErrorState, Loading, QueryView } from '../components/QueryState'
import Section, { td, th } from '../components/Section'
import StatusDot from '../components/StatusDot'
import { formatRelative, formatSeconds } from '../components/format'

const RECENT_INCIDENTS = 10

export default function OverviewPage() {
  const workloads = useWorkloads()
  const incidents = useIncidents()

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Overview</h1>
      <HealthStrip />

      <Section id="workloads" title="Workloads">
        <QueryView query={workloads} loadingLabel="Loading workloads…" empty="No startups recorded yet — run a scenario.">
          {(data) => (
            <div className="-m-4">
              <WorkloadsTable workloads={data} />
            </div>
          )}
        </QueryView>
      </Section>

      <Section id="incidents" title="Recent incidents">
        <QueryView query={incidents} loadingLabel="Loading incidents…" empty="No incidents raised. Every startup is within its SLO.">
          {(data) => <IncidentList incidents={data.slice(0, RECENT_INCIDENTS)} />}
        </QueryView>
      </Section>
    </div>
  )
}

function HealthStrip() {
  const health = useHealth()
  if (health.isPending) return <Loading label="Checking platform health…" />
  if (!health.data) return <ErrorState error={health.error} onRetry={() => void health.refetch()} />
  const h: HealthOut = health.data
  const items: [string, boolean][] = [
    ['Database', h.database],
    ['Kubernetes', h.kubernetes],
    ['Jaeger', h.jaeger],
    ['Prometheus', h.prometheus],
    ['Watchers', h.watchers_running],
  ]
  return (
    <section aria-label="Platform health" className="rounded-lg border border-line bg-surface px-4 py-3">
      <ul className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {items.map(([label, ok]) => (
          <li key={label}>
            <StatusDot status={ok ? 'ok' : 'down'} label={label} showStatusText={!ok} />
          </li>
        ))}
        {h.clock_offset_s != null && (
          <li className="text-sm text-muted" title="Offset between the KubeSage host clock and the cluster clock">
            Clock offset{' '}
            <span className={`tabular ${Math.abs(h.clock_offset_s) > 1 ? 'font-medium text-warn' : 'text-fg'}`}>
              {h.clock_offset_s >= 0 ? '+' : ''}
              {formatSeconds(h.clock_offset_s)}
            </span>
          </li>
        )}
        {health.isError && <li className="text-sm text-danger">Health check failing; showing last known state.</li>}
      </ul>
    </section>
  )
}

function WorkloadsTable({ workloads }: { workloads: WorkloadOut[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Workloads with startup statistics</caption>
        <thead className="border-b border-line">
          <tr>
            <th scope="col" className={th}>Workload</th>
            <th scope="col" className={`${th} text-right`}>SLO</th>
            <th scope="col" className={`${th} text-right`}>Startups</th>
            <th scope="col" className={`${th} text-right`}>Median total</th>
            <th scope="col" className={th}>Last startup</th>
            <th scope="col" className={`${th} text-right`}>Open incidents</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {workloads.map((w) => {
            const last = w.last_startup
            const lastOver = last?.total_s != null && last.total_s > w.slo_seconds
            return (
              <tr key={w.id} className="hover:bg-surface-2/60">
                <th scope="row" className={`${td} text-left font-normal`}>
                  <Link to={`/workloads/${w.id}`} className="font-medium">
                    <span className="text-muted">{w.namespace}/</span>
                    {w.name}
                  </Link>
                  <div className="text-xs text-muted">{w.kind}</div>
                </th>
                <td className={`${td} text-right tabular`}>{formatSeconds(w.slo_seconds)}</td>
                <td className={`${td} text-right tabular`}>{w.startup_count}</td>
                <td className={`${td} text-right tabular`}>{formatSeconds(w.median_total_s)}</td>
                <td className={td}>
                  {last ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <Link to={`/startups/${last.id}`} className={`tabular ${lastOver ? 'font-medium text-danger' : ''}`}>
                        {last.total_s != null ? formatSeconds(last.total_s) : 'in progress'}
                        {lastOver && <span className="sr-only"> (over SLO)</span>}
                      </Link>
                      {lastOver && <span aria-hidden="true" className="text-xs text-danger">over SLO</span>}
                      <ColdWarmBadge cold={last.cold} />
                      <StartupStatusBadge status={last.status} />
                      <span className="text-xs text-muted">{formatRelative(last.created_at)}</span>
                    </div>
                  ) : (
                    <span className="text-muted">No startups yet</span>
                  )}
                </td>
                <td className={`${td} text-right tabular`}>
                  {w.open_incidents > 0 ? (
                    <span className="font-medium text-danger">{w.open_incidents}</span>
                  ) : (
                    <span className="text-muted">0</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function IncidentList({ incidents }: { incidents: IncidentOut[] }) {
  return (
    <ul className="divide-y divide-line">
      {incidents.map((inc) => (
        <li key={inc.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 first:pt-0 last:pb-0">
          <IncidentStatusBadge status={inc.status} />
          <Link to={`/incidents/${inc.id}`} className="font-medium">
            Incident #{inc.id}: {inc.namespace}/{inc.workload_name}
          </Link>
          <span className="text-sm tabular">
            {formatSeconds(inc.total_s)}{' '}
            <span className="text-muted">
              {inc.breach_reason === 'slo' ? 'vs SLO' : 'vs regression threshold'} {formatSeconds(inc.threshold_s)}
            </span>
          </span>
          <CategoryBadge category={inc.top_category} />
          <time dateTime={inc.detected_at} className="ml-auto text-xs text-muted">
            {formatRelative(inc.detected_at)}
          </time>
        </li>
      ))}
    </ul>
  )
}
