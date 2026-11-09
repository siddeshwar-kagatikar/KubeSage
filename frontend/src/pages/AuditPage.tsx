import { useAudit } from '../api/hooks'
import { QueryView } from '../components/QueryState'
import Section, { td, th } from '../components/Section'
import { formatDateTime } from '../components/format'

const LIMIT = 200

export default function AuditPage() {
  const audit = useAudit(LIMIT)
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Audit log</h1>
      <Section id="audit" title={`Latest ${LIMIT} events`}>
        <QueryView query={audit} loadingLabel="Loading audit log…" empty="No audit events recorded yet.">
          {(data) => (
            <div className="-m-4 overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Audit events, newest first</caption>
                <thead className="border-b border-line">
                  <tr>
                    <th scope="col" className={th}>At</th>
                    <th scope="col" className={th}>Actor</th>
                    <th scope="col" className={th}>Event</th>
                    <th scope="col" className={th}>Target</th>
                    <th scope="col" className={th}>Details</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {data.map((a) => (
                    <tr key={a.id} className="align-top">
                      <td className={`${td} text-xs whitespace-nowrap tabular`}>
                        <time dateTime={a.at}>{formatDateTime(a.at)}</time>
                      </td>
                      <td className={td}>{a.actor}</td>
                      <th scope="row" className={`${td} text-left font-normal`}>
                        <code className="text-xs">{a.event}</code>
                      </th>
                      <td className={`${td} max-w-xs font-mono text-xs break-words`}>{a.target ?? '—'}</td>
                      <td className={td}>
                        {Object.keys(a.details).length === 0 ? (
                          <span className="text-muted">—</span>
                        ) : (
                          <details>
                            <summary className="cursor-pointer text-xs text-muted hover:text-fg">
                              {Object.keys(a.details).length} field{Object.keys(a.details).length === 1 ? '' : 's'}
                            </summary>
                            <pre className="mt-1 max-h-64 max-w-xl overflow-auto rounded border border-line bg-surface-2 p-2 font-mono text-xs whitespace-pre-wrap break-words">
                              {JSON.stringify(a.details, null, 2)}
                            </pre>
                          </details>
                        )}
                      </td>
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
