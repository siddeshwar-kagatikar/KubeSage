import { Link } from 'react-router-dom'
import { useWorkload, useWorkloadStartups } from '../api/hooks'
import type { StartupSummary, WorkloadOut } from '../api/types'
import { ColdWarmBadge, StartupStatusBadge } from '../components/Badges'
import CategoryBadge from '../components/CategoryBadge'
import { ErrorState, Loading, QueryView } from '../components/QueryState'
import Section, { Stat, td, th } from '../components/Section'
import SloBar from '../components/SloBar'
import SloEditor from '../components/SloEditor'
import { formatDateTime, formatRelative, formatSeconds } from '../components/format'
import NotFoundPage from './NotFoundPage'
import { useNumericParam } from './useNumericParam'

const STARTUP_LIMIT = 50

export default function WorkloadPage() {
  const id = useNumericParam()
  const workload = useWorkload(id)
  const startups = useWorkloadStartups(id, STARTUP_LIMIT)

  if (!Number.isFinite(id)) return <NotFoundPage what="Workload" />
  if (workload.isPending) return <Loading label="Loading workload…" />
  if (workload.isError && !workload.data) return <ErrorState error={workload.error} onRetry={() => void workload.refetch()} />
  if (!workload.data) return <NotFoundPage what="Workload" />

  const w = workload.data
  return (
    <div className="space-y-6">
      <WorkloadHeader workload={w} />
      <Section id="startups" title={`Recent startups (latest ${STARTUP_LIMIT})`}>
        <QueryView query={startups} loadingLabel="Loading startups…" empty="No startups recorded yet — run a scenario.">
          {(data) => (
            <div className="-m-4">
              <StartupsTable startups={data} slo={w.slo_seconds} />
            </div>
          )}
        </QueryView>
      </Section>
    </div>
  )
}

function WorkloadHeader({ workload: w }: { workload: WorkloadOut }) {
  return (
    <header className="space-y-4">
      <nav aria-label="Breadcrumb" className="text-sm text-muted">
        <Link to="/">Overview</Link> <span aria-hidden="true">/</span> <span>Workload</span>
      </nav>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">
            <span className="text-muted">{w.namespace}/</span>
            {w.name}
          </h1>
          <p className="text-sm text-muted">{w.kind}</p>
        </div>
        <SloEditor key={w.slo_seconds} workloadId={w.id} slo={w.slo_seconds} />
      </div>
      <dl className="grid grid-cols-2 gap-4 rounded-lg border border-line bg-surface p-4 sm:grid-cols-4">
        <Stat label="Startups recorded" value={w.startup_count} />
        <Stat label="Median total" value={formatSeconds(w.median_total_s)} />
        <Stat
          label="Last startup"
          value={w.last_startup ? formatSeconds(w.last_startup.total_s) : '—'}
        />
        <Stat
          label="Open incidents"
          value={<span className={w.open_incidents > 0 ? 'text-danger' : ''}>{w.open_incidents}</span>}
        />
      </dl>
    </header>
  )
}

function StartupsTable({ startups, slo }: { startups: StartupSummary[]; slo: number }) {
  const scaleMax = Math.max(slo, ...startups.map((s) => s.total_s ?? 0)) * 1.05
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Recent startups, newest first</caption>
        <thead className="border-b border-line">
          <tr>
            <th scope="col" className={th}>Time</th>
            <th scope="col" className={th}>Pod</th>
            <th scope="col" className={th}>Total vs SLO</th>
            <th scope="col" className={th}>Start</th>
            <th scope="col" className={th}>Top category</th>
            <th scope="col" className={th}>
              <span className="sr-only">Links</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {startups.map((s) => (
            <tr key={s.id} className="hover:bg-surface-2/60">
              <td className={`${td} whitespace-nowrap`}>
                <time dateTime={s.created_at} title={formatDateTime(s.created_at)}>
                  {formatRelative(s.created_at)}
                </time>
              </td>
              <th scope="row" className={`${td} text-left font-normal`}>
                <span className="font-mono text-xs break-all">{s.pod_name}</span>
              </th>
              <td className={td}>
                <SloBar total={s.total_s} slo={slo} scaleMax={scaleMax} />
              </td>
              <td className={td}>
                <div className="flex gap-1.5">
                  <ColdWarmBadge cold={s.cold} />
                  {s.status !== 'ready' && <StartupStatusBadge status={s.status} />}
                </div>
              </td>
              <td className={td}>
                <CategoryBadge category={s.top_category} />
              </td>
              <td className={`${td} whitespace-nowrap text-right`}>
                <Link to={`/startups/${s.id}`} aria-label={`View waterfall for ${s.pod_name}`}>
                  Waterfall
                </Link>
                {s.incident_id != null && (
                  <>
                    {' · '}
                    <Link to={`/incidents/${s.incident_id}`} className="text-danger">
                      Incident #{s.incident_id}
                    </Link>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
