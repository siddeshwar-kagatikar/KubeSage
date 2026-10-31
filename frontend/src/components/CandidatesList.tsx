import type { CandidateOut } from '../api/types'
import CategoryBadge from './CategoryBadge'
import KeyValueTable from './KeyValueTable'
import { EmptyState } from './QueryState'
import { categoryLabel } from './categories'
import { formatPercent, formatSeconds } from './format'

/** Detector candidates, highest score first. Score = share of total startup time explained. */
export default function CandidatesList({ candidates }: { candidates: CandidateOut[] }) {
  if (candidates.length === 0) return <EmptyState>No detector attributed this startup to a category.</EmptyState>
  const sorted = [...candidates].sort((a, b) => b.score - a.score)
  return (
    <ol className="space-y-3">
      {sorted.map((c, i) => {
        const share = Math.max(0, Math.min(1, c.score))
        return (
          <li key={`${c.category}-${i}`} className="rounded-md border border-line p-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="text-xs text-muted tabular">#{i + 1}</span>
              <CategoryBadge category={c.category} />
              <div className="flex items-center gap-2">
                <div
                  role="meter"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(share * 100)}
                  aria-label={`${categoryLabel(c.category)} explains`}
                  aria-valuetext={`${formatPercent(share)} of startup time`}
                  className="h-2 w-28 rounded-sm bg-track"
                >
                  <div className="h-full rounded-sm bg-accent" style={{ width: `${share * 100}%` }} />
                </div>
                <span className="text-sm font-medium tabular">{formatPercent(share)}</span>
                <span className="text-xs text-muted tabular">of startup · {formatSeconds(c.seconds)}</span>
              </div>
            </div>
            <p className="mt-2 text-sm">{c.summary}</p>
            {Object.keys(c.evidence).length > 0 && (
              <details className="mt-2 text-sm">
                <summary className="cursor-pointer text-xs text-muted hover:text-fg">Evidence</summary>
                <div className="mt-2 max-w-xl">
                  <KeyValueTable data={c.evidence} caption={`Evidence for ${categoryLabel(c.category)}`} />
                </div>
              </details>
            )}
          </li>
        )
      })}
    </ol>
  )
}
