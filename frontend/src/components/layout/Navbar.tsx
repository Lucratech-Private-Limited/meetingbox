// Top navigation bar with links to main sections

import { Link, useLocation } from 'react-router-dom'
import { APP_NAME } from '../../utils/constants'
import { useAuthStore } from '../../store/authStore'

const navigation = [
  { name: 'Dashboard', href: '/dashboard' },
  { name: 'Settings', href: '/settings' },
  { name: 'System', href: '/system' },
]

export default function Navbar() {
  const location = useLocation()
  const logout = useAuthStore((s) => s.logout)
  const user = useAuthStore((s) => s.user)

  return (
    <nav className="bg-white shadow-sm">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16">
          <div className="flex">
            {/* Brand */}
            <Link
              to="/dashboard"
              className="flex-shrink-0 flex items-center"
            >
              <h1 className="text-xl font-bold text-gray-900">{APP_NAME}</h1>
            </Link>

            {/* Nav links */}
            <div className="hidden sm:ml-6 sm:flex sm:space-x-8">
              {navigation.map((item) => {
                const isActive = location.pathname.startsWith(item.href)
                return (
                  <Link
                    key={item.name}
                    to={item.href}
                    className={`inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium ${
                      isActive
                        ? 'border-primary-500 text-gray-900'
                        : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
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
            {user && (
              <span className="text-sm text-gray-500">{user.display_name}</span>
            )}
            <button
              onClick={logout}
              className="text-sm font-medium text-gray-500 hover:text-gray-700"
            >
              Logout
            </button>
          </div>

          {/* Mobile menu (simplified) */}
          <div className="flex items-center sm:hidden">
            <div className="flex space-x-4">
              {navigation.map((item) => (
                <Link
                  key={item.name}
                  to={item.href}
                  className="text-sm font-medium text-gray-500 hover:text-gray-700"
                >
                  {item.name}
                </Link>
              ))}
              <button
                onClick={logout}
                className="text-sm font-medium text-gray-500 hover:text-gray-700"
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
