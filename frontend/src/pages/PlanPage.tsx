import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { usePlan } from '../api/hooks'
import { PlanDetails } from '../components/Plan'
import { ErrorState, Loading } from '../components/QueryState'
import Section from '../components/Section'
import { formatDateTime } from '../components/format'
import NotFoundPage from './NotFoundPage'
import { useNumericParam } from './useNumericParam'

export default function PlanPage() {
  const id = useNumericParam()
  const plan = usePlan(id)

  if (!Number.isFinite(id)) return <NotFoundPage what="Plan" />
  if (plan.isPending) return <Loading label="Loading plan…" />
  if (!plan.data) {
    if (plan.error instanceof ApiError && plan.error.status === 404) return <NotFoundPage what="Plan" />
    return <ErrorState error={plan.error} onRetry={() => void plan.refetch()} />
  }
  const p = plan.data
  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <nav aria-label="Breadcrumb" className="text-sm text-muted">
          <Link to="/">Overview</Link> <span aria-hidden="true">/</span>{' '}
          <Link to={`/incidents/${p.incident_id}`}>Incident #{p.incident_id}</Link> <span aria-hidden="true">/</span>{' '}
          <span>Plan #{p.id}</span>
        </nav>
        <h1 className="text-xl font-semibold">{p.action_title}</h1>
        <p className="text-sm text-muted">Proposed {formatDateTime(p.created_at)}</p>
      </header>
      <Section id="plan" title={`Plan #${p.id}`}>
        <PlanDetails plan={p} />
      </Section>
    </div>
  )
}
