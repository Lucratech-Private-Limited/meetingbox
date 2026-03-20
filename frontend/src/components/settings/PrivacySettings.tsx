import { useState, useEffect } from 'react'
import toast from 'react-hot-toast'
import { settingsApi } from '../../api/settings'

export default function PrivacySettings() {
  const [autoRecord, setAutoRecord] = useState(false)
  const [autoSummarize, setAutoSummarize] = useState(true)
  const [retentionDays, setRetentionDays] = useState(90)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const load = async () => {
      try {
        const settings = await settingsApi.get()
        setAutoRecord(settings.auto_record ?? false)
        setAutoSummarize(settings.auto_summarize ?? true)
        const days = settings.auto_delete_days
        setRetentionDays(days === 'never' ? 0 : Number(days) || 90)
      } catch {
        // Settings endpoint may not be available yet
      }
    }
    load()
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await settingsApi.update({
        auto_record: autoRecord,
        auto_summarize: autoSummarize,
        auto_delete_days: retentionDays === 0 ? 'never' : String(retentionDays),
        privacy_mode: !autoRecord,
      })
      toast.success('Privacy settings saved')
    } catch {
      toast.error('Failed to save privacy settings')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-6">

      {/* Auto Record */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium text-gray-900">Auto-Record Meetings</h3>
          <p className="text-sm text-gray-500">Automatically start recording when the device detects a meeting</p>
        </div>
        <button
          onClick={() => setAutoRecord(!autoRecord)}
          className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out ${
            autoRecord ? 'bg-primary-600' : 'bg-gray-200'
          }`}
          role="switch"
          aria-checked={autoRecord}
        >
          <span
            className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
              autoRecord ? 'translate-x-5' : 'translate-x-0'
            }`}
          />
        </button>
      </div>

      {/* Auto Summarize */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium text-gray-900">Auto-Summarize</h3>
          <p className="text-sm text-gray-500">Automatically generate a summary after each meeting ends</p>
        </div>
        <button
          onClick={() => setAutoSummarize(!autoSummarize)}
          className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out ${
            autoSummarize ? 'bg-primary-600' : 'bg-gray-200'
          }`}
          role="switch"
          aria-checked={autoSummarize}
        >
          <span
            className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
              autoSummarize ? 'translate-x-5' : 'translate-x-0'
            }`}
          />
        </button>
      </div>

      {/* Data Retention */}
      <div>
        <label htmlFor="retention" className="block text-sm font-medium text-gray-700 mb-2">
          Data Retention Period
        </label>
        <select
          id="retention"
          value={retentionDays}
          onChange={(e) => setRetentionDays(Number(e.target.value))}
          className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
        >
          <option value={30}>30 days</option>
          <option value={60}>60 days</option>
          <option value={90}>90 days</option>
          <option value={180}>180 days</option>
          <option value={365}>1 year</option>
          <option value={0}>Keep forever</option>
        </select>
        <p className="mt-1 text-sm text-gray-500">
          Meeting recordings and transcripts older than this will be automatically deleted
        </p>
      </div>

      {/* Save */}
      <div className="pt-4 border-t border-gray-200">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-6 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save Changes'}
        </button>
      </div>
    </div>
  )
}
