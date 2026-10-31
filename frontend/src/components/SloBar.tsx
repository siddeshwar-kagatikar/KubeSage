import { formatSeconds } from './format'

interface Props {
  total: number | null
  slo: number
  /** Shared scale so bars in a list are comparable. */
  scaleMax: number
}

/** Horizontal total-vs-SLO bar with an SLO tick and a text label. */
export default function SloBar({ total, slo, scaleMax }: Props) {
  if (total == null) return <span className="text-sm text-muted">in progress</span>
  const max = Math.max(scaleMax, total, slo, 0.001)
  const over = total > slo
  const pct = Math.min(100, (total / max) * 100)
  const sloPct = Math.min(100, (slo / max) * 100)
  const label = `${formatSeconds(total)} against an SLO of ${formatSeconds(slo)}${over ? ', over SLO' : ''}`
  return (
    <div className="flex items-center gap-2">
      <div role="img" aria-label={label} className="relative h-2.5 w-28 shrink-0 rounded-sm bg-track sm:w-40">
        <div
          className={`absolute inset-y-0 left-0 rounded-sm ${over ? 'bg-danger' : 'bg-accent'}`}
          style={{ width: `${pct}%` }}
        />
        <div
          aria-hidden="true"
          title={`SLO ${formatSeconds(slo)}`}
          className="absolute -inset-y-1 w-0.5 bg-fg"
          style={{ left: `calc(${sloPct}% - 1px)` }}
        />
      </div>
      <span aria-hidden="true" className={`text-sm tabular whitespace-nowrap ${over ? 'font-medium text-danger' : ''}`}>
        {formatSeconds(total)}
        {over && <span className="ml-1 text-xs">over</span>}
      </span>
    </div>
  )
}
