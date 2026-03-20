// Axios API client with interceptors for auth and error handling

import axios from 'axios'
import toast from 'react-hot-toast'

const client = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '',
  timeout: 300000, // 5 minutes — local LLM inference can be slow on edge devices
  headers: {
    'Content-Type': 'application/json',
  },
})

// Request interceptor — attach auth token if present
client.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('auth_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// Response interceptor — global error handling
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const url = error.config?.url || ''
      // Don't redirect during auth initialization — let authStore handle cleanup
      if (!url.includes('/api/auth/me') && !url.includes('/api/auth/has-users')) {
        localStorage.removeItem('auth_token')
        window.location.href = '/login'
      }
    } else if (error.response?.status >= 500) {
      toast.error('Server error. Please try again later.')
    } else if (!error.response) {
      toast.error('Network error. Check your connection.')
    }
    return Promise.reject(error)
  }
)

export { client as apiClient }
export default client
