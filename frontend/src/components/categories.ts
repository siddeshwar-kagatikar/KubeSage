import type { Category } from '../api/types'

export const CATEGORY_LABELS: Record<Category, string> = {
  large_image: 'Large image',
  slow_registry: 'Slow registry/network',
  model_loading: 'Model weight loading',
  app_init: 'Slow app initialisation',
  capacity: 'Insufficient capacity',
  slow_readiness: 'Slow readiness',
}

export function isCategory(c: string | null | undefined): c is Category {
  return c != null && Object.hasOwn(CATEGORY_LABELS, c)
}

/** Label for a Category, "unknown" (→ Undetermined) or any other string the model produced. */
export function categoryLabel(c: string | null | undefined): string {
  if (!c) return 'Unattributed'
  if (c === 'unknown') return 'Undetermined'
  return isCategory(c) ? CATEGORY_LABELS[c] : c
}
