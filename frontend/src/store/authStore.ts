import { create } from 'zustand'
import { apiClient } from '../api/client'

interface User {
  id: string
  username: string
  display_name: string
  role: string
  onboarding_complete: boolean
}

interface AuthState {
  token: string | null
  user: User | null
  hasUsers: boolean | null
  loading: boolean

  initialize: () => Promise<void>
  login: (username: string, password: string) => Promise<{ token: string; user: User }>
  register: (username: string, password: string, displayName: string) => Promise<void>
  logout: () => void
  setAuthFromSetup: (token: string, user: User) => void
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

  login: async (username: string, password: string) => {
    const { data } = await apiClient.post('/api/auth/login', { username, password })
    localStorage.setItem('auth_token', data.token)
    set({ token: data.token, user: data.user, hasUsers: true })
    return data
  },

  register: async (username: string, password: string, displayName: string) => {
    const { data } = await apiClient.post('/api/auth/register', {
      username,
      password,
      display_name: displayName,
    })
    localStorage.setItem('auth_token', data.token)
    set({ token: data.token, user: data.user, hasUsers: true })
  },

  logout: () => {
    localStorage.removeItem('auth_token')
    set({ token: null, user: null })
    window.location.href = '/login'
  },

  setAuthFromSetup: (token: string, user: User) => {
    localStorage.setItem('auth_token', token)
    set({ token, user })
  },

  completeOnboarding: async () => {
    await apiClient.post('/api/auth/complete-onboarding')
    set((state) => ({
      user: state.user ? { ...state.user, onboarding_complete: true } : null,
      hasUsers: true,
    }))
  },
}))
