/** Shared display formatters. Keep these pure so they are trivial to test. */

const NBSP = ' '

export function formatSeconds(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return '—'
  const abs = Math.abs(s)
  if (abs === 0) return `0${NBSP}s`
  if (abs < 1) return `${Math.round(s * 1000)}${NBSP}ms`
  if (abs < 10) return `${s.toFixed(2)}${NBSP}s`
  if (abs < 120) return `${s.toFixed(1)}${NBSP}s`
  const m = Math.floor(abs / 60)
  const rem = Math.round(abs - m * 60)
  return `${s < 0 ? '-' : ''}${m}${NBSP}m${NBSP}${rem}${NBSP}s`
}

/** Compact axis tick label, e.g. "0", "2.5s", "1m30s". */
export function formatTick(s: number): string {
  if (s === 0) return '0'
  if (s < 1) return `${Math.round(s * 1000)}ms`
  if (s < 60) return `${Number(s.toFixed(1))}s`
  const m = Math.floor(s / 60)
  const rem = Math.round(s - m * 60)
  return rem ? `${m}m${rem}s` : `${m}m`
}

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB']

export function formatBytes(b: number | null | undefined): string {
  if (b == null || !Number.isFinite(b)) return '—'
  let v = Math.abs(b)
  let i = 0
  while (v >= 1000 && i < BYTE_UNITS.length - 1) {
    v /= 1000
    i++
  }
  const digits = i === 0 || v >= 100 ? 0 : v >= 10 ? 1 : 2
  return `${b < 0 ? '-' : ''}${v.toFixed(digits)}${NBSP}${BYTE_UNITS[i]}`
}

export function formatPercent(ratio: number | null | undefined, digits = 0): string {
  if (ratio == null || !Number.isFinite(ratio)) return '—'
  return `${(ratio * 100).toFixed(digits)}%`
}

const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : dateTimeFmt.format(d)
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

export function formatRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return iso
  const diff = (t - now) / 1000
  const abs = Math.abs(diff)
  if (abs < 45) return rtf.format(Math.round(diff), 'second')
  if (abs < 45 * 60) return rtf.format(Math.round(diff / 60), 'minute')
  if (abs < 22 * 3600) return rtf.format(Math.round(diff / 3600), 'hour')
  return rtf.format(Math.round(diff / 86400), 'day')
}

export function humaniseKey(key: string): string {
  return key.replace(/[_.]+/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
}

/** Format a free-form signal/evidence value, using the key name as a unit hint. */
export function formatSignal(key: string, value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') {
    const k = key.toLowerCase()
    if (/(bytes_per_s|bps|throughput)/.test(k)) return `${formatBytes(value)}/s`
    if (/bytes$|_bytes|size/.test(k)) return formatBytes(value)
    if (/(_ms|millis)$/.test(k)) return formatSeconds(value / 1000)
    if (/(_s|_sec|_secs|_seconds|seconds)$/.test(k)) return formatSeconds(value)
    if (/(ratio|share|fraction|score)$/.test(k) && value >= 0 && value <= 1) return formatPercent(value, 1)
    return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { maximumFractionDigits: 3 })
  }
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}
