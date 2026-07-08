// Gmail-specific integration connect button

import { integrationsApi } from '../../api/integrations'
import toast from 'react-hot-toast'

interface GmailConnectProps {
  connected: boolean
  onStatusChange: () => void
}

export default function GmailConnect({ connected, onStatusChange }: GmailConnectProps) {
  const handleConnect = async () => {
    try {
      const authUrl = await integrationsApi.getAuthUrl('gmail')
      window.location.href = authUrl
    } catch {
      toast.error('Failed to connect Gmail')
    }
  }

  const handleDisconnect = async () => {
    try {
      await integrationsApi.disconnect('gmail')
      toast.success('Gmail disconnected')
      onStatusChange()
    } catch {
      toast.error('Failed to disconnect Gmail')
    }
  }

  return (
    <div className="border border-gray-200 rounded-lg p-4 hover:border-primary-500 transition-colors">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 bg-red-100 rounded-lg flex items-center justify-center">
            <svg className="w-6 h-6 text-red-600" fill="currentColor" viewBox="0 0 20 20">
              <path d="M2.003 5.884L10 9.882l7.997-3.998A2 2 0 0016 4H4a2 2 0 00-1.997 1.884z" />
              <path d="M18 8.118l-8 4-8-4V14a2 2 0 002 2h12a2 2 0 002-2V8.118z" />
            </svg>
          </div>
          <div>
            <h3 className="font-medium text-gray-900">Gmail</h3>
            <p className="text-sm text-gray-500">Send AI-drafted emails</p>
          </div>
        </div>
        {connected ? (
          <button
            onClick={handleDisconnect}
            className="px-4 py-2 text-sm font-medium text-red-700 bg-red-50 rounded-lg hover:bg-red-100"
          >
            Disconnect
          </button>
        ) : (
          <button
            onClick={handleConnect}
            className="px-4 py-2 text-sm font-medium text-primary-700 bg-primary-50 rounded-lg hover:bg-primary-100"
          >
            Connect
          </button>
        )}
      </div>
    </div>
  )
}
