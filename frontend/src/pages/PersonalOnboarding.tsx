import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../store/authStore'
import { integrationsApi } from '../api/integrations'
import type { Integration } from '../types/user'
import toast from 'react-hot-toast'

interface Step {
  id: number
  title: string
}

const steps: Step[] = [
  { id: 1, title: 'Welcome' },
  { id: 2, title: 'Connect Integrations' },
  { id: 3, title: 'All Set!' },
]

export default function PersonalOnboarding() {
  const [currentStep, setCurrentStep] = useState(1)
  const [integrationsList, setIntegrationsList] = useState<Integration[]>([])
  const [connecting, setConnecting] = useState<string | null>(null)
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const completeOnboarding = useAuthStore((s) => s.completeOnboarding)

  const loadIntegrations = useCallback(async () => {
    try {
      const data = await integrationsApi.list()
      setIntegrationsList(data)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    if (currentStep === 2) loadIntegrations()
  }, [currentStep, loadIntegrations])

  const handleConnect = async (provider: string) => {
    setConnecting(provider)
    try {
      const authUrl = await integrationsApi.getAuthUrl(provider)
      window.location.href = authUrl
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Connection not available', { duration: 8000 })
      setConnecting(null)
    }
  }

  const handleNext = async () => {
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

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="max-w-2xl w-full mx-auto">

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

        <div className="bg-white rounded-lg shadow-lg p-8">

          {currentStep === 1 && (
            <div className="text-center">
              <div className="w-20 h-20 bg-primary-100 rounded-full flex items-center justify-center mx-auto mb-6">
                <svg className="w-10 h-10 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
              </div>
              <h1 className="text-3xl font-bold text-gray-900 mb-4">
                Welcome, {user?.display_name || user?.username || 'there'}!
              </h1>
              <p className="text-lg text-gray-600 mb-8">
                Let&apos;s personalize your MeetingBox experience. This will only take a moment.
              </p>
            </div>
          )}

          {currentStep === 2 && (
            <div>
              <h2 className="text-2xl font-bold text-gray-900 mb-4">Connect Your Tools (Optional)</h2>
              <p className="text-gray-600 mb-6">
                Connect Gmail and Calendar to enable AI-powered actions like drafting emails and scheduling follow-ups.
                You can skip this and set up later in Settings.
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
                            onClick={() => handleConnect(item.id)}
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

          {currentStep === 3 && (
            <div className="text-center">
              <div className="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-6">
                <svg className="w-10 h-10 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              </div>
              <h2 className="text-2xl font-bold text-gray-900 mb-4">You&apos;re All Set!</h2>
              <p className="text-gray-600 mb-8">
                Your account is ready. View meetings, manage actions, and connect integrations anytime from your dashboard.
              </p>
            </div>
          )}

          <div className="flex items-center justify-between mt-8 pt-6 border-t border-gray-200">
            {currentStep > 1 && currentStep < steps.length ? (
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
                className="px-6 py-2 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700"
              >
                {currentStep === steps.length ? 'Go to Dashboard' : 'Continue'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
