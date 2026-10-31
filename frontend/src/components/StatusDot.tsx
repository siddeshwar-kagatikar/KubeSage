export type DotStatus = 'ok' | 'warn' | 'down' | 'unknown'

const COLOUR: Record<DotStatus, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  down: 'bg-danger',
  unknown: 'bg-muted',
}

/** Shape varies too (square when down, ring when degraded) so status is not conveyed by colour alone. */
const SHAPE: Record<DotStatus, string> = {
  ok: 'rounded-full',
  warn: 'rounded-full ring-2 ring-warn/40',
  down: 'rounded-[2px]',
  unknown: 'rounded-full opacity-60',
}

const SR_TEXT: Record<DotStatus, string> = {
  ok: 'OK',
  warn: 'degraded',
  down: 'down',
  unknown: 'unknown',
}

interface Props {
  status: DotStatus
  label: string
  /** Optional trailing detail, e.g. clock offset. */
  detail?: string
  /** Show the status word visibly (e.g. "down") rather than only to screen readers. */
  showStatusText?: boolean
}

export default function StatusDot({ status, label, detail, showStatusText = false }: Props) {
  return (
    <span className="inline-flex items-center gap-2 text-sm">
      <span aria-hidden="true" className={`inline-block size-2.5 shrink-0 ${COLOUR[status]} ${SHAPE[status]}`} />
      <span>
        {label}
        {showStatusText ? (
          <span className={`ml-1 text-xs ${status === 'ok' ? 'text-muted' : status === 'down' ? 'text-danger' : 'text-warn'}`}>
            {SR_TEXT[status]}
          </span>
        ) : (
          <span className="sr-only">: {SR_TEXT[status]}</span>
        )}
        {detail && <span className="ml-1 text-xs text-muted">{detail}</span>}
      </span>
    </span>
  )
}
