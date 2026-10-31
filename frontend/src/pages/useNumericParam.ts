import { useParams } from 'react-router-dom'

/** Reads a positive integer route param; returns NaN when missing or malformed. */
export function useNumericParam(name = 'id'): number {
  const raw = useParams()[name]
  const n = raw && /^\d+$/.test(raw) ? Number(raw) : Number.NaN
  return n
}
