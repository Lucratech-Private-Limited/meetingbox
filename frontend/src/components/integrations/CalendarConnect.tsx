// Google Calendar integration connect button

import { integrationsApi } from '../../api/integrations'
import toast from 'react-hot-toast'

interface CalendarConnectProps {
  connected: boolean
  onStatusChange: () => void
}

export default function CalendarConnect({ connected, onStatusChange }: CalendarConnectProps) {
  const handleConnect = async () => {
    try {
      const authUrl = await integrationsApi.getAuthUrl('calendar')
      window.location.href = authUrl
    } catch {
      toast.error('Failed to connect Calendar')
    }
  }

  const handleDisconnect = async () => {
    try {
      await integrationsApi.disconnect('calendar')
      toast.success('Calendar disconnected')
      onStatusChange()
    } catch {
      toast.error('Failed to disconnect Calendar')
    }
  }

  return (
    <div className="border border-gray-200 rounded-lg p-4 hover:border-primary-500 transition-colors">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
            <svg className="w-6 h-6 text-blue-600" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z" clipRule="evenodd" />
            </svg>
          </div>
          <div>
            <h3 className="font-medium text-gray-900">Google Calendar</h3>
            <p className="text-sm text-gray-500">Auto-schedule meetings</p>
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
