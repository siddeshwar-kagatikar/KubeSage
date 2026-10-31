import type { ReactNode } from 'react'
import type { IncidentStatus, StartupSummary } from '../api/types'

type Tone = 'neutral' | 'accent' | 'ok' | 'warn' | 'danger'

const TONE: Record<Tone, string> = {
  neutral: 'bg-surface-2 text-muted border-line',
  accent: 'bg-accent-soft text-accent border-accent/30',
  ok: 'bg-ok-soft text-ok border-ok/30',
  warn: 'bg-warn-soft text-warn border-warn/30',
  danger: 'bg-danger-soft text-danger border-danger/30',
}

export function Pill({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded border px-1.5 py-px text-xs font-medium whitespace-nowrap ${TONE[tone]}`}
    >
      {children}
    </span>
  )
}

export function ColdWarmBadge({ cold }: { cold: boolean | null }) {
  if (cold == null) return <Pill title="Cold/warm not yet determined">unknown</Pill>
  return cold ? (
    <Pill tone="accent" title="Cold start: image pulled onto the node">
      cold
    </Pill>
  ) : (
    <Pill title="Warm start: image already cached on the node">warm</Pill>
  )
}

export function StartupStatusBadge({ status }: { status: StartupSummary['status'] }) {
  return status === 'ready' ? <Pill tone="ok">ready</Pill> : <Pill tone="warn">starting…</Pill>
}

export function IncidentStatusBadge({ status }: { status: IncidentStatus }) {
  const tone: Tone = status === 'open' ? 'danger' : status === 'diagnosed' ? 'warn' : 'ok'
  return <Pill tone={tone}>{status}</Pill>
}

export function BreachBadge({ breached }: { breached: boolean | null }) {
  if (breached == null) return <Pill>SLO unknown</Pill>
  return breached ? <Pill tone="danger">SLO breached</Pill> : <Pill tone="ok">within SLO</Pill>
}
