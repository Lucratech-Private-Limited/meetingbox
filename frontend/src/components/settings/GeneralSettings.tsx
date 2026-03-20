// General settings — device name, timezone

import { useState, useEffect } from 'react'
import { settingsApi } from '../../api/settings'
import { TIMEZONES } from '../../utils/constants'
import toast from 'react-hot-toast'

export default function GeneralSettings() {
  const [deviceName, setDeviceName] = useState('')
  const [timezone, setTimezone] = useState('America/New_York')
  const [isSaving, setIsSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)

  // Fetch current settings on mount
  useEffect(() => {
    const load = async () => {
      try {
        const data = await settingsApi.get()
        if (data.device_name) setDeviceName(data.device_name)
        if (data.timezone) setTimezone(data.timezone)
      } catch {
        // Use defaults
      } finally {
        setLoaded(true)
      }
    }
    load()
  }, [])

  const handleSave = async () => {
    try {
      setIsSaving(true)
      await settingsApi.update({ device_name: deviceName, timezone })
      toast.success('Settings saved')
    } catch {
      toast.error('Failed to save settings')
    } finally {
      setIsSaving(false)
    }
  }

  if (!loaded) {
    return <div className="text-center py-8 text-gray-500">Loading...</div>
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-6">
      {/* Device Name */}
      <div>
        <label htmlFor="deviceName" className="block text-sm font-medium text-gray-700 mb-2">
          Device Name
        </label>
        <input
          type="text"
          id="deviceName"
          value={deviceName}
          onChange={(e) => setDeviceName(e.target.value)}
          className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
        />
        <p className="mt-1 text-sm text-gray-500">
          This name appears on your network and in the dashboard
        </p>
      </div>

      {/* Timezone */}
      <div>
        <label htmlFor="timezone" className="block text-sm font-medium text-gray-700 mb-2">
          Timezone
        </label>
        <select
          id="timezone"
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
        >
          {TIMEZONES.map((tz) => (
            <option key={tz.value} value={tz.value}>
              {tz.label}
            </option>
          ))}
        </select>
      </div>

      {/* Save */}
      <div className="pt-4 border-t border-gray-200">
        <button
          onClick={handleSave}
          disabled={isSaving}
          className="px-6 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
        >
          {isSaving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>
    </div>
  )
}
