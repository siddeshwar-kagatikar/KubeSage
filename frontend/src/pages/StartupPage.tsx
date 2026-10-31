import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useStartup, useWorkload } from '../api/hooks'
import type { StartupDetail } from '../api/types'
import { BreachBadge, ColdWarmBadge, StartupStatusBadge } from '../components/Badges'
import CandidatesList from '../components/CandidatesList'
import CategoryBadge from '../components/CategoryBadge'
import KeyValueTable from '../components/KeyValueTable'
import { ErrorState, Loading } from '../components/QueryState'
import Section, { Stat } from '../components/Section'
import Waterfall from '../components/Waterfall'
import { formatDateTime, formatSeconds } from '../components/format'
import NotFoundPage from './NotFoundPage'
import { useNumericParam } from './useNumericParam'

export default function StartupPage() {
  const id = useNumericParam()
  const startup = useStartup(id)

  if (!Number.isFinite(id)) return <NotFoundPage what="Startup" />
  if (startup.isPending) return <Loading label="Loading startup…" />
  if (!startup.data) {
    if (startup.error instanceof ApiError && startup.error.status === 404) return <NotFoundPage what="Startup" />
    return <ErrorState error={startup.error} onRetry={() => void startup.refetch()} />
  }
  return <StartupView startup={startup.data} />
}

function StartupView({ startup: s }: { startup: StartupDetail }) {
  const workload = useWorkload(s.workload_id)
  const slo = workload.data?.slo_seconds ?? null
  const breached = s.total_s != null && slo != null ? s.total_s > slo : null

  return (
    <div className="space-y-6">
      <header className="space-y-4">
        <nav aria-label="Breadcrumb" className="text-sm text-muted">
          <Link to="/">Overview</Link> <span aria-hidden="true">/</span>{' '}
          <Link to={`/workloads/${s.workload_id}`}>
            {s.namespace}/{workload.data?.name ?? `workload ${s.workload_id}`}
          </Link>{' '}
          <span aria-hidden="true">/</span> <span>Startup #{s.id}</span>
        </nav>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-mono text-lg font-semibold break-all">{s.pod_name}</h1>
          <ColdWarmBadge cold={s.cold} />
          <StartupStatusBadge status={s.status} />
          <BreachBadge breached={breached} />
          {s.incident_id != null && (
            <Link to={`/incidents/${s.incident_id}`} className="text-sm text-danger">
              Incident #{s.incident_id}
            </Link>
          )}
        </div>
        <dl className="grid grid-cols-2 gap-4 rounded-lg border border-line bg-surface p-4 md:grid-cols-3 lg:grid-cols-6">
          <Stat
            label="Total"
            value={
              <span className={breached ? 'text-danger' : ''}>
                {s.total_s != null ? formatSeconds(s.total_s) : 'in progress'}
              </span>
            }
          />
          <Stat label="SLO" value={formatSeconds(slo)} />
          <Stat label="Top category" value={<CategoryBadge category={s.top_category} />} />
          <Stat label="Node" value={<span className="font-mono text-xs">{s.node ?? '—'}</span>} />
          <Stat label="Created" value={formatDateTime(s.created_at)} />
          <Stat label="Ready" value={formatDateTime(s.ready_at)} />
        </dl>
        <p className="text-sm">
          <span className="text-muted">Image </span>
          <span className="font-mono text-xs break-all">{s.image ?? '—'}</span>
        </p>
      </header>

      <Section
        id="waterfall"
        title="Startup waterfall"
        actions={
          !s.spans_found && (
            <span className="text-xs text-muted">No app spans found — app init and readiness shown combined</span>
          )
        }
      >
        <Waterfall phases={s.phases} createdAt={s.created_at} totalS={s.total_s} sloS={slo} />
      </Section>

      <div className="grid gap-6 lg:grid-cols-3">
        <Section id="candidates" title="Detector candidates" className="lg:col-span-2">
          <CandidatesList candidates={s.candidates} />
        </Section>
        <Section id="signals" title="Signals">
          <KeyValueTable data={s.signals} caption="Signals collected for this startup" emptyText="No signals collected." />
        </Section>
      </div>
    </div>
  )
}
