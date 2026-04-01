import { create } from 'zustand'
import { apiClient } from '../api/client'

interface User {
  id: string
  username: string
  email?: string | null
  display_name: string
  role: string
  onboarding_complete: boolean
  avatar_url?: string | null
}

interface AuthState {
  token: string | null
  user: User | null
  hasUsers: boolean | null
  loading: boolean

  initialize: () => Promise<void>
  startGoogleSignIn: () => Promise<void>
  consumeGoogleCallback: (token: string) => Promise<User>
  logout: () => void
  completeOnboarding: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem('auth_token'),
  user: null,
  hasUsers: null,
  loading: true,

  initialize: async () => {
    const checkAuth = async () => {
      const token = localStorage.getItem('auth_token')
      if (!token) return { token: null, user: null }
      try {
        const { data } = await apiClient.get('/api/auth/me')
        return { token, user: data as User }
      } catch {
        localStorage.removeItem('auth_token')
        return { token: null, user: null }
      }
    }

    const checkHasUsers = async () => {
      try {
        const { data } = await apiClient.get('/api/auth/has-users')
        return data.has_users as boolean
      } catch {
        return null
      }
    }

    const [authResult, hasUsers] = await Promise.all([checkAuth(), checkHasUsers()])
    set({
      token: authResult.token,
      user: authResult.user,
      hasUsers,
      loading: false,
    })
  },

  startGoogleSignIn: async () => {
    const { data } = await apiClient.get('/api/auth/google/auth-url')
    window.location.href = data.auth_url
  },

  logout: () => {
    localStorage.removeItem('auth_token')
    set({ token: null, user: null })
    window.location.href = '/login'
  },

  consumeGoogleCallback: async (token: string) => {
    localStorage.setItem('auth_token', token)
    try {
      const { data } = await apiClient.get('/api/auth/me')
      const user = data as User
      set({ token, user, hasUsers: true })
      return user
    } catch (err) {
      localStorage.removeItem('auth_token')
      set({ token: null, user: null })
      throw err
    }
  },

  completeOnboarding: async () => {
    await apiClient.post('/api/auth/complete-onboarding')
    set((state) => ({
      user: state.user ? { ...state.user, onboarding_complete: true } : null,
      hasUsers: true,
    }))
  },
}))
