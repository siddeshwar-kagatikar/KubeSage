import { useEffect, useRef } from 'react'
import type { EvidenceOut } from '../api/types'
import { formatDateTime } from './format'

export const evidenceAnchor = (ref: string) => `evidence-${ref}`

const TOOL_LABELS: Record<string, string> = {
  get_startup_timeline: 'Startup timeline',
  get_detector_findings: 'Detector findings',
  get_pod_events: 'Pod events',
  get_pod_spec: 'Pod spec',
  get_container_logs: 'Container logs',
  get_workload_history: 'Workload history',
  query_metric: 'Metric query',
  get_node_capacity: 'Node capacity',
  get_runbook: 'Runbook',
  search_past_incidents: 'Past incidents',
}

export function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool
}

/** Inline citation chips. Each is a button that reveals the cited evidence item. */
export function EvidenceChips({
  ids,
  known,
  onShow,
}: {
  ids: string[]
  known: Set<string>
  onShow: (ref: string) => void
}) {
  if (ids.length === 0) return null
  return (
    <span className="inline-flex flex-wrap gap-1 align-middle">
      {ids.map((ref) =>
        known.has(ref) ? (
          <button
            key={ref}
            type="button"
            onClick={() => onShow(ref)}
            aria-label={`Show evidence ${ref}`}
            className="rounded border border-accent/40 bg-accent-soft px-1.5 py-px font-mono text-[11px] font-medium text-accent hover:border-accent"
          >
            {ref}
          </button>
        ) : (
          <span
            key={ref}
            title="Cited evidence not found in this run"
            className="rounded border border-dashed border-line px-1.5 py-px font-mono text-[11px] text-muted"
          >
            {ref}
            <span className="sr-only"> (evidence not found)</span>
          </span>
        ),
      )}
    </span>
  )
}

function summariseArgs(args: Record<string, unknown>): string {
  const parts = Object.entries(args).map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
  const s = parts.join(', ')
  return s.length > 90 ? `${s.slice(0, 87)}…` : s
}

const pre = 'max-h-96 overflow-auto rounded border border-line bg-surface-2 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words'

function EvidenceItem({
  item,
  open,
  focusNonce,
  onToggle,
}: {
  item: EvidenceOut
  open: boolean
  /** Changes each time a chip requests this item; null when not targeted. */
  focusNonce: number | null
  onToggle: (open: boolean) => void
}) {
  const summaryRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (focusNonce != null && summaryRef.current) {
      summaryRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
      summaryRef.current.focus({ preventScroll: true })
    }
  }, [focusNonce])

  const { runbook, ...rest } = item.result
  const hasRunbook = typeof runbook === 'string'
  const hasArgs = Object.keys(item.args).length > 0

  return (
    <li>
      <details
        id={evidenceAnchor(item.ref)}
        open={open}
        onToggle={(e) => onToggle(e.currentTarget.open)}
        className={`rounded-md border ${focusNonce != null ? 'border-accent' : 'border-line'} scroll-mt-20`}
      >
        <summary ref={summaryRef} className="flex cursor-pointer flex-wrap items-baseline gap-x-2 gap-y-1 px-3 py-2 text-sm">
          <span className="font-mono text-xs font-semibold text-accent">{item.ref}</span>
          <span className="font-medium">{toolLabel(item.tool)}</span>
          <code className="text-xs text-muted">{item.tool}</code>
          {hasArgs && <span className="min-w-0 truncate font-mono text-xs text-muted">({summariseArgs(item.args)})</span>}
        </summary>
        <div className="space-y-3 border-t border-line px-3 py-3 text-sm">
          <p className="text-xs text-muted">Recorded {formatDateTime(item.created_at)}</p>
          {hasArgs && (
            <div>
              <h4 className="mb-1 text-xs font-medium text-muted">Arguments</h4>
              <pre className={pre}>{JSON.stringify(item.args, null, 2)}</pre>
            </div>
          )}
          {hasRunbook && (
            <div>
              <h4 className="mb-1 text-xs font-medium text-muted">Runbook</h4>
              <pre className={pre}>{runbook}</pre>
            </div>
          )}
          {(!hasRunbook || Object.keys(rest).length > 0) && (
            <div>
              <h4 className="mb-1 text-xs font-medium text-muted">Result</h4>
              <pre className={pre}>{JSON.stringify(hasRunbook ? rest : item.result, null, 2)}</pre>
            </div>
          )}
        </div>
      </details>
    </li>
  )
}

/** Evidence gathered during a diagnosis run; items are collapsed by default. */
export function EvidenceList({
  evidence,
  openRefs,
  focusRef,
  onToggle,
}: {
  evidence: EvidenceOut[]
  openRefs: Set<string>
  /** Ref most recently requested via a chip; scrolled to and focused. */
  focusRef: { ref: string; nonce: number } | null
  onToggle: (ref: string, open: boolean) => void
}) {
  if (evidence.length === 0) return <p className="text-sm text-muted">No evidence recorded.</p>
  return (
    <ol className="space-y-2">
      {evidence.map((e) => (
        <EvidenceItem
          key={e.ref}
          item={e}
          open={openRefs.has(e.ref)}
          focusNonce={focusRef?.ref === e.ref ? focusRef.nonce : null}
          onToggle={(open) => onToggle(e.ref, open)}
        />
      ))}
    </ol>
  )
}
