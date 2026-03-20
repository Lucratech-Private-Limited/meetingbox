import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import { useAuthStore } from './store/authStore'
import Layout from './components/layout/Layout'
import ErrorBoundary from './components/ErrorBoundary'
import Onboarding from './pages/Onboarding'
import PersonalOnboarding from './pages/PersonalOnboarding'
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
import MeetingDetail from './pages/MeetingDetail'
import LiveRecording from './pages/LiveRecording'
import Settings from './pages/Settings'
import SystemStatus from './pages/SystemStatus'

function AuthGate({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  const user = useAuthStore((s) => s.user)
  const loading = useAuthStore((s) => s.loading)

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950">
        <div className="animate-pulse text-gray-400">Loading...</div>
      </div>
    )
  }

  if (!token || !user) {
    return <Navigate to="/login" replace />
  }

  if (!user.onboarding_complete) {
    return <Navigate to="/onboarding" replace />
  }

  return <>{children}</>
}

export default function App() {
  const initialize = useAuthStore((s) => s.initialize)
  const token = useAuthStore((s) => s.token)
  const user = useAuthStore((s) => s.user)
  const hasUsers = useAuthStore((s) => s.hasUsers)
  const loading = useAuthStore((s) => s.loading)

  useEffect(() => {
    initialize()
  }, [initialize])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-950">
        <div className="animate-pulse text-gray-400">Loading...</div>
      </div>
    )
  }

  const isAuthed = !!(token && user)

  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Toaster position="top-right" />

        <Routes>
          {/* First-time device setup -- only when no users exist */}
          <Route
            path="/setup"
            element={
              hasUsers === false ? <Onboarding /> : <Navigate to="/login" />
            }
          />

          {/* Self-registration -- only when users already exist and not logged in */}
          <Route
            path="/register"
            element={
              hasUsers === false ? (
                <Navigate to="/setup" />
              ) : isAuthed ? (
                <Navigate to={user?.onboarding_complete ? '/dashboard' : '/onboarding'} />
              ) : (
                <Register />
              )
            }
          />

          {/* Login */}
          <Route
            path="/login"
            element={
              hasUsers === false ? (
                <Navigate to="/setup" />
              ) : isAuthed ? (
                <Navigate to={user?.onboarding_complete ? '/dashboard' : '/onboarding'} />
              ) : (
                <Login />
              )
            }
          />

          {/* Personal onboarding -- logged in but onboarding not complete */}
          <Route
            path="/onboarding"
            element={
              !isAuthed ? (
                <Navigate to="/login" />
              ) : user?.onboarding_complete ? (
                <Navigate to="/dashboard" />
              ) : (
                <PersonalOnboarding />
              )
            }
          />

          {/* Protected app routes */}
          <Route
            path="/"
            element={
              <AuthGate>
                <Layout />
              </AuthGate>
            }
          >
            <Route index element={<Navigate to="/dashboard" />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="meeting/:id" element={<MeetingDetail />} />
            <Route path="live" element={<LiveRecording />} />
            <Route path="settings" element={<Settings />} />
            <Route path="system" element={<SystemStatus />} />
          </Route>

          {/* Catch-all */}
          <Route
            path="*"
            element={
              hasUsers === false ? (
                <Navigate to="/setup" />
              ) : isAuthed ? (
                <Navigate to="/dashboard" />
              ) : (
                <Navigate to="/login" />
              )
            }
          />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  )
}
