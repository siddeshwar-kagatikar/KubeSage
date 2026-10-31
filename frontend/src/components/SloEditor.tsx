import { useId, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useUpdateSlo } from '../api/hooks'
import { errorMessage } from './QueryState'
import { formatSeconds } from './format'

/** Inline SLO display with an edit form (PUT /api/workloads/{id}/slo). */
export default function SloEditor({ workloadId, slo }: { workloadId: number; slo: number }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(String(slo))
  const [validation, setValidation] = useState<string | null>(null)
  const mutation = useUpdateSlo(workloadId)
  const inputId = useId()
  const errorId = useId()

  const start = () => {
    setValue(String(slo))
    setValidation(null)
    mutation.reset()
    setEditing(true)
  }

  const cancel = () => {
    setEditing(false)
    setValidation(null)
    mutation.reset()
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const n = Number(value)
    if (!value.trim() || !Number.isFinite(n) || n <= 0) {
      setValidation('Enter a positive number of seconds.')
      return
    }
    setValidation(null)
    mutation.mutate({ slo_seconds: n }, { onSuccess: () => setEditing(false) })
  }

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') cancel()
  }

  if (!editing) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted">SLO</span>
        <span className="text-lg font-semibold tabular">{formatSeconds(slo)}</span>
        <button
          type="button"
          onClick={start}
          className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:bg-surface-2 hover:text-fg"
          aria-label={`Edit SLO (currently ${formatSeconds(slo)})`}
        >
          Edit
        </button>
      </div>
    )
  }

  const error = validation ?? (mutation.isError ? errorMessage(mutation.error) : null)

  return (
    <form onSubmit={submit} onKeyDown={onKeyDown} className="flex flex-wrap items-center gap-2" noValidate>
      <label htmlFor={inputId} className="text-sm text-muted">
        SLO (seconds)
      </label>
      <input
        id={inputId}
        type="number"
        inputMode="decimal"
        min={0.1}
        step="any"
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        className="w-24 rounded border border-line bg-surface px-2 py-1 text-sm tabular"
      />
      <button
        type="submit"
        disabled={mutation.isPending}
        className="rounded bg-accent px-2.5 py-1 text-xs font-medium text-surface hover:opacity-90 disabled:opacity-60"
      >
        {mutation.isPending ? 'Saving…' : 'Save'}
      </button>
      <button
        type="button"
        onClick={cancel}
        className="rounded border border-line px-2.5 py-1 text-xs hover:bg-surface-2"
      >
        Cancel
      </button>
      {error && (
        <p id={errorId} role="alert" className="w-full text-xs text-danger">
          {error}
        </p>
      )}
    </form>
  )
}
