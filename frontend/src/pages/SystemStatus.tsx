import { useState, useEffect, useCallback, useRef } from 'react'
import { apiClient } from '../api/client'
import { meetingsApi } from '../api/meetings'
import type { ApplianceStatusPayload, SystemInfo } from '../types/user'
import LoadingSpinner from '../components/ui/LoadingSpinner'
import Modal from '../components/ui/Modal'
import Button from '../components/ui/Button'
import toast from 'react-hot-toast'

export default function SystemStatus() {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [applianceNote, setApplianceNote] = useState<string | null>(null)
  const [pairedDeviceName, setPairedDeviceName] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [showCleanup, setShowCleanup] = useState(false)
  const [cleanupCount, setCleanupCount] = useState(5)
  const [isCleaning, setIsCleaning] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await apiClient.get<ApplianceStatusPayload>('/api/system/appliance-status')
      setInfo(res.data.system)
      setApplianceNote(res.data.message)
      setPairedDeviceName(res.data.device?.device_name ?? null)
      setError(null)
    } catch {
      setError('Could not reach the backend. Make sure it is running.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    intervalRef.current = setInterval(load, 5000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [load])

  const handleCleanup = async () => {
    try {
      setIsCleaning(true)
      const result = await meetingsApi.cleanupOldest(cleanupCount)
      if (result.deleted === 0) {
        toast.success('No meetings to delete for your account.')
      } else {
        toast.success(`Deleted ${result.deleted} oldest meeting${result.deleted !== 1 ? 's' : ''}`)
      }
      setShowCleanup(false)
      load()
    } catch {
      toast.error('Failed to clean up meetings')
    } finally {
      setIsCleaning(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <LoadingSpinner size="large" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-4">System Status</h1>
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-800">
          <p>{error}</p>
          <button
            onClick={() => { setLoading(true); load() }}
            className="mt-3 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 text-sm"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  const stats = info
    ? [
    {
      label: 'CPU',
      value: `${info.cpu_percent.toFixed(1)}%`,
      percent: info.cpu_percent,
      color: info.cpu_percent > 80 ? 'bg-red-500' : info.cpu_percent > 50 ? 'bg-yellow-500' : 'bg-green-500',
    },
    {
      label: 'Memory',
      value: `${info.memory_percent.toFixed(1)}% (${info.memory_used_gb.toFixed(1)} / ${info.memory_total_gb.toFixed(1)} GB)`,
      percent: info.memory_percent,
      color: info.memory_percent > 80 ? 'bg-red-500' : info.memory_percent > 50 ? 'bg-yellow-500' : 'bg-green-500',
    },
    {
      label: 'Disk',
      value: `${info.disk_percent.toFixed(1)}% (${info.disk_used_gb.toFixed(1)} / ${info.disk_total_gb.toFixed(1)} GB)`,
      percent: info.disk_percent,
      color: info.disk_percent > 80 ? 'bg-red-500' : info.disk_percent > 50 ? 'bg-yellow-500' : 'bg-green-500',
    },
      ]
    : []

  return (
    <div className="max-w-4xl mx-auto px-4 py-8" data-tutorial="tutorial-system">
      <h1 className="text-3xl font-bold text-gray-900 mb-2">System Status</h1>
      <p className="text-sm text-gray-600 mb-4">
        Metrics reflect your <strong>paired MeetingBox device</strong> (mini PC), not this web server.
        {pairedDeviceName ? (
          <span className="block mt-1 text-gray-800">Device: {pairedDeviceName}</span>
        ) : null}
      </p>

      {applianceNote && (
        <div className="mb-6 bg-amber-50 border border-amber-200 rounded-lg p-4 text-amber-900 text-sm">
          {applianceNote}
        </div>
      )}

      {stats.length > 0 ? (
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {stats.map((stat) => (
          <div key={stat.label} className="bg-white rounded-lg border border-gray-200 p-6">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide">
                {stat.label}
              </h3>
            </div>
            <p className="text-lg font-semibold text-gray-900 mb-3">{stat.value}</p>
            <div className="w-full bg-gray-200 rounded-full h-2">
              <div
                className={`h-2 rounded-full transition-all ${stat.color}`}
                style={{ width: `${Math.min(stat.percent, 100)}%` }}
              />
            </div>
          </div>
        ))}
      </div>
      ) : !applianceNote ? (
        <p className="text-gray-600 text-sm mb-6">No metrics to display yet.</p>
      ) : null}

      {info?.updated_at ? (
        <p className="mt-3 text-xs text-gray-500">
          Last updated from device: {new Date(info.updated_at).toLocaleString()}
        </p>
      ) : null}

      {/* Free up Space */}
      <div className="mt-8">
        <h2 className="text-xl font-bold text-gray-900 mb-4">Storage</h2>
        {info && info.disk_percent > 80 && (
          <div className="mb-4 bg-red-50 border border-red-200 rounded-lg p-4 flex items-start">
            <svg className="h-5 w-5 text-red-400 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            <div className="ml-3">
              <h3 className="text-sm font-medium text-red-800">Disk usage is above 80%</h3>
              <p className="mt-1 text-sm text-red-700">
                Free up space by deleting old meetings to keep the system running smoothly.
              </p>
            </div>
          </div>
        )}
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <p className="text-sm text-gray-600 mb-4">
            Delete old meeting recordings, transcripts, and summaries to reclaim disk space on the server
            (your account). This does not change the appliance display above.
          </p>
          <div className="flex flex-wrap gap-3">
            <button
              onClick={() => setShowCleanup(true)}
              className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700"
            >
              Free up Space
            </button>
          </div>
        </div>
      </div>

      {/* Cleanup modal */}
      <Modal isOpen={showCleanup} onClose={() => setShowCleanup(false)} title="Free up Space">
        <p className="text-sm text-gray-600 mb-4">
          Choose how to free up disk space. Deleted meetings cannot be recovered.
        </p>
        <div className="space-y-4">
          <div className="bg-gray-50 rounded-lg p-4">
            <h4 className="text-sm font-medium text-gray-900 mb-2">Delete oldest meetings first</h4>
            <p className="text-sm text-gray-500 mb-3">
              Remove the oldest recordings to make room for new ones.
            </p>
            <div className="flex items-center gap-3">
              <label htmlFor="cleanup-count" className="text-sm text-gray-700">
                Number to delete:
              </label>
              <select
                id="cleanup-count"
                value={cleanupCount}
                onChange={(e) => setCleanupCount(Number(e.target.value))}
                className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500"
              >
                {[5, 10, 20, 50].map((n) => (
                  <option key={n} value={n}>{n} meetings</option>
                ))}
              </select>
              <Button
                variant="danger"
                onClick={handleCleanup}
                isLoading={isCleaning}
              >
                Delete
              </Button>
            </div>
          </div>
          <div className="bg-gray-50 rounded-lg p-4">
            <h4 className="text-sm font-medium text-gray-900 mb-2">Delete manually</h4>
            <p className="text-sm text-gray-500">
              Go to the Dashboard and delete individual meetings by hovering over them and clicking the trash icon.
            </p>
          </div>
        </div>
        <div className="mt-6 flex justify-end">
          <Button variant="secondary" onClick={() => setShowCleanup(false)}>
            Close
          </Button>
        </div>
      </Modal>
    </div>
  )
}
