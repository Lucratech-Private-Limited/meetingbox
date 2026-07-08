import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useAuthStore } from '../store/authStore'
import toast from 'react-hot-toast'

export default function Login() {
  const [submitting, setSubmitting] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()
  const startGoogleSignIn = useAuthStore((s) => s.startGoogleSignIn)

  useEffect(() => {
    const error = searchParams.get('error')
    if (!error) return
    toast.error(error.replace(/_/g, ' '))
    searchParams.delete('error')
    setSearchParams(searchParams, { replace: true })
  }, [searchParams, setSearchParams])

  const handleGoogleSignIn = async () => {
    setSubmitting(true)
    try {
      await startGoogleSignIn()
    } catch (err: unknown) {
      const ax = err as { response?: { data?: { detail?: unknown } }; message?: string }
      // Offline / wrong API URL: axios interceptor already toasts; avoid duplicate.
      if (!ax?.response) return
      const detail = ax.response?.data?.detail
      const msg =
        (typeof detail === 'string' && detail) ||
        (typeof ax?.message === 'string' && ax.message) ||
        'Unable to start Google sign-in'
      toast.error(msg)
    } finally {
      // If assign() did not navigate away (bad URL, blocked redirect, offline API), re-enable.
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-app-page px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-white tracking-tight">
            MeetingBox <span className="text-blue-400">AI</span>
          </h1>
          <p className="text-app-ink-faint mt-2 text-sm">Sign in or sign up with Google</p>
        </div>

        <div className="space-y-5">
          <button
            type="button"
            onClick={handleGoogleSignIn}
            disabled={submitting}
            className="w-full rounded-lg border border-app-border-light bg-app-surface px-4 py-3 text-sm font-semibold text-app-ink hover:bg-app-raised disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            {submitting ? 'Redirecting...' : 'Continue with Google'}
          </button>
          <p className="text-center text-sm text-app-ink-subtle">
            Your Google account is only used for dashboard access. Gmail and Calendar stay optional in the Integrations tab.
          </p>
          <p className="text-center text-xs text-app-ink-muted">
            <Link to="/privacy" className="underline hover:text-app-ink-faint">
              Privacy Policy
            </Link>
            <span className="mx-2">·</span>
            <Link to="/terms" className="underline hover:text-app-ink-faint">
              Terms of Service
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
