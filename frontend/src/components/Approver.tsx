import { useId, useState } from 'react'

const KEY = 'kubesage.approver'

function load(): string {
  try {
    return localStorage.getItem(KEY) ?? ''
  } catch {
    return ''
  }
}

export function rememberApprover(name: string) {
  try {
    localStorage.setItem(KEY, name)
  } catch {
    /* storage unavailable: name just isn't remembered */
  }
}

/** Approver name input, prefilled with the last name used. */
export function useApproverField() {
  const [name, setName] = useState(load)
  const id = useId()
  const field = (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-muted">
        Your name <span aria-hidden="true">*</span>
      </label>
      <input
        id={id}
        required
        maxLength={128}
        autoComplete="name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="w-56 rounded border border-line bg-surface px-2 py-1 text-sm"
      />
    </div>
  )
  return { name: name.trim(), field }
}

/** Copy-to-clipboard button with a visible + announced confirmation. */
export function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setState('copied')
    } catch {
      setState('failed')
    }
    window.setTimeout(() => setState('idle'), 2000)
  }
  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={`Copy ${label}`}
      className="rounded border border-line bg-surface px-2 py-0.5 text-xs hover:bg-surface-2"
    >
      <span aria-live="polite">{state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : 'Copy'}</span>
    </button>
  )
}
