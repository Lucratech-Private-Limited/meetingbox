import { useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { useAuthStore } from '../store/authStore'

export default function AuthCallback() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const consumeGoogleCallback = useAuthStore((s) => s.consumeGoogleCallback)

  useEffect(() => {
    const token = searchParams.get('token')
    if (!token) {
      toast.error('Missing sign-in token')
      navigate('/login', { replace: true })
      return
    }

    let active = true
    consumeGoogleCallback(token)
      .then((user) => {
        if (!active) return
        navigate(user.onboarding_complete ? '/dashboard' : '/onboarding', { replace: true })
      })
      .catch(() => {
        if (!active) return
        toast.error('Sign-in failed')
        navigate('/login', { replace: true })
      })

    return () => {
      active = false
    }
  }, [consumeGoogleCallback, navigate, searchParams])

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950 px-4">
      <div className="text-sm text-gray-400">Signing you in...</div>
    </div>
  )
}
