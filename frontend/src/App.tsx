import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import { useAuthStore } from './store/authStore'
import Layout from './components/layout/Layout'
import DashboardLayout from './components/layout/DashboardLayout'
import ErrorBoundary from './components/ErrorBoundary'
import PersonalOnboarding from './pages/PersonalOnboarding'
import Login from './pages/Login'
import Register from './pages/Register'
import AuthCallback from './pages/AuthCallback'
import Dashboard from './pages/Dashboard'
import MeetingDetail from './pages/MeetingDetail'
import LiveRecording from './pages/LiveRecording'
import Settings from './pages/Settings'
import SystemStatus from './pages/SystemStatus'
import AssistantChat from './pages/AssistantChat'
import Calendar from './pages/Calendar'
import Meetings from './pages/Meetings'
import Emails from './pages/Emails'
import Tasks from './pages/Tasks'
import Privacy from './pages/Privacy'
import Terms from './pages/Terms'
import KioskIdle from './pages/KioskIdle'

function AuthGate({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  const user = useAuthStore((s) => s.user)
  const loading = useAuthStore((s) => s.loading)

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-app-page">
        <div className="animate-pulse text-app-ink-faint">Loading...</div>
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
  const loading = useAuthStore((s) => s.loading)

  useEffect(() => {
    initialize()
  }, [initialize])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-app-page">
        <div className="animate-pulse text-app-ink-faint">Loading...</div>
      </div>
    )
  }

  const isAuthed = !!(token && user)

  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Toaster
          position="top-right"
          toastOptions={{
            className: '!bg-app-surface !text-app-ink !border !border-app-border',
            duration: 4000,
          }}
        />

        <Routes>
          <Route path="/setup" element={<Navigate to="/login" replace />} />
          <Route path="/register" element={<Register />} />

          <Route
            path="/login"
            element={
              isAuthed ? (
                <Navigate to={user?.onboarding_complete ? '/dashboard' : '/onboarding'} />
              ) : (
                <Login />
              )
            }
          />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route path="/terms" element={<Terms />} />
          {/* Pixel-faithful kiosk/tablet idle preview (Figma #338:60) — fullscreen, no chrome */}
          <Route path="/kiosk/idle" element={<KioskIdle />} />

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

          {/* Protected app routes — full-screen dashboard shell (no global Navbar) */}
          <Route
            path="/"
            element={
              <AuthGate>
                <DashboardLayout />
              </AuthGate>
            }
          >
            <Route index element={<Navigate to="/dashboard" />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="meetings" element={<Meetings />} />
            <Route path="emails" element={<Emails />} />
            <Route path="tasks" element={<Tasks />} />
            <Route path="calendar" element={<Calendar />} />
            <Route path="meeting/:id" element={<MeetingDetail />} />
            <Route path="live" element={<LiveRecording />} />
            <Route path="settings" element={<Settings />} />
            <Route path="system" element={<SystemStatus />} />
            <Route path="assistant" element={<AssistantChat />} />
          </Route>

          {/* Catch-all */}
          <Route
            path="*"
            element={
              isAuthed ? (
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
