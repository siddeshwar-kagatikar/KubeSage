import type { PhaseOut } from '../api/types'
import { EmptyState } from './QueryState'
import { formatSeconds, formatTick } from './format'
import { axisTicks, buildRows, phaseColour, phaseLabel } from './phases'

interface Props {
  phases: PhaseOut[]
  /** Pod creation time; all offsets are relative to this. */
  createdAt: string
  totalS: number | null
  /** Optional SLO, drawn as a vertical marker when it falls within range. */
  sloS?: number | null
}

const SOURCE_LABEL: Record<PhaseOut['source'], string> = {
  k8s: 'k8s',
  event: 'event',
  span: 'span',
}

/**
 * Startup waterfall: one row per phase, bar offset/width proportional to time since pod creation.
 * Rendered as a table so screen readers get phase, start, and duration as text; the bars are decorative.
 */
export default function Waterfall({ phases, createdAt, totalS, sloS }: Props) {
  if (phases.length === 0) return <EmptyState>No phases recorded for this startup yet.</EmptyState>

  const rows = buildRows(phases, createdAt)
  const lastEnd = Math.max(0, ...rows.map((r) => r.offset + r.duration))
  let rawMax = Math.max(lastEnd, totalS ?? 0, 0.1)
  // Include the SLO in range if it's reasonably close, so the marker is visible.
  if (sloS != null && sloS > rawMax && sloS <= rawMax * 1.5) rawMax = sloS
  const { step } = axisTicks(rawMax)
  const domain = Math.ceil(rawMax / step - 1e-9) * step
  const ticks = Array.from({ length: Math.round(domain / step) + 1 }, (_, i) => Number((i * step).toFixed(3)))
  const pct = (s: number) => (s / domain) * 100
  const showSlo = sloS != null && sloS <= domain
  const gridStyle = {
    backgroundImage: 'linear-gradient(to right, var(--line) 1px, transparent 1px)',
    backgroundSize: `${pct(step)}% 100%`,
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-sm">
        <caption className="sr-only">
          Startup phases. Offsets are seconds since the pod was created
          {totalS != null ? `; total startup ${formatSeconds(totalS)}` : ''}.
        </caption>
        <colgroup>
          <col className="w-56" />
          <col className="w-20" />
          <col className="w-20" />
          <col />
        </colgroup>
        <thead>
          <tr className="border-b border-line">
            <th scope="col" className="py-2 pr-3 text-left text-xs font-medium text-muted">
              Phase
            </th>
            <th scope="col" className="px-2 py-2 text-right text-xs font-medium text-muted">
              Start
            </th>
            <th scope="col" className="px-2 py-2 text-right text-xs font-medium text-muted">
              Duration
            </th>
            <th scope="col" className="py-2 pl-3 pr-10 text-left text-xs font-medium text-muted">
              <span className="sr-only">Timeline</span>
              <div aria-hidden="true" className="relative h-4">
                {ticks.map((t) => (
                  <span
                    key={t}
                    className="absolute top-0 -translate-x-1/2 tabular whitespace-nowrap"
                    style={{ left: `${pct(t)}%` }}
                  >
                    {formatTick(t)}
                  </span>
                ))}
              </div>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ phase, depth, offset, duration }, i) => {
            const colour = phaseColour(phase.name)
            const left = pct(offset)
            const width = pct(duration)
            const labelOnLeft = left + width > 78
            const isZero = duration === 0
            return (
              <tr
                key={`${phase.name}-${phase.start}-${i}`}
                className={`border-b border-line/60 last:border-0 ${depth ? 'bg-surface-2/50' : ''}`}
              >
                <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                  <div className={`flex items-center gap-2 ${depth ? 'pl-5' : ''}`}>
                    {depth === 1 && (
                      <span aria-hidden="true" className="-ml-3 text-muted">
                        ↳
                      </span>
                    )}
                    <span aria-hidden="true" className="size-2.5 shrink-0 rounded-sm" style={{ backgroundColor: colour }} />
                    <span className={depth ? 'text-[13px]' : 'font-medium'}>{phaseLabel(phase.name)}</span>
                    <span className="ml-auto rounded bg-surface-2 px-1 font-mono text-[10px] text-muted" title={`Source: ${phase.source}`}>
                      <span className="sr-only">source </span>
                      {SOURCE_LABEL[phase.source]}
                    </span>
                  </div>
                  {depth === 1 && <span className="sr-only">, child of {phaseLabel(phase.parent ?? '')}</span>}
                </th>
                <td className="px-2 py-1.5 text-right text-xs text-muted tabular whitespace-nowrap">
                  +{formatSeconds(offset)}
                </td>
                <td className="px-2 py-1.5 text-right tabular whitespace-nowrap">{formatSeconds(duration)}</td>
                <td className="py-1.5 pl-3 pr-10">
                  <div aria-hidden="true" className="relative h-5" style={gridStyle}>
                    {showSlo && (
                      <div className="absolute inset-y-[-6px] w-0 border-l border-dashed border-danger" style={{ left: `${pct(sloS)}%` }} />
                    )}
                    <div
                      title={`${phase.name}: +${formatSeconds(offset)}, ${formatSeconds(duration)}`}
                      className={`absolute top-1 h-3 rounded-[2px] ${isZero ? 'opacity-70' : ''}`}
                      style={{
                        left: `${left}%`,
                        width: isZero ? '2px' : `max(${width}%, 2px)`,
                        backgroundColor: colour,
                      }}
                    />
                    <span
                      className="absolute top-0 text-[11px] leading-5 text-muted tabular whitespace-nowrap"
                      style={
                        labelOnLeft
                          ? { right: `calc(${100 - left}% + 4px)` }
                          : { left: `calc(${left + width}% + 4px)` }
                      }
                    >
                      {formatSeconds(duration)}
                    </span>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
        {showSlo && (
          <tfoot>
            <tr>
              <td colSpan={4} className="pt-2 text-right text-xs text-muted">
                <span aria-hidden="true" className="mr-1 inline-block h-3 w-0 border-l border-dashed border-danger align-middle" />
                Dashed line: SLO {formatSeconds(sloS)}
              </td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  )
}
