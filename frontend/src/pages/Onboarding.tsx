import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../store/authStore'
import { settingsApi } from '../api/settings'
import { apiClient } from '../api/client'
import { integrationsApi } from '../api/integrations'
import type { Integration } from '../types/user'
import toast from 'react-hot-toast'

const TIMEZONE_OPTIONS = [
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'America/Anchorage', 'Pacific/Honolulu', 'America/Toronto', 'America/Vancouver',
  'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow',
  'Asia/Dubai', 'Asia/Kolkata', 'Asia/Shanghai', 'Asia/Tokyo',
  'Australia/Sydney', 'Pacific/Auckland',
]

interface OnboardingStep {
  id: number
  title: string
  description: string
}

const steps: OnboardingStep[] = [
  { id: 1, title: 'Welcome', description: "Let's set up your MeetingBox AI" },
  { id: 2, title: 'Create Account', description: 'Create your admin account' },
  { id: 3, title: 'Name Your Device', description: 'Give your MeetingBox a memorable name' },
  { id: 4, title: 'Timezone', description: 'Set your local timezone' },
  { id: 5, title: 'Connect Integrations', description: 'Optional: Connect Gmail and Calendar' },
  { id: 6, title: 'All Set!', description: "You're ready to record your first meeting" },
]

export default function Onboarding() {
  const [currentStep, setCurrentStep] = useState(1)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [deviceName, setDeviceName] = useState('')
  const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/New_York')
  const [isSaving, setIsSaving] = useState(false)
  const [integrationsList, setIntegrationsList] = useState<Integration[]>([])
  const [connecting, setConnecting] = useState<string | null>(null)
  const navigate = useNavigate()
  const setAuthFromSetup = useAuthStore((s) => s.setAuthFromSetup)
  const completeOnboarding = useAuthStore((s) => s.completeOnboarding)

  const loadIntegrations = useCallback(async () => {
    try {
      const data = await integrationsApi.list()
      setIntegrationsList(data)
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    if (currentStep === 5) loadIntegrations()
  }, [currentStep, loadIntegrations])

  const handleIntegrationConnect = async (provider: string) => {
    setConnecting(provider)
    try {
      const authUrl = await integrationsApi.getAuthUrl(provider)
      window.location.href = authUrl
    } catch (err: any) {
      const msg = err?.response?.data?.detail || 'Connection not available'
      toast.error(msg, { duration: 8000 })
      setConnecting(null)
    }
  }

  const handleNext = async () => {
    if (currentStep === 2) {
      if (!username.trim() || !password || !confirmPassword) {
        toast.error('Please fill in all fields')
        return
      }
      if (password !== confirmPassword) {
        toast.error('Passwords do not match')
        return
      }
      if (password.length < 6) {
        toast.error('Password must be at least 6 characters')
        return
      }
      setIsSaving(true)
      try {
        const { data } = await apiClient.post('/api/auth/setup', {
          username: username.trim(),
          password,
          display_name: username.trim(),
        })
        setAuthFromSetup(data.token, data.user)
      } catch (err: any) {
        const msg = err?.response?.data?.detail || 'Account creation failed'
        toast.error(msg)
        setIsSaving(false)
        return
      } finally {
        setIsSaving(false)
      }
    }

    if (currentStep === 3 && deviceName.trim()) {
      setIsSaving(true)
      try {
        await settingsApi.setDeviceName(deviceName.trim())
      } catch {
        // Non-blocking
      } finally {
        setIsSaving(false)
      }
    }

    if (currentStep === 4) {
      setIsSaving(true)
      try {
        await settingsApi.update({ timezone })
      } catch {
        // Non-blocking
      } finally {
        setIsSaving(false)
      }
    }

    if (currentStep < steps.length) {
      setCurrentStep(currentStep + 1)
    } else {
      try {
        await completeOnboarding()
        navigate('/dashboard', { replace: true })
      } catch {
        toast.error('Failed to complete setup. Please try again.')
      }
    }
  }

  const handleSkip = async () => {
    try {
      await completeOnboarding()
      navigate('/dashboard', { replace: true })
    } catch {
      toast.error('Failed to complete setup. Please try again.')
    }
  }

  const canProceed = () => {
    if (currentStep === 2) {
      return username.trim().length >= 3 && password.length >= 6 && password === confirmPassword
    }
    return true
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="max-w-2xl w-full mx-auto">

        {/* Progress bar */}
        <div className="mb-8">
          <div className="flex items-center justify-between mb-2">
            {steps.map((step) => (
              <div
                key={step.id}
                className={`flex-1 h-1 mx-1 rounded transition-colors ${
                  step.id <= currentStep ? 'bg-primary-600' : 'bg-gray-200'
                }`}
              />
            ))}
          </div>
          <p className="text-sm text-gray-500 text-center">
            Step {currentStep} of {steps.length}
          </p>
        </div>

        {/* Step content */}
        <div className="bg-white rounded-lg shadow-lg p-8">

          {/* Step 1: Welcome */}
          {currentStep === 1 && (
            <div className="text-center">
              <div className="w-20 h-20 bg-primary-100 rounded-full flex items-center justify-center mx-auto mb-6">
                <svg className="w-10 h-10 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <h1 className="text-3xl font-bold text-gray-900 mb-4">Welcome to MeetingBox AI</h1>
              <p className="text-lg text-gray-600 mb-8">
                Let&apos;s get you set up in just a few minutes. You&apos;ll be recording AI-powered meeting notes in no time.
              </p>
            </div>
          )}

          {/* Step 2: Create Account */}
          {currentStep === 2 && (
            <div>
              <h2 className="text-2xl font-bold text-gray-900 mb-4">Create Your Account</h2>
              <p className="text-gray-600 mb-6">
                This will be your admin account for managing MeetingBox.
              </p>
              <div className="space-y-4">
                <div>
                  <label htmlFor="onb-username" className="block text-sm font-medium text-gray-700 mb-1">
                    Username
                  </label>
                  <input
                    type="text"
                    id="onb-username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="admin"
                    autoComplete="username"
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  />
                </div>
                <div>
                  <label htmlFor="onb-password" className="block text-sm font-medium text-gray-700 mb-1">
                    Password
                  </label>
                  <input
                    type="password"
                    id="onb-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="At least 6 characters"
                    autoComplete="new-password"
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  />
                </div>
                <div>
                  <label htmlFor="onb-confirm" className="block text-sm font-medium text-gray-700 mb-1">
                    Confirm Password
                  </label>
                  <input
                    type="password"
                    id="onb-confirm"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Re-enter your password"
                    autoComplete="new-password"
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  />
                  {confirmPassword && password !== confirmPassword && (
                    <p className="mt-1 text-sm text-red-500">Passwords do not match</p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Step 3: Name your device */}
          {currentStep === 3 && (
            <div>
              <h2 className="text-2xl font-bold text-gray-900 mb-4">Name Your MeetingBox</h2>
              <p className="text-gray-600 mb-6">
                This name will appear on your network and in the dashboard.
              </p>
              <div className="mb-6">
                <label htmlFor="deviceName" className="block text-sm font-medium text-gray-700 mb-2">
                  Device Name
                </label>
                <input
                  type="text"
                  id="deviceName"
                  value={deviceName}
                  onChange={(e) => setDeviceName(e.target.value)}
                  placeholder="Conference Room A"
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                />
                <p className="mt-2 text-sm text-gray-500">
                  Suggestion: Use your room name or location
                </p>
              </div>
            </div>
          )}

          {/* Step 4: Timezone */}
          {currentStep === 4 && (
            <div>
              <h2 className="text-2xl font-bold text-gray-900 mb-4">Set Your Timezone</h2>
              <p className="text-gray-600 mb-6">
                We&apos;ve auto-detected your timezone. Change it if needed.
              </p>
              <div className="mb-6">
                <label htmlFor="timezone" className="block text-sm font-medium text-gray-700 mb-2">
                  Timezone
                </label>
                <select
                  id="timezone"
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent bg-white"
                >
                  {TIMEZONE_OPTIONS.map((tz) => (
                    <option key={tz} value={tz}>{tz.replace(/_/g, ' ')}</option>
                  ))}
                </select>
              </div>
            </div>
          )}

          {/* Step 5: Integrations */}
          {currentStep === 5 && (
            <div>
              <h2 className="text-2xl font-bold text-gray-900 mb-4">Connect Your Tools (Optional)</h2>
              <p className="text-gray-600 mb-6">
                Connect Gmail and Calendar to enable AI-powered actions. You can skip this and set up later in Settings.
              </p>

              <div className="space-y-4">
                {[
                  { id: 'gmail', name: 'Gmail', desc: 'Send AI-drafted emails', bgColor: 'bg-red-100', iconColor: 'text-red-600',
                    icon: <><path d="M2.003 5.884L10 9.882l7.997-3.998A2 2 0 0016 4H4a2 2 0 00-1.997 1.884z" /><path d="M18 8.118l-8 4-8-4V14a2 2 0 002 2h12a2 2 0 002-2V8.118z" /></> },
                  { id: 'calendar', name: 'Google Calendar', desc: 'Auto-schedule meetings', bgColor: 'bg-blue-100', iconColor: 'text-blue-600',
                    icon: <path fillRule="evenodd" d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z" clipRule="evenodd" /> },
                ].map((item) => {
                  const connected = integrationsList.find(i => i.id === item.id)?.connected ?? false
                  const email = integrationsList.find(i => i.id === item.id)?.email
                  return (
                    <div key={item.id} className="border border-gray-200 rounded-lg p-4 hover:border-primary-500 transition-colors">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-3">
                          <div className={`w-10 h-10 ${item.bgColor} rounded-lg flex items-center justify-center`}>
                            <svg className={`w-6 h-6 ${item.iconColor}`} fill="currentColor" viewBox="0 0 20 20">
                              {item.icon}
                            </svg>
                          </div>
                          <div>
                            <h3 className="font-medium text-gray-900">{item.name}</h3>
                            <p className="text-sm text-gray-500">{item.desc}</p>
                            {connected && email && (
                              <p className="text-xs text-green-600">{email}</p>
                            )}
                          </div>
                        </div>
                        {connected ? (
                          <span className="inline-flex items-center px-3 py-1 rounded-full text-sm bg-green-100 text-green-800">
                            Connected
                          </span>
                        ) : (
                          <button
                            onClick={() => handleIntegrationConnect(item.id)}
                            disabled={connecting !== null}
                            className="px-4 py-2 text-sm font-medium text-primary-700 bg-primary-50 rounded-lg hover:bg-primary-100 disabled:opacity-50"
                          >
                            {connecting === item.id ? 'Redirecting...' : 'Connect'}
                          </button>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Step 6: Complete */}
          {currentStep === 6 && (
            <div className="text-center">
              <div className="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-6">
                <svg className="w-10 h-10 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <h2 className="text-2xl font-bold text-gray-900 mb-4">You&apos;re All Set!</h2>
              <p className="text-gray-600 mb-8">
                Your MeetingBox AI is ready to use. Press the button on the device to start recording, or use this dashboard to manage your meetings.
              </p>
              <div className="bg-primary-50 border border-primary-200 rounded-lg p-4">
                <p className="text-sm text-primary-800 font-medium mb-2">Quick Tip</p>
                <p className="text-sm text-primary-700">
                  Just press the button on your MeetingBox to start recording. We&apos;ll handle the rest &mdash; transcription, summary, and action items.
                </p>
              </div>
            </div>
          )}

          {/* Navigation buttons */}
          <div className="flex items-center justify-between mt-8 pt-6 border-t border-gray-200">
            {currentStep > 2 && currentStep < steps.length ? (
              <button onClick={handleSkip} className="text-sm text-gray-500 hover:text-gray-700">
                Skip for now
              </button>
            ) : (
              <div />
            )}
            <div className="flex space-x-3">
              {currentStep > 1 && currentStep < steps.length && (
                <button
                  onClick={() => setCurrentStep(currentStep - 1)}
                  className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
                >
                  Back
                </button>
              )}
              <button
                onClick={handleNext}
                disabled={isSaving || !canProceed()}
                className="px-6 py-2 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 disabled:opacity-50"
              >
                {isSaving
                  ? 'Saving...'
                  : currentStep === steps.length
                    ? 'Go to Dashboard'
                    : 'Continue'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
