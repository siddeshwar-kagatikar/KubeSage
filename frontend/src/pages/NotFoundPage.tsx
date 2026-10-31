import { Link } from 'react-router-dom'

export default function NotFoundPage({ what = 'Page' }: { what?: string }) {
  return (
    <div className="space-y-3 py-10 text-center">
      <h1 className="text-xl font-semibold">{what} not found</h1>
      <p className="text-sm text-muted">It may have been removed, or the link is wrong.</p>
      <p>
        <Link to="/">Back to overview</Link>
      </p>
    </div>
  )
}
