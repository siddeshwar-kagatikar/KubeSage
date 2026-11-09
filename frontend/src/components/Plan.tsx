import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError } from '../api/client'
import { useApprovePlan, useRejectPlan } from '../api/hooks'
import type { ActionPlanOut, PlanStatus, PolicyCheckOut } from '../api/types'
import { CopyButton, rememberApprover, useApproverField } from './Approver'
import { Pill } from './Badges'
import CategoryBadge from './CategoryBadge'
import { ErrorState } from './QueryState'
import { formatDateTime } from './format'

const STATUS: Record<PlanStatus, { label: string; tone: 'neutral' | 'accent' | 'ok' | 'warn' | 'danger' }> = {
  awaiting_approval: { label: 'awaiting approval', tone: 'warn' },
  rejected_by_policy: { label: 'blocked by policy', tone: 'danger' },
  rejected: { label: 'rejected', tone: 'neutral' },
  running: { label: 'experiment running', tone: 'accent' },
  completed: { label: 'experiment completed', tone: 'ok' },
  failed: { label: 'experiment failed', tone: 'danger' },
  aborted: { label: 'aborted', tone: 'neutral' },
}

export function PlanStatusBadge({ status }: { status: PlanStatus }) {
  const s = STATUS[status] ?? { label: status, tone: 'neutral' as const }
  return <Pill tone={s.tone}>{s.label}</Pill>
}

export function PolicyChecks({ checks }: { checks: PolicyCheckOut[] }) {
  if (checks.length === 0) return <p className="text-sm text-muted">No policy checks recorded.</p>
  return (
    <ul className="space-y-1 text-sm">
      {checks.map((c) => (
        <li key={c.name} className="flex gap-2">
          <span aria-hidden="true" className={`w-4 shrink-0 font-bold ${c.passed ? 'text-ok' : 'text-danger'}`}>
            {c.passed ? '✓' : '✗'}
          </span>
          <span>
            <span className="sr-only">{c.passed ? 'Passed: ' : 'Failed: '}</span>
            <span className="font-medium">{c.name}</span>
            {c.detail && <span className="text-muted"> — {c.detail}</span>}
          </span>
        </li>
      ))}
    </ul>
  )
}

function lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'text-muted'
  if (line.startsWith('+')) return 'bg-ok-soft text-fg'
  if (line.startsWith('-')) return 'bg-danger-soft text-fg'
  if (line.startsWith('@@')) return 'text-accent'
  return ''
}

/** Unified diff; the +/- markers stay visible so the tint is not the only cue. */
export function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) return <p className="text-sm text-muted">No changes to show.</p>
  return (
    <pre
      aria-label="Diff of the sandbox pod spec"
      className="max-h-[28rem] overflow-auto rounded border border-line bg-surface-2 py-2 font-mono text-xs leading-5"
    >
      {diff.split('\n').map((line, i) => (
        <div key={i} className={`px-3 whitespace-pre ${lineClass(line)}`}>
          {line || ' '}
        </div>
      ))}
    </pre>
  )
}

const pre = 'max-h-80 overflow-auto rounded border border-line bg-surface-2 p-3 font-mono text-xs whitespace-pre'

function Block({ title, text }: { title: string; text: string }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <h4 className="text-xs font-medium text-muted">{title}</h4>
        <CopyButton text={text} label={title} />
      </div>
      <pre className={pre}>{text}</pre>
    </div>
  )
}

export function ExportSection({ plan }: { plan: ActionPlanOut }) {
  const { command, patch, objects } = plan.export
  return (
    <div className="space-y-3">
      <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-sm">
        KubeSage only changes a sandbox copy. Nothing below is applied to the original workload automatically — review
        it and apply it yourself (e.g. through your GitOps repo) once the experiment shows an improvement.
      </p>
      {command && <Block title="Command" text={command} />}
      {patch && <Block title="patch.json" text={JSON.stringify(patch, null, 2)} />}
      {objects && objects.length > 0 && (
        <Block title={`Additional objects (${objects.length})`} text={JSON.stringify(objects, null, 2)} />
      )}
      {!command && !patch && !objects?.length && <p className="text-sm text-muted">No export available.</p>}
    </div>
  )
}

