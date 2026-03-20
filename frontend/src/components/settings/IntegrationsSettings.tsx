import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { integrationsApi } from '../../api/integrations'
import type { Integration } from '../../types/user'
import LoadingSpinner from '../ui/LoadingSpinner'
import toast from 'react-hot-toast'

export default function IntegrationsSettings() {
  const [integrations, setIntegrations] = useState<Integration[]>([])
  const [loading, setLoading] = useState(true)
  const [connecting, setConnecting] = useState<string | null>(null)
  const [searchParams, setSearchParams] = useSearchParams()

  const loadIntegrations = useCallback(async () => {
    try {
      const data = await integrationsApi.list()
      setIntegrations(data)
    } catch {
      setIntegrations([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadIntegrations()
  }, [loadIntegrations])

  useEffect(() => {
    const status = searchParams.get('integration')
    if (!status) return

    if (status === 'success') {
      const provider = searchParams.get('provider') || 'Integration'
      const email = searchParams.get('email')
      const msg = email ? `${provider} connected as ${email}!` : `${provider} connected!`
      toast.success(msg, { duration: 5000 })
      loadIntegrations()
    } else if (status === 'error') {
      const reason = searchParams.get('reason') || 'Unknown error'
      toast.error(`Connection failed: ${reason.replace(/_/g, ' ')}`, { duration: 8000 })
    }

    searchParams.delete('integration')
    searchParams.delete('provider')
    searchParams.delete('email')
    searchParams.delete('reason')
    setSearchParams(searchParams, { replace: true })
  }, [searchParams, setSearchParams, loadIntegrations])

  const handleConnect = async (provider: string) => {
    setConnecting(provider)
    try {
      const authUrl = await integrationsApi.getAuthUrl(provider)
      window.location.href = authUrl
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      const status = err?.response?.status
      let msg = detail || 'Failed to start connection'
      if (status === 503 && !detail) {
        msg = 'Google OAuth is not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env.'
      }
      toast.error(msg, { duration: 8000 })
      setConnecting(null)
    }
  }

  const handleDisconnect = async (provider: string) => {
    try {
      await integrationsApi.disconnect(provider)
      toast.success('Disconnected')
      loadIntegrations()
    } catch {
      toast.error('Failed to disconnect')
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <LoadingSpinner />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {integrations.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 p-6 text-center">
          <p className="text-gray-500">
            No integrations available. Ensure the backend is running and Google OAuth credentials are configured.
          </p>
        </div>
      ) : (
        integrations.map((integration) => (
          <div
            key={integration.id}
            className="bg-white rounded-lg border border-gray-200 p-6"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-4">
                <div className="w-12 h-12 rounded-lg bg-gray-100 flex items-center justify-center text-2xl">
                  {integration.id === 'gmail' ? (
                    <svg className="w-7 h-7 text-red-500" fill="currentColor" viewBox="0 0 20 20">
                      <path d="M2.003 5.884L10 9.882l7.997-3.998A2 2 0 0016 4H4a2 2 0 00-1.997 1.884z" />
                      <path d="M18 8.118l-8 4-8-4V14a2 2 0 002 2h12a2 2 0 002-2V8.118z" />
                    </svg>
                  ) : (
                    <svg className="w-7 h-7 text-blue-500" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z" clipRule="evenodd" />
                    </svg>
                  )}
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-gray-900">{integration.name}</h3>
                  <p className="text-sm text-gray-600">{integration.description}</p>
                  {integration.connected && integration.email && (
                    <p className="text-xs text-green-600 mt-1">Connected as {integration.email}</p>
                  )}
                </div>
              </div>

              {integration.connected ? (
                <div className="flex items-center space-x-3">
                  <span className="inline-flex items-center px-3 py-1 rounded-full text-sm bg-green-100 text-green-800">
                    Connected
                  </span>
                  <button
                    onClick={() => handleDisconnect(integration.id)}
                    className="px-4 py-2 text-sm font-medium text-red-700 bg-red-50 rounded-lg hover:bg-red-100"
                  >
                    Disconnect
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => handleConnect(integration.id)}
                  disabled={connecting !== null}
                  className="px-4 py-2 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 disabled:opacity-50"
                >
                  {connecting === integration.id ? 'Redirecting...' : 'Connect'}
                </button>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  )
}
