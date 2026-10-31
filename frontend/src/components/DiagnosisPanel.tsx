import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import { useDiagnosis, useRunDiagnosis } from '../api/hooks'
import type { Confidence, DiagnosisRunOut } from '../api/types'
import { Pill } from './Badges'
import CategoryBadge from './CategoryBadge'
import { EvidenceChips, EvidenceList } from './Evidence'
import { ErrorState, Loading, errorMessage } from './QueryState'
import Section from './Section'
import { categoryLabel } from './categories'
import { formatSeconds } from './format'

const CONFIDENCE_TONE: Record<Confidence, 'ok' | 'warn' | 'neutral'> = {
  high: 'ok',
  medium: 'warn',
  low: 'neutral',
}

function useElapsed(since: string | null, active: boolean): number | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [active])
  if (!since) return null
  return Math.max(0, (now - new Date(since).getTime()) / 1000)
}

/** AI diagnosis for an incident: run/re-run, poll while running, render grounded findings. */
export default function DiagnosisPanel({ incidentId }: { incidentId: number }) {
  const diagnosis = useDiagnosis(incidentId)
  const run = useRunDiagnosis(incidentId)
  const d = diagnosis.data
  const running = d?.status === 'running' || run.isPending
  const conflict = run.error instanceof ApiError && run.error.status === 409

  const action = (
    <button
      type="button"
      onClick={() => run.mutate()}
      disabled={running || diagnosis.isPending}
      aria-busy={running || undefined}
      className="inline-flex items-center gap-2 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-surface hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {running && (
        <span aria-hidden="true" className="size-3 animate-spin rounded-full border-2 border-surface/40 border-t-surface" />
      )}
      {running ? 'Diagnosing…' : d ? 'Re-run diagnosis' : 'Run diagnosis'}
    </button>
  )

  let body: ReactNode
  if (diagnosis.isPending) body = <Loading label="Loading diagnosis…" />
  else if (diagnosis.isError && d === undefined)
    body = <ErrorState error={diagnosis.error} onRetry={() => void diagnosis.refetch()} />
  else if (!d) body = <NoDiagnosis />
  else if (d.status === 'running') body = <Running run={d} />
  else body = <DiagnosisResult run={d} />

  return (
    <Section id="diagnosis" title="AI diagnosis" actions={action}>
      <div className="space-y-4">
        {run.isError &&
          (conflict ? (
            <p role="status" className="rounded-md border border-line bg-surface-2 px-3 py-2 text-sm">
              {errorMessage(run.error)} — following the run already in progress.
            </p>
          ) : (
            <ErrorState error={run.error} onRetry={() => run.mutate()} />
          ))}
        <div aria-live="polite">{body}</div>
      </div>
    </Section>
  )
}

function NoDiagnosis() {
  return (
    <div className="rounded-md border border-dashed border-line px-4 py-6 text-sm">
      <p className="font-medium">No diagnosis yet.</p>
      <p className="mt-1 text-muted">
        Run diagnosis to have the Copilot investigate this incident. The Copilot investigates using recorded events,
        spans, pod spec, logs and metrics; every claim cites evidence. A run usually takes 10–60 seconds.
      </p>
    </div>
  )
}

function Running({ run }: { run: DiagnosisRunOut }) {
  const elapsed = useElapsed(run.started_at, true)
  return (
    <div role="status" className="flex items-center gap-3 rounded-md border border-line bg-surface-2 px-4 py-4 text-sm">
      <span aria-hidden="true" className="size-4 animate-spin rounded-full border-2 border-line border-t-accent" />
      <div>
        <p className="font-medium">Diagnosing…</p>
        <p className="text-muted">
          The Copilot is gathering evidence and reasoning about the cause
          {elapsed != null && <> ({formatSeconds(Math.round(elapsed))} elapsed)</>}. Typically 10–60 seconds.
        </p>
      </div>
    </div>
  )
}

function SubHeading({ children }: { children: ReactNode }) {
  return <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">{children}</h3>
}

