import type { ReactNode } from 'react'

/** Card-style section with an accessible heading. */
export default function Section({
  title,
  id,
  actions,
  children,
  className = '',
  flush = false,
}: {
  title: ReactNode
  id: string
  actions?: ReactNode
  children: ReactNode
  className?: string
  /** Remove body padding (for edge-to-edge tables). */
  flush?: boolean
}) {
  const headingId = `${id}-heading`
  return (
    <section aria-labelledby={headingId} className={`rounded-lg border border-line bg-surface ${className}`}>
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <h2 id={headingId} className="text-sm font-semibold">
          {title}
        </h2>
        {actions}
      </div>
      <div className={flush ? '' : 'p-4'}>{children}</div>
    </section>
  )
}

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-0.5 truncate text-sm font-medium tabular">{value}</dd>
    </div>
  )
}

/** Shared table cell classes so all tables look alike. */
export const th = 'px-4 py-2 text-left text-xs font-medium text-muted whitespace-nowrap'
export const td = 'px-4 py-2.5 align-middle'
