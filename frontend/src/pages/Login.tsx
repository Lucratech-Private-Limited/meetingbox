import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
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
    } catch (err: any) {
      const msg = err?.response?.data?.detail || 'Unable to start Google sign-in'
      toast.error(msg)
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950 px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-white tracking-tight">
            MeetingBox <span className="text-blue-400">AI</span>
          </h1>
          <p className="text-gray-400 mt-2 text-sm">Sign in or sign up with Google</p>
        </div>

        <div className="space-y-5">
          <button
            type="button"
            onClick={handleGoogleSignIn}
            disabled={submitting}
            className="w-full rounded-lg border border-gray-700 bg-white px-4 py-3 text-sm font-semibold text-gray-900 hover:bg-gray-100 disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            {submitting ? 'Redirecting...' : 'Continue with Google'}
          </button>
          <p className="text-center text-sm text-gray-500">
            Your Google account is only used for dashboard access. Gmail and Calendar stay optional in the Integrations tab.
          </p>
        </div>
      </div>
    </div>
  )
}