function DiagnosisResult({ run: d }: { run: DiagnosisRunOut }) {
  const known = useMemo(() => new Set(d.evidence.map((e) => e.ref)), [d.evidence])
  const [openRefs, setOpenRefs] = useState<Set<string>>(() => new Set())
  const [focusRef, setFocusRef] = useState<{ ref: string; nonce: number } | null>(null)

  const show = (ref: string) => {
    setOpenRefs((prev) => new Set(prev).add(ref))
    setFocusRef((prev) => ({ ref, nonce: (prev?.nonce ?? 0) + 1 }))
  }
  const toggle = (ref: string, open: boolean) =>
    setOpenRefs((prev) => {
      if (prev.has(ref) === open) return prev
      const next = new Set(prev)
      if (open) next.add(ref)
      else next.delete(ref)
      return next
    })

  const chips = (ids: string[]) => <EvidenceChips ids={ids} known={known} onShow={show} />

  return (
    <div className="space-y-5">
      {d.status === 'failed' && (
        <div role="alert" className="rounded-md border border-danger/40 bg-danger-soft px-3 py-2 text-sm">
          <span className="font-medium text-danger">Diagnosis failed. </span>
          {d.error ?? 'No error message was recorded.'}
        </div>
      )}

      {(d.category || d.summary || d.source) && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            {d.category && <CategoryBadge category={d.category} />}
            {d.confidence && <Pill tone={CONFIDENCE_TONE[d.confidence]}>{d.confidence} confidence</Pill>}
            {d.source === 'llm' && (
              <Pill tone="accent" title="AI diagnosis whose claims passed grounding validation">
                AI · validated
              </Pill>
            )}
            {d.source === 'fallback' && (
              <Pill title="Deterministic detector result used because the AI diagnosis was unavailable or failed validation">
                Detector fallback
              </Pill>
            )}
          </div>
          {d.summary && <p className="text-sm leading-relaxed">{d.summary}</p>}
          {d.source === 'fallback' && (d.error || d.validation_errors.length > 0) && (
            <details className="text-sm">
              <summary className="cursor-pointer text-xs text-muted hover:text-fg">Why fallback</summary>
              <div className="mt-2 space-y-2 rounded-md border border-line bg-surface-2 px-3 py-2">
                {d.error && <p>{d.error}</p>}
                {d.validation_errors.length > 0 && (
                  <ul className="list-disc space-y-1 pl-5 text-xs">
                    {d.validation_errors.map((v, i) => (
                      <li key={i}>{v}</li>
                    ))}
                  </ul>
                )}
              </div>
            </details>
          )}
        </div>
      )}

      {d.findings.length > 0 && (
        <div>
          <SubHeading>Findings</SubHeading>
          <ul className="space-y-2">
            {d.findings.map((f, i) => (
              <li key={i} className="text-sm">
                {f.statement} {chips(f.evidence_ids)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {d.ruled_out.length > 0 && (
        <div>
          <SubHeading>Ruled out</SubHeading>
          <ul className="space-y-2">
            {d.ruled_out.map((r, i) => (
              <li key={i} className="text-sm">
                <span className="font-medium">{categoryLabel(r.category)}</span>
                <span className="text-muted"> — </span>
                {r.reason} {chips(r.evidence_ids)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {d.recommendations.length > 0 && (
        <div>
          <SubHeading>Recommendations</SubHeading>
          <ol className="list-decimal space-y-2 pl-5">
            {d.recommendations.map((r, i) => (
              <li key={i} className="text-sm">
                <strong>{r.action}</strong>
                <span className="block text-muted">
                  {r.rationale} {chips(r.evidence_ids)}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}

      <RunMetadata run={d} />

      <div>
        <SubHeading>Evidence ({d.evidence.length})</SubHeading>
        <EvidenceList evidence={d.evidence} openRefs={openRefs} focusRef={focusRef} onToggle={toggle} />
      </div>
    </div>
  )
}

function RunMetadata({ run: d }: { run: DiagnosisRunOut }) {
  const duration =
    d.finished_at != null ? (new Date(d.finished_at).getTime() - new Date(d.started_at).getTime()) / 1000 : null
  const tokens =
    d.input_tokens != null || d.output_tokens != null
      ? `${(d.input_tokens ?? 0).toLocaleString()} in / ${(d.output_tokens ?? 0).toLocaleString()} out`
      : '—'
  const items: [string, ReactNode][] = [
    ['Model', d.model ? (d.provider ? `${d.provider}/${d.model}` : d.model) : '—'],
    ['Prompt', d.prompt_version ?? '—'],
    ['Tool calls', d.tool_calls],
    ['LLM calls', d.llm_calls],
    ['Tokens', tokens],
    ['Duration', formatSeconds(duration)],
  ]
  return (
    <dl className="flex flex-wrap gap-x-5 gap-y-1 border-t border-line pt-3 text-xs text-muted">
      {items.map(([k, v]) => (
        <div key={k} className="flex gap-1">
          <dt>{k}</dt>
          <dd className="font-mono text-fg tabular">{v}</dd>
        </div>
      ))}
    </dl>
  )
}
