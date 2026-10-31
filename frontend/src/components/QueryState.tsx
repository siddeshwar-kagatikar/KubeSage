import type { ReactNode } from 'react'
import { ApiError } from '../api/client'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 py-6 text-sm text-muted">
      <span aria-hidden="true" className="size-3 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label}
    </div>
  )
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.status === 404 ? 'Not found.' : error.detail
  if (error instanceof Error) return error.message
  return 'Something went wrong.'
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-md border border-danger/40 bg-danger-soft px-3 py-2 text-sm"
    >
      <span className="font-medium text-danger">Error</span>
      <span>{errorMessage(error)}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="ml-auto rounded border border-line bg-surface px-2 py-0.5 text-xs hover:bg-surface-2"
        >
          Retry
        </button>
      )}
    </div>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-md border border-dashed border-line px-4 py-6 text-center text-sm text-muted">{children}</p>
  )
}

interface QueryLike<T> {
  data: T | undefined
  isPending: boolean
  isError: boolean
  error: unknown
  refetch: () => unknown
}

/**
 * Renders loading / error / empty / data for a React Query result.
 * `isEmpty` defaults to "array with no items".
 */
export function QueryView<T>({
  query,
  empty,
  isEmpty,
  loadingLabel,
  children,
}: {
  query: QueryLike<T>
  empty?: ReactNode
  isEmpty?: (data: T) => boolean
  loadingLabel?: string
  children: (data: T) => ReactNode
}) {
  if (query.isPending) return <Loading label={loadingLabel} />
  // With polling, keep showing stale data if a background refetch fails.
  if (query.data === undefined)
    return <ErrorState error={query.error ?? new Error('No data')} onRetry={() => void query.refetch()} />
  const emptyCheck = isEmpty ?? ((d: T) => Array.isArray(d) && d.length === 0)
  return (
    <>
      {query.isError && (
        <div className="mb-3">
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        </div>
      )}
      {empty && emptyCheck(query.data) ? <EmptyState>{empty}</EmptyState> : children(query.data)}
    </>
  )
}
