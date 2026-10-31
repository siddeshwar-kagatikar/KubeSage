import type { PhaseOut } from '../api/types'

/** Canonical order of top-level phases; used as a tie-breaker when start times are equal. */
export const PHASE_ORDER = [
  'scheduling',
  'pod_sandbox',
  'init_containers',
  'image_pull',
  'container_start',
  'app_init',
  'readiness',
  'app_init_and_readiness',
] as const

const PHASE_LABELS: Record<string, string> = {
  scheduling: 'Scheduling',
  pod_sandbox: 'Pod sandbox',
  init_containers: 'Init containers',
  image_pull: 'Image pull',
  container_start: 'Container start',
  app_init: 'App init',
  readiness: 'Readiness',
  app_init_and_readiness: 'App init + readiness',
  'process.boot': 'Process boot',
  import: 'Imports',
  'model.download': 'Model download',
  'model.load': 'Model load',
  'init.compute': 'Init compute',
  'dependency.connect': 'Dependency connect',
  warmup: 'Warm-up',
}

/** Mid-tone hues that hold up on both light and dark backgrounds. */
const PHASE_COLOURS: Record<string, string> = {
  scheduling: '#7c8796',
  pod_sandbox: '#8f7f6b',
  init_containers: '#c08a1e',
  image_pull: '#2f7fc1',
  container_start: '#5b6ad0',
  app_init: '#178f7e',
  readiness: '#9c4dbe',
  app_init_and_readiness: '#178f7e',
  'process.boot': '#3aa597',
  import: '#2a9bb5',
  'model.download': '#e07b39',
  'model.load': '#d4573b',
  'init.compute': '#6d9a2b',
  'dependency.connect': '#c04e9a',
  warmup: '#c9a227',
}

const FALLBACK_COLOUR = '#8a94a3'

export function phaseLabel(name: string): string {
  return PHASE_LABELS[name] ?? name
}

export function phaseColour(name: string): string {
  return PHASE_COLOURS[name] ?? FALLBACK_COLOUR
}

export interface WaterfallRow {
  phase: PhaseOut
  depth: 0 | 1
  /** Seconds from created_at to phase start (clamped at 0). */
  offset: number
  duration: number
}

function orderIndex(name: string): number {
  const i = (PHASE_ORDER as readonly string[]).indexOf(name)
  return i === -1 ? PHASE_ORDER.length : i
}

/**
 * Builds display rows: top-level phases by start time, each followed by its child spans.
 * Children whose parent is absent are attached to `app_init_and_readiness` if present, else appended.
 */
export function buildRows(phases: PhaseOut[], createdAt: string): WaterfallRow[] {
  const t0 = new Date(createdAt).getTime()
  const toRow = (phase: PhaseOut, depth: 0 | 1): WaterfallRow => {
    const start = new Date(phase.start).getTime()
    const end = new Date(phase.end).getTime()
    const offset = Number.isFinite(start) ? Math.max(0, (start - t0) / 1000) : 0
    const fromTimes = Number.isFinite(end) && Number.isFinite(start) ? (end - start) / 1000 : 0
    const duration = Math.max(0, Number.isFinite(phase.duration_s) ? phase.duration_s : fromTimes)
    return { phase, depth, offset, duration }
  }

  const byStart = (a: PhaseOut, b: PhaseOut) =>
    new Date(a.start).getTime() - new Date(b.start).getTime() || orderIndex(a.name) - orderIndex(b.name)

  const top = phases.filter((p) => !p.parent).sort(byStart)
  const topNames = new Set(top.map((p) => p.name))
  const children = new Map<string, PhaseOut[]>()
  const orphans: PhaseOut[] = []
  for (const p of phases) {
    if (!p.parent) continue
    let parent = p.parent
    if (!topNames.has(parent)) {
      if (topNames.has('app_init_and_readiness')) parent = 'app_init_and_readiness'
      else {
        orphans.push(p)
        continue
      }
    }
    const list = children.get(parent) ?? []
    list.push(p)
    children.set(parent, list)
  }

  const rows: WaterfallRow[] = []
  for (const p of top) {
    rows.push(toRow(p, 0))
    for (const c of (children.get(p.name) ?? []).sort(byStart)) rows.push(toRow(c, 1))
  }
  for (const o of orphans.sort(byStart)) rows.push(toRow(o, 1))
  return rows
}

const NICE_STEPS = [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1800, 3600]

/** Picks a readable tick step giving at most ~8 intervals. */
export function axisTicks(max: number, maxTicks = 8): { step: number; ticks: number[] } {
  const step = NICE_STEPS.find((s) => max / s <= maxTicks) ?? Math.ceil(max / maxTicks)
  const ticks: number[] = []
  for (let t = 0; t <= max + 1e-9; t += step) ticks.push(Number(t.toFixed(3)))
  return { step, ticks }
}
