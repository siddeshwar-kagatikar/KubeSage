import { Link } from 'react-router-dom'
import { useExperiments } from '../api/hooks'
import { ExperimentStatusBadge, VerdictBadge } from '../components/Experiment'
import { QueryView } from '../components/QueryState'
import Section, { td, th } from '../components/Section'
import { formatRelative, formatSeconds } from '../components/format'

export default function ExperimentsPage() {
  const experiments = useExperiments()
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Experiments</h1>
      <Section id="experiments" title="Before/after experiments">
        <QueryView
          query={experiments}
          loadingLabel="Loading experiments…"
          empty="No experiments yet. Approve a fix plan on an incident to run one."
        >
          {(data) => (
            <div className="-m-4 overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Experiments, newest first</caption>
                <thead className="border-b border-line">
                  <tr>
                    <th scope="col" className={th}>Experiment</th>
                    <th scope="col" className={th}>Action</th>
                    <th scope="col" className={th}>Status</th>
                    <th scope="col" className={`${th} text-right`}>Trials</th>
                    <th scope="col" className={`${th} text-right`}>Baseline → candidate</th>
                    <th scope="col" className={th}>Verdict</th>
                    <th scope="col" className={th}>Started</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {data.map((e) => (
                    <tr key={e.id} className="hover:bg-surface-2/60">
                      <th scope="row" className={`${td} text-left font-normal`}>
                        <Link to={`/experiments/${e.id}`} className="font-medium">
                          #{e.id}
                        </Link>
                        <div className="text-xs text-muted">
                          <Link to={`/incidents/${e.incident_id}`}>Incident #{e.incident_id}</Link>
                        </div>
                      </th>
                      <td className={td}>
                        <code className="text-xs">{e.action}</code>
                      </td>
                      <td className={td}>
                        <ExperimentStatusBadge status={e.status} />
                      </td>
                      <td className={`${td} text-right tabular`}>
                        {e.trials.length}/{e.planned_trials}
                      </td>
                      <td className={`${td} text-right tabular whitespace-nowrap`}>
                        {e.comparison.baseline
                          ? `${formatSeconds(e.comparison.baseline.median_s)} → ${formatSeconds(e.comparison.candidate?.median_s)}`
                          : '—'}
                      </td>
                      <td className={td}>
                        <VerdictBadge verdict={e.verdict} />
                      </td>
                      <td className={`${td} text-xs text-muted whitespace-nowrap`}>{formatRelative(e.started_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryView>
      </Section>
    </div>
  )
}
