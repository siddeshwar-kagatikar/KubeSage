import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useIncidentPlans, useProposePlan } from '../api/hooks'
import { PlanDetails, PlanStatusBadge } from './Plan'
import { ErrorState, QueryView } from './QueryState'
import Section from './Section'
import { formatRelative } from './format'

/** Incident-page section: propose a fix plan, review it, approve/reject. */
export default function OptimisationPanel({ incidentId }: { incidentId: number }) {
  const plans = useIncidentPlans(incidentId)
  const propose = useProposePlan(incidentId)
  const noAction = propose.error instanceof ApiError && propose.error.status === 422

  const action = (
    <button
      type="button"
      onClick={() => propose.mutate()}
      disabled={propose.isPending}
      aria-busy={propose.isPending || undefined}
      className="inline-flex items-center gap-2 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-surface hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {propose.isPending && (
        <span aria-hidden="true" className="size-3 animate-spin rounded-full border-2 border-surface/40 border-t-surface" />
      )}
      {propose.isPending ? 'Proposing…' : 'Propose fix'}
    </button>
  )

  return (
    <Section id="optimisation" title="Optimisation" actions={action}>
      <div className="space-y-4">
        <div aria-live="polite">
          {propose.isPending && (
            <p role="status" className="text-sm text-muted">
              Choosing an action and dry-running it against Kubernetes. This can take up to a minute and a half.
            </p>
          )}
          {noAction && propose.error instanceof ApiError && (
            <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-sm">
              <span className="font-medium">No automated action. </span>
              {propose.error.detail}
            </p>
          )}
          {propose.isError && !noAction && (
            <ErrorState
              error={
                propose.error instanceof ApiError && propose.error.status === 502
                  ? new Error(`Kubernetes error: ${propose.error.detail}`)
                  : propose.error
              }
              onRetry={() => propose.mutate()}
            />
          )}
        </div>

        <QueryView
          query={plans}
          loadingLabel="Loading plans…"
          empty="No fix proposed yet. Propose fix picks a remediation for the diagnosed category, checks it against policy and prepares a sandbox experiment for your approval."
        >
          {(data) => (
            <ol className="space-y-3">
              {data.map((p, i) => (
                <li key={p.id}>
                  <details open={i === 0} className="rounded-md border border-line">
                    <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
                      <span className="font-medium">
                        Plan #{p.id}: {p.action_title}
                      </span>
                      <PlanStatusBadge status={p.status} />
                      <span className="ml-auto text-xs text-muted">{formatRelative(p.created_at)}</span>
                    </summary>
                    <div className="space-y-3 border-t border-line p-3">
                      <p className="text-xs">
                        <Link to={`/plans/${p.id}`}>Open plan page</Link>
                      </p>
                      <PlanDetails plan={p} />
                    </div>
                  </details>
                </li>
              ))}
            </ol>
          )}
        </QueryView>
      </div>
    </Section>
  )
}