function DecisionForm({ plan }: { plan: ActionPlanOut }) {
  const { name, field } = useApproverField()
  const [note, setNote] = useState('')
  const [missingName, setMissingName] = useState(false)
  const approve = useApprovePlan(plan)
  const reject = useRejectPlan(plan)
  const navigate = useNavigate()
  const busy = approve.isPending || reject.isPending
  const error = approve.error ?? reject.error

  const decide = (kind: 'approve' | 'reject') => (e?: FormEvent) => {
    e?.preventDefault()
    if (!name) {
      setMissingName(true)
      return
    }
    setMissingName(false)
    rememberApprover(name)
    const body = { approver: name, note: note.trim() || null }
    if (kind === 'approve') {
      reject.reset()
      approve.mutate(body, { onSuccess: (exp) => navigate(`/experiments/${exp.id}`) })
    } else {
      approve.reset()
      reject.mutate(body)
    }
  }

  return (
    <form onSubmit={decide('approve')} noValidate className="space-y-3 rounded-md border border-line p-3">
      <p className="text-sm">
        Approving runs a before/after experiment in the sandbox namespace (about 10 minutes). The original workload is not
        modified.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        {field}
        <div className="flex min-w-60 flex-1 flex-col gap-1">
          <label htmlFor={`note-${plan.id}`} className="text-xs font-medium text-muted">
            Note (optional)
          </label>
          <input
            id={`note-${plan.id}`}
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="rounded border border-line bg-surface px-2 py-1 text-sm"
          />
        </div>
      </div>
      {missingName && (
        <p role="alert" className="text-xs text-danger">
          Enter your name to record the decision.
        </p>
      )}
      {error && (
        <ErrorState
          error={
            error instanceof ApiError && error.status === 409
              ? new Error(`Approval blocked: ${error.detail}`)
              : error
          }
        />
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-surface hover:opacity-90 disabled:opacity-60"
        >
          {approve.isPending ? 'Starting experiment…' : 'Approve and run experiment'}
        </button>
        <button
          type="button"
          onClick={() => decide('reject')()}
          disabled={busy}
          className="rounded-md border border-line px-3 py-1.5 text-xs hover:bg-surface-2 disabled:opacity-60"
        >
          {reject.isPending ? 'Rejecting…' : 'Reject'}
        </button>
      </div>
    </form>
  )
}

function Sub({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">{title}</h3>
      {children}
    </div>
  )
}

/** Full plan body: rationale, policy checks, diff, export and the approve/reject form. */
export function PlanDetails({ plan: p }: { plan: ActionPlanOut }) {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <PlanStatusBadge status={p.status} />
        <CategoryBadge category={p.category} />
        <Pill tone={p.risk === 'low' ? 'ok' : 'warn'}>{p.risk} risk</Pill>
        {p.source === 'llm' ? <Pill tone="accent">AI-selected</Pill> : <Pill>Deterministic default</Pill>}
        <code className="text-xs text-muted">{p.action}</code>
      </div>

      <div className="space-y-2 text-sm">
        <p>{p.rationale}</p>
        {p.action_note && <p className="text-muted">{p.action_note}</p>}
        {p.source === 'fallback' && p.llm_error && (
          <details>
            <summary className="cursor-pointer text-xs text-muted hover:text-fg">Why deterministic default</summary>
            <p className="mt-2 rounded-md border border-line bg-surface-2 px-3 py-2 text-xs break-words">{p.llm_error}</p>
          </details>
        )}
      </div>

      {p.experiment_id != null && (
        <p className="text-sm">
          <Link to={`/experiments/${p.experiment_id}`}>View experiment #{p.experiment_id} →</Link>
        </p>
      )}

      {p.decided_by && (
        <p className="text-sm text-muted">
          Decided by <span className="text-fg">{p.decided_by}</span>
          {p.decided_at && <> on {formatDateTime(p.decided_at)}</>}
          {p.decision_note && <> — “{p.decision_note}”</>}
        </p>
      )}

      {p.status === 'awaiting_approval' && <DecisionForm plan={p} />}

      <Sub title="Policy checks">
        <PolicyChecks checks={p.policy_checks} />
        {p.status === 'rejected_by_policy' && (
          <p className="mt-2 text-sm text-danger">A policy check failed, so this plan cannot be approved.</p>
        )}
      </Sub>

      <Sub title="Sandbox change (diff)">
        <DiffView diff={p.diff} />
      </Sub>

      <Sub title="Apply to the original workload">
        <ExportSection plan={p} />
      </Sub>
    </div>
  )
}
