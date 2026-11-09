import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useExperiment } from '../api/hooks'
import type { ExperimentOut } from '../api/types'
import {
  AbortForm,
  ArmBars,
  ExperimentProgress,
  ExperimentStatusBadge,
  PhaseMedians,
  PrepCosts,
  TrialsTable,
  VerdictBadge,
} from '../components/Experiment'
import { ErrorState, Loading } from '../components/QueryState'
import Section, { Stat } from '../components/Section'
import { formatDateTime, formatSeconds } from '../components/format'
import NotFoundPage from './NotFoundPage'
import { useNumericParam } from './useNumericParam'

export default function ExperimentPage() {
  const id = useNumericParam()
  const exp = useExperiment(id)

  if (!Number.isFinite(id)) return <NotFoundPage what="Experiment" />
  if (exp.isPending) return <Loading label="Loading experiment…" />
  if (!exp.data) {
    if (exp.error instanceof ApiError && exp.error.status === 404) return <NotFoundPage what="Experiment" />
    return <ErrorState error={exp.error} onRetry={() => void exp.refetch()} />
  }
  return <ExperimentView e={exp.data} />
}

function ExperimentView({ e }: { e: ExperimentOut }) {
  const c = e.comparison
  const duration = e.finished_at ? (new Date(e.finished_at).getTime() - new Date(e.started_at).getTime()) / 1000 : null

  return (
    <div className="space-y-6">
      <header className="space-y-4">
        <nav aria-label="Breadcrumb" className="text-sm text-muted">
          <Link to="/experiments">Experiments</Link> <span aria-hidden="true">/</span>{' '}
          <Link to={`/incidents/${e.incident_id}`}>Incident #{e.incident_id}</Link> <span aria-hidden="true">/</span>{' '}
          <Link to={`/plans/${e.plan_id}`}>Plan #{e.plan_id}</Link> <span aria-hidden="true">/</span>{' '}
          <span>Experiment #{e.id}</span>
        </nav>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-semibold">Experiment #{e.id}</h1>
          <ExperimentStatusBadge status={e.status} />
          <VerdictBadge verdict={e.verdict} />
          <code className="text-xs text-muted">{e.action}</code>
        </div>
        <dl className="grid grid-cols-2 gap-4 rounded-lg border border-line bg-surface p-4 md:grid-cols-5">
          <Stat label="Trials per arm" value={e.trials_per_arm} />
          <Stat label="Node" value={<span className="font-mono text-xs">{e.node ?? '—'}</span>} />
          <Stat label="Started" value={formatDateTime(e.started_at)} />
          <Stat label="Finished" value={formatDateTime(e.finished_at)} />
          <Stat label="Duration" value={formatSeconds(duration)} />
        </dl>
      </header>

      <Section id="protocol" title="Progress">
        <div className="space-y-3 text-sm" aria-live="polite">
          <ExperimentProgress e={e} />
          <p className="text-muted">
            Protocol: 1 warm-up trial (baseline, discarded), then alternating baseline and candidate trials in a sandbox
            copy of the workload. Each trial must become Ready, pass a smoke request and not restart.
          </p>
          {e.status === 'running' && <AbortForm e={e} />}
          {e.error && <ErrorState error={new Error(e.error)} />}
        </div>
      </Section>

      <Section id="result" title="Result">
        <div className="space-y-5">
          {c.verdict ? (
            <p className="flex flex-wrap items-center gap-2 text-sm">
              <VerdictBadge verdict={c.verdict} />
              <span>{c.reason}</span>
              {c.delta_s != null && (
                <span className="text-muted tabular">
                  (baseline − candidate {formatSeconds(c.delta_s)}
                  {c.delta_pct != null && <>, {c.delta_pct}%</>}; threshold {formatSeconds(c.threshold_s)})
                </span>
              )}
            </p>
          ) : (
            <p className="text-sm text-muted">
              {e.status === 'running' ? 'The comparison appears when all trials have finished.' : 'No comparison available.'}
            </p>
          )}
          <div className="space-y-1 rounded-md border border-line bg-surface-2 px-3 py-2 text-sm">
            {e.action_note && <p>{e.action_note}</p>}
            <p className="text-muted">Small samples: demonstration, not statistical significance.</p>
          </div>
          {c.baseline && c.candidate && (
            <>
              <div>
                <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Total startup</h3>
                <ArmBars baseline={c.baseline} candidate={c.candidate} />
              </div>
              <div>
                <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Median per phase</h3>
                <PhaseMedians baseline={c.baseline} candidate={c.candidate} />
              </div>
            </>
          )}
          <div>
            <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Preparation costs</h3>
            <PrepCosts e={e} />
          </div>
        </div>
      </Section>

      <Section id="trials" title={`Trials (${e.trials.length})`}>
        <div className="-m-4">
          <TrialsTable trials={e.trials} />
        </div>
      </Section>
    </div>
  )
}
