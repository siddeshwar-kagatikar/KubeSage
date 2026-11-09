import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useAbortExperiment } from '../api/hooks'
import type { ArmSummary, ExperimentOut, TrialOut, Verdict } from '../api/types'
import { rememberApprover, useApproverField } from './Approver'
import { Pill } from './Badges'
import KeyValueTable from './KeyValueTable'
import { EmptyState, ErrorState } from './QueryState'
import { td, th } from './Section'
import { formatSeconds } from './format'
import { phaseLabel } from './phases'

const VERDICT: Record<Verdict, { label: string; tone: 'ok' | 'neutral' | 'danger' | 'warn' }> = {
  improved: { label: 'Improved', tone: 'ok' },
  no_change: { label: 'No significant change', tone: 'neutral' },
  regressed: { label: 'Regressed', tone: 'danger' },
  failed: { label: 'Failed', tone: 'danger' },
}

export function VerdictBadge({ verdict }: { verdict: Verdict | null }) {
  if (!verdict) return null
  const v = VERDICT[verdict]
  return <Pill tone={v.tone}>{v.label}</Pill>
}

export function ExperimentStatusBadge({ status }: { status: ExperimentOut['status'] }) {
  const tone = status === 'running' ? 'accent' : status === 'completed' ? 'ok' : status === 'failed' ? 'danger' : 'neutral'
  return <Pill tone={tone}>{status === 'running' ? 'running…' : status}</Pill>
}

export function ExperimentProgress({ e }: { e: ExperimentOut }) {
  const done = e.trials.length
  return (
    <div className="flex items-center gap-3">
      <progress
        max={e.planned_trials}
        value={done}
        aria-label="Trials completed"
        className="h-2 w-48 overflow-hidden rounded [&::-moz-progress-bar]:bg-accent [&::-webkit-progress-bar]:bg-track [&::-webkit-progress-value]:bg-accent"
      />
      <span className="text-sm tabular">
        {done} / {e.planned_trials} trials
      </span>
    </div>
  )
}

const ARM_COLOUR = { baseline: 'bg-muted', candidate: 'bg-accent' } as const

function guardrailSummary(t: TrialOut): string {
  const g = t.guardrails
  const parts: string[] = []
  if (typeof g.restarts === 'number') parts.push(`${g.restarts} restart${g.restarts === 1 ? '' : 's'}`)
  if (typeof g.smoke_ok === 'boolean') parts.push(g.smoke_ok ? 'smoke ok' : 'smoke failed')
  return parts.join(', ') || '—'
}

