import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useIncident } from '../api/hooks'
import type { IncidentOut } from '../api/types'
import { IncidentStatusBadge } from '../components/Badges'
import CandidatesList from '../components/CandidatesList'
import CategoryBadge from '../components/CategoryBadge'
import DiagnosisPanel from '../components/DiagnosisPanel'
import OptimisationPanel from '../components/OptimisationPanel'
import { ErrorState, Loading } from '../components/QueryState'
import Section, { Stat } from '../components/Section'
import { formatDateTime, formatPercent, formatSeconds } from '../components/format'
import NotFoundPage from './NotFoundPage'
import { useNumericParam } from './useNumericParam'

export default function IncidentPage() {
  const id = useNumericParam()
  const incident = useIncident(id)

  if (!Number.isFinite(id)) return <NotFoundPage what="Incident" />
  if (incident.isPending) return <Loading label="Loading incident…" />
  if (!incident.data) {
    if (incident.error instanceof ApiError && incident.error.status === 404) return <NotFoundPage what="Incident" />
    return <ErrorState error={incident.error} onRetry={() => void incident.refetch()} />
  }
  return <IncidentView incident={incident.data} />
}

function BreachExplanation({ incident: i }: { incident: IncidentOut }) {
  const excess = i.total_s - i.threshold_s
  if (i.breach_reason === 'slo') {
    return (
      <p className="text-sm">
        Startup took <strong className="tabular">{formatSeconds(i.total_s)}</strong>, exceeding the workload SLO of{' '}
        <strong className="tabular">{formatSeconds(i.threshold_s)}</strong> by{' '}
        <strong className="tabular text-danger">{formatSeconds(excess)}</strong>.
      </p>
    )
  }
  const vsBaseline = i.baseline_s ? i.total_s / i.baseline_s - 1 : null
  return (
    <p className="text-sm">
      Startup took <strong className="tabular">{formatSeconds(i.total_s)}</strong>, above the regression threshold of{' '}
      <strong className="tabular">{formatSeconds(i.threshold_s)}</strong>
      {i.baseline_s != null && (
        <>
          {' '}
          (baseline <strong className="tabular">{formatSeconds(i.baseline_s)}</strong>
          {vsBaseline != null && (
            <>
              , <strong className="tabular text-danger">+{formatPercent(vsBaseline)}</strong> slower
            </>
          )}
          )
        </>
      )}
      .
    </p>
  )
}

function IncidentView({ incident: i }: { incident: IncidentOut }) {
  return (
    <div className="space-y-6">
      <header className="space-y-4">
        <nav aria-label="Breadcrumb" className="text-sm text-muted">
          <Link to="/">Overview</Link> <span aria-hidden="true">/</span>{' '}
          <Link to={`/workloads/${i.workload_id}`}>
            {i.namespace}/{i.workload_name}
          </Link>{' '}
          <span aria-hidden="true">/</span> <span>Incident #{i.id}</span>
        </nav>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">Incident #{i.id}</h1>
          <IncidentStatusBadge status={i.status} />
          <span className="text-sm text-muted">
            {i.breach_reason === 'slo' ? 'SLO breach' : 'Startup regression'}
          </span>
        </div>
        <dl className="grid grid-cols-2 gap-4 rounded-lg border border-line bg-surface p-4 md:grid-cols-5">
          <Stat
            label="Workload"
            value={
              <Link to={`/workloads/${i.workload_id}`}>
                {i.namespace}/{i.workload_name}
              </Link>
            }
          />
          <Stat label="Pod" value={<span className="font-mono text-xs">{i.pod_name}</span>} />
          <Stat label="Detected" value={formatDateTime(i.detected_at)} />
          <Stat label="Total" value={<span className="text-danger">{formatSeconds(i.total_s)}</span>} />
          <Stat label="Top category" value={<CategoryBadge category={i.top_category} />} />
        </dl>
      </header>

      <Section
        id="breach"
        title="Why this incident was raised"
        actions={
          <Link to={`/startups/${i.startup_id}`} className="text-sm">
            View startup waterfall →
          </Link>
        }
      >
        <BreachExplanation incident={i} />
      </Section>

      <Section id="candidates" title="Detector candidates">
        <CandidatesList candidates={i.candidates} />
      </Section>

      <DiagnosisPanel incidentId={i.id} />

      <OptimisationPanel incidentId={i.id} />
    </div>
  )
}
