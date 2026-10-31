import { formatSignal, humaniseKey } from './format'

/** Flattens nested objects into dotted keys, e.g. readiness_probe.period_s. */
function flatten(obj: Record<string, unknown>, prefix = ''): [string, unknown][] {
  return Object.entries(obj).flatMap(([k, v]): [string, unknown][] => {
    const key = prefix ? `${prefix}.${k}` : k
    return v && typeof v === 'object' && !Array.isArray(v) ? flatten(v as Record<string, unknown>, key) : [[key, v]]
  })
}

/** Simple two-column table for free-form signals / evidence. */
export default function KeyValueTable({
  data,
  caption,
  emptyText = 'No values recorded.',
}: {
  data: Record<string, unknown>
  caption: string
  emptyText?: string
}) {
  const rows = flatten(data).sort(([a], [b]) => a.localeCompare(b))
  if (rows.length === 0) return <p className="text-sm text-muted">{emptyText}</p>
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">{caption}</caption>
      <thead className="sr-only">
        <tr>
          <th scope="col">Name</th>
          <th scope="col">Value</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-line">
        {rows.map(([k, v]) => {
          const leaf = k.split('.').pop() ?? k
          return (
            <tr key={k}>
              <th scope="row" className="py-1.5 pr-4 text-left font-normal text-muted">
                <span title={k}>{humaniseKey(k)}</span>
              </th>
              <td className="py-1.5 text-right font-mono text-xs tabular break-all" title={`${k} = ${String(v)}`}>
                {formatSignal(leaf, v)}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
