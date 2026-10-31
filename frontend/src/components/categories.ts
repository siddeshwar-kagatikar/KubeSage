import type { Category } from '../api/types'

export const CATEGORY_LABELS: Record<Category, string> = {
  large_image: 'Large image',
  slow_registry: 'Slow registry/network',
  model_loading: 'Model weight loading',
  app_init: 'Slow app initialisation',
  capacity: 'Insufficient capacity',
  slow_readiness: 'Slow readiness',
}

export function categoryLabel(c: Category | null | undefined): string {
  if (!c) return 'Unattributed'
  return CATEGORY_LABELS[c] ?? c
}