export function TrialsTable({ trials }: { trials: TrialOut[] }) {
  if (trials.length === 0) return <EmptyState>No trials finished yet.</EmptyState>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <caption className="sr-only">Experiment trials in run order</caption>
        <thead className="border-b border-line">
          <tr>
            <th scope="col" className={th}>#</th>
            <th scope="col" className={th}>Arm</th>
            <th scope="col" className={th}>Status</th>
            <th scope="col" className={`${th} text-right`}>Total</th>
            <th scope="col" className={th}>Guardrails</th>
            <th scope="col" className={`${th} text-right`}>Prep</th>
            <th scope="col" className={th}>Error</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {trials.map((t) => (
            <tr key={t.index} className={t.warmup ? 'text-muted' : ''}>
              <th scope="row" className={`${td} text-left font-normal tabular`}>
                {t.index}
              </th>
              <td className={td}>
                <span className="inline-flex items-center gap-1.5">
                  <span aria-hidden="true" className={`size-2 rounded-full ${ARM_COLOUR[t.arm]}`} />
                  {t.arm}
                  {t.warmup && <Pill title="Warm-up trial, excluded from the comparison">warm-up</Pill>}
                </span>
              </td>
              <td className={td}>
                {t.status === 'ok' ? <Pill tone="ok">ok</Pill> : <Pill tone="danger">failed</Pill>}
              </td>
              <td className={`${td} text-right tabular whitespace-nowrap`}>
                {t.startup_id != null ? (
                  <Link to={`/startups/${t.startup_id}`} aria-label={`Trial ${t.index} waterfall, ${formatSeconds(t.total_s)}`}>
                    {formatSeconds(t.total_s)}
                  </Link>
                ) : (
                  formatSeconds(t.total_s)
                )}
              </td>
              <td className={`${td} whitespace-nowrap`}>{guardrailSummary(t)}</td>
              <td className={`${td} text-right tabular`}>{formatSeconds(t.prep_s)}</td>
              <td className={`${td} max-w-xs text-xs break-words text-danger`}>{t.error ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Baseline vs candidate: min–max range with the median marked, on a shared scale. */
export function ArmBars({ baseline, candidate }: { baseline: ArmSummary; candidate: ArmSummary }) {
  const max = Math.max(baseline.max_s ?? 0, candidate.max_s ?? 0, 0.1) * 1.05
  const pct = (s: number) => `${(s / max) * 100}%`
  const rows: [string, ArmSummary, string][] = [
    ['Baseline', baseline, ARM_COLOUR.baseline],
    ['Candidate', candidate, ARM_COLOUR.candidate],
  ]
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">Startup totals by arm: median, min and max</caption>
      <thead className="sr-only">
        <tr>
          <th scope="col">Arm</th>
          <th scope="col">Median</th>
          <th scope="col">Range</th>
          <th scope="col">Chart</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, a, colour]) => (
          <tr key={label}>
            <th scope="row" className="w-24 py-2 pr-3 text-left font-medium">
              {label}
              <div className="text-xs font-normal text-muted">
                {a.succeeded}/{a.trials} ok
              </div>
            </th>
            <td className="w-24 py-2 pr-3 text-right tabular">
              <span className="sr-only">median </span>
              <span className="font-semibold">{formatSeconds(a.median_s)}</span>
            </td>
            <td className="w-36 py-2 pr-3 text-xs text-muted tabular">
              {formatSeconds(a.min_s)} – {formatSeconds(a.max_s)}
            </td>
            <td className="py-2">
              {a.median_s != null && a.min_s != null && a.max_s != null && (
                <div aria-hidden="true" className="relative h-4 rounded-sm bg-track">
                  <div className={`absolute inset-y-1 opacity-40 ${colour}`} style={{ left: pct(a.min_s), width: `calc(${pct(a.max_s - a.min_s)} + 2px)` }} />
                  <div className={`absolute inset-y-0 left-0 rounded-sm ${colour}`} style={{ width: pct(a.median_s) }} />
                </div>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function PhaseMedians({ baseline, candidate }: { baseline: ArmSummary; candidate: ArmSummary }) {
  const names = [...new Set([...Object.keys(baseline.phase_median_s), ...Object.keys(candidate.phase_median_s)])]
  if (names.length === 0) return <p className="text-sm text-muted">No phase data.</p>
  const max = Math.max(0.1, ...names.flatMap((n) => [baseline.phase_median_s[n] ?? 0, candidate.phase_median_s[n] ?? 0]))
  const bar = (s: number | undefined, colour: string) => (
    <div aria-hidden="true" className="h-2 rounded-sm bg-track">
      <div className={`h-full rounded-sm ${colour}`} style={{ width: `${((s ?? 0) / max) * 100}%` }} />
    </div>
  )
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-sm">
        <caption className="sr-only">Median seconds per phase, baseline vs candidate</caption>
        <thead className="border-b border-line">
          <tr>
            <th scope="col" className="py-2 pr-3 text-left text-xs font-medium text-muted">Phase</th>
            <th scope="col" className="px-2 py-2 text-right text-xs font-medium text-muted">Baseline</th>
            <th scope="col" className="px-2 py-2 text-right text-xs font-medium text-muted">Candidate</th>
            <th scope="col" className="px-2 py-2 text-right text-xs font-medium text-muted">Change</th>
            <th scope="col" className="w-2/5 py-2 pl-3 text-left text-xs font-medium text-muted">
              <span className="sr-only">Chart</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line/60">
          {names.map((n) => {
            const b = baseline.phase_median_s[n]
            const c = candidate.phase_median_s[n]
            const d = b != null && c != null ? c - b : null
            return (
              <tr key={n}>
                <th scope="row" className="py-1.5 pr-3 text-left font-normal">{phaseLabel(n)}</th>
                <td className="px-2 py-1.5 text-right tabular">{formatSeconds(b)}</td>
                <td className="px-2 py-1.5 text-right tabular">{formatSeconds(c)}</td>
                <td className={`px-2 py-1.5 text-right tabular ${d != null && d < -0.05 ? 'text-ok' : d != null && d > 0.05 ? 'text-danger' : 'text-muted'}`}>
                  {d == null ? '—' : `${d > 0 ? '+' : ''}${formatSeconds(d)}`}
                </td>
                <td className="space-y-1 py-1.5 pl-3">
                  {bar(b, ARM_COLOUR.baseline)}
                  {bar(c, ARM_COLOUR.candidate)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="mt-2 flex gap-4 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className={`h-2 w-4 rounded-sm ${ARM_COLOUR.baseline}`} />Baseline (top bar)</span>
        <span className="inline-flex items-center gap-1.5"><span aria-hidden="true" className={`h-2 w-4 rounded-sm ${ARM_COLOUR.candidate}`} />Candidate (bottom bar)</span>
      </p>
    </div>
  )
}

export function PrepCosts({ e }: { e: ExperimentOut }) {
  const c = e.comparison
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <h4 className="mb-1 text-xs font-medium text-muted">One-off preparation</h4>
        <KeyValueTable data={e.prep} caption="One-off preparation costs" emptyText="None for this action." />
      </div>
      <div>
        <h4 className="mb-1 text-xs font-medium text-muted">Per-trial preparation (median)</h4>
        <KeyValueTable
          data={{ baseline_s: c.baseline?.prep_median_s ?? null, candidate_s: c.candidate?.prep_median_s ?? null }}
          caption="Per-trial preparation median by arm"
        />
      </div>
    </div>
  )
}

export function AbortForm({ e }: { e: ExperimentOut }) {
  const { name, field } = useApproverField()
  const [missing, setMissing] = useState(false)
  const abort = useAbortExperiment(e)
  const submit = (ev: FormEvent) => {
    ev.preventDefault()
    if (!name) return setMissing(true)
    setMissing(false)
    if (!window.confirm(`Abort experiment #${e.id}? Running trials are stopped and sandbox objects cleaned up.`)) return
    rememberApprover(name)
    abort.mutate({ approver: name })
  }
  return (
    <form onSubmit={submit} noValidate className="flex flex-wrap items-end gap-3">
      {field}
      <button
        type="submit"
        disabled={abort.isPending}
        className="rounded-md border border-danger/50 px-3 py-1.5 text-xs font-medium text-danger hover:bg-danger-soft disabled:opacity-60"
      >
        {abort.isPending ? 'Aborting…' : 'Abort experiment'}
      </button>
      {missing && (
        <p role="alert" className="w-full text-xs text-danger">
          Enter your name to abort.
        </p>
      )}
      {abort.isError && (
        <div className="w-full">
          <ErrorState error={abort.error} />
        </div>
      )}
    </form>
  )
}
