import type { Category } from '../api/types'
import { categoryLabel } from './categories'

/** Muted swatch per category; the text label always carries the meaning. */
const SWATCH: Record<Category, string> = {
  large_image: '#2f7fc1',
  slow_registry: '#7a5af5',
  model_loading: '#d4573b',
  app_init: '#178f7e',
  capacity: '#b7791f',
  slow_readiness: '#9c4dbe',
}

export default function CategoryBadge({ category }: { category: Category | null | undefined }) {
  if (!category) return <span className="text-sm text-muted">—</span>
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2 py-0.5 text-xs font-medium whitespace-nowrap">
      <span aria-hidden="true" className="size-2 rounded-full" style={{ backgroundColor: SWATCH[category] }} />
      {categoryLabel(category)}
    </span>
  )
}
