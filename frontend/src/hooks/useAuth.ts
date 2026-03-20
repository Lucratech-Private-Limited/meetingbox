import { useAuthStore } from '../store/authStore'

export function useAuth() {
  const { token, user, logout, completeOnboarding } = useAuthStore()

  return {
    isAuthenticated: !!token && !!user,
    user,
    onboardingComplete: user?.onboarding_complete ?? false,
    completeOnboarding,
    logout,
  }
}
