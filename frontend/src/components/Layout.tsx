import { NavLink, Outlet } from 'react-router-dom'
import { useHealth } from '../api/hooks'
import StatusDot, { type DotStatus } from './StatusDot'

interface NavItem {
  to: string
  label: string
  end?: boolean
}

/** Top-level navigation. Phase 3+ pages (diagnoses, fixes, experiments) slot in here. */
export const NAV_ITEMS: NavItem[] = [{ to: '/', label: 'Overview', end: true }]

export default function Layout() {
  const health = useHealth()

  let overall: DotStatus = 'unknown'
  let overallLabel = 'Checking…'
  if (health.isError) {
    overall = 'down'
    overallLabel = 'API unreachable'
  } else if (health.data) {
    overall = health.data.status === 'ok' ? 'ok' : 'warn'
    overallLabel = health.data.status === 'ok' ? 'All systems OK' : 'Degraded'
  }

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2 font-semibold text-fg no-underline hover:no-underline">
            <img src="/favicon.svg" alt="" className="size-6" />
            <span>KubeSage</span>
            <span className="hidden text-xs font-normal text-muted sm:inline">cold-start profiler</span>
          </NavLink>
          <nav aria-label="Primary" className="flex-1">
            <ul className="flex gap-1">
              {NAV_ITEMS.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) =>
                      `rounded-md px-3 py-1.5 text-sm no-underline hover:no-underline ${
                        isActive ? 'bg-accent-soft font-medium text-fg' : 'text-muted hover:bg-surface-2 hover:text-fg'
                      }`
                    }
                  >
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <div aria-live="polite">
            <StatusDot status={overall} label={overallLabel} />
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
        <Outlet />
      </main>
      <footer className="border-t border-line py-3 text-center text-xs text-muted">KubeSage · Phase 2</footer>
    </div>
  )
}
