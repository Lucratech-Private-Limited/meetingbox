// Top navigation bar with links to main sections

import { Link, useLocation } from 'react-router-dom'
import { APP_NAME } from '../../utils/constants'
import { useAuthStore } from '../../store/authStore'
import { useTutorialStore } from '../../store/tutorialStore'

type NavLinkItem = { name: string; href: string; exact?: boolean }

const navigation: NavLinkItem[] = [
  { name: 'Dashboard', href: '/dashboard', exact: true },
  { name: 'Meetings', href: '/meetings' },
  { name: 'Calendar', href: '/calendar' },
  { name: 'Emails', href: '/emails' },
  { name: 'Tasks', href: '/tasks' },
  { name: 'Assistant', href: '/assistant' },
  { name: 'Settings', href: '/settings' },
  { name: 'System', href: '/system' },
]

function navItemActive(pathname: string, href: string, exact?: boolean): boolean {
  const p = pathname.replace(/\/$/, '') || '/'
  const h = href.replace(/\/$/, '') || '/'
  if (exact) return p === h
  return p === h || p.startsWith(`${h}/`)
}

export default function Navbar() {
  const location = useLocation()
  const logout = useAuthStore((s) => s.logout)
  const user = useAuthStore((s) => s.user)
  const startTutorial = useTutorialStore((s) => s.start)

  return (
    <nav className="bg-app-navbar border-b border-app-border shadow-sm" data-tutorial="nav-main">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16">
          <div className="flex">
            {/* Brand */}
            <Link
              to="/dashboard"
              className="flex-shrink-0 flex items-center"
            >
              <h1 className="text-xl font-bold text-app-ink">{APP_NAME}</h1>
            </Link>

            {/* Nav links */}
            <div className="hidden sm:ml-6 sm:flex sm:space-x-8">
              {navigation.map((item) => {
                const isActive = navItemActive(location.pathname, item.href, item.exact)
                return (
                  <Link
                    key={item.name}
                    to={item.href}
                    className={`inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium ${
                      isActive
                        ? 'border-primary-500 text-app-ink'
                        : 'border-transparent text-app-ink-subtle hover:border-app-border-light hover:text-app-ink-muted'
                    }`}
                  >
                    {item.name}
                  </Link>
                )
              })}
            </div>
          </div>

          {/* Right side: user + logout */}
          <div className="hidden sm:flex sm:items-center sm:space-x-4">
            <button
              type="button"
              onClick={startTutorial}
              className="text-sm font-medium text-primary-600 hover:text-primary-800"
              data-tutorial="nav-tour"
            >
              Tour
            </button>
            {user && (
              <span className="text-sm text-app-ink-subtle">{user.display_name}</span>
            )}
            <button
              onClick={logout}
              className="text-sm font-medium text-app-ink-subtle hover:text-app-ink-muted"
            >
              Logout
            </button>
          </div>

          {/* Mobile menu (simplified) */}
          <div className="flex items-center sm:hidden">
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {navigation.map((item) => (
                <Link
                  key={item.name}
                  to={item.href}
                  className="text-sm font-medium text-app-ink-subtle hover:text-app-ink-muted"
                >
                  {item.name}
                </Link>
              ))}
              <button
                type="button"
                onClick={startTutorial}
                className="text-sm font-medium text-primary-600 hover:text-primary-800"
              >
                Tour
              </button>
              <button
                onClick={logout}
                className="text-sm font-medium text-app-ink-subtle hover:text-app-ink-muted"
              >
                Logout
              </button>
            </div>
          </div>
        </div>
      </div>
    </nav>
  )
}
