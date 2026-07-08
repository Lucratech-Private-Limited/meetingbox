import { useCallback, useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { devicesApi } from '../../api/devices'
import type { Device } from '../../types/user'

export default function DevicesSettings() {
  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(true)
  const [pairingCode, setPairingCode] = useState<string | null>(null)
  const [pairingExpiresAt, setPairingExpiresAt] = useState<string | null>(null)
  const [creatingCode, setCreatingCode] = useState(false)
  const [busyDeviceId, setBusyDeviceId] = useState<string | null>(null)

  const loadDevices = useCallback(async () => {
    try {
      const data = await devicesApi.list()
      setDevices(data)
    } catch {
      toast.error('Failed to load devices')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadDevices()
  }, [loadDevices])

  const handleCreateCode = async () => {
    setCreatingCode(true)
    try {
      const data = await devicesApi.createPairingCode()
      setPairingCode(data.code)
      setPairingExpiresAt(data.expires_at)
      toast.success('Pairing code created')
    } catch (err: any) {
      const msg = err?.response?.data?.detail || 'Failed to create pairing code'
      toast.error(msg)
    } finally {
      setCreatingCode(false)
    }
  }

  const handleUnpair = async (deviceId: string) => {
    setBusyDeviceId(deviceId)
    try {
      await devicesApi.unpair(deviceId)
      toast.success('Device unpaired')
      await loadDevices()
    } catch (err: any) {
      const msg = err?.response?.data?.detail || 'Failed to unpair device'
      toast.error(msg)
    } finally {
      setBusyDeviceId(null)
    }
  }

  return (
    <div className="space-y-6">
      <div className="bg-app-surface rounded-lg border border-app-border p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-app-ink">Pair a new device</h2>
            <p className="mt-1 text-sm text-app-ink-muted">
              Generate a short-lived code, then enter it on the mini PC to link that device to this account.
            </p>
          </div>
          <button
            onClick={handleCreateCode}
            disabled={creatingCode}
            className="px-4 py-2 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700 disabled:opacity-50"
          >
            {creatingCode ? 'Creating...' : 'Generate code'}
          </button>
        </div>

        {pairingCode && (
          <div className="mt-6 rounded-lg border border-primary-600/40 bg-primary-900/35 p-5">
            <p className="text-sm text-primary-200">Enter this code on the device within 15 minutes.</p>
            <p className="mt-3 text-3xl font-bold tracking-[0.35em] text-primary-100">{pairingCode}</p>
            {pairingExpiresAt && (
              <p className="mt-2 text-xs text-primary-300">
                Expires at {new Date(pairingExpiresAt).toLocaleString()}
              </p>
            )}
          </div>
        )}
      </div>

      <div className="bg-app-surface rounded-lg border border-app-border p-6">
        <h2 className="text-lg font-semibold text-app-ink">Your devices</h2>
        <p className="mt-1 text-sm text-app-ink-muted">
          Meetings recorded by these devices will be linked to this account.
        </p>

        {loading ? (
          <div className="py-8 text-center text-app-ink-subtle">Loading devices...</div>
        ) : devices.length === 0 ? (
          <div className="py-8 text-center text-app-ink-subtle">No devices paired yet.</div>
        ) : (
          <div className="mt-6 space-y-4">
            {devices.map((device) => (
              <div key={device.id} className="flex items-center justify-between rounded-lg border border-app-border p-4">
                <div>
                  <div className="text-sm font-medium text-app-ink">{device.device_name || 'MeetingBox'}</div>
                  <div className="mt-1 text-xs text-app-ink-subtle">
                    Status: {device.status}
                    {device.serial_number ? ` · Serial: ${device.serial_number}` : ''}
                    {device.last_seen_at ? ` · Last seen: ${new Date(device.last_seen_at).toLocaleString()}` : ''}
                  </div>
                </div>
                <button
                  onClick={() => handleUnpair(device.id)}
                  disabled={busyDeviceId === device.id || device.status !== 'active'}
                  className="px-4 py-2 text-sm font-medium text-red-700 bg-red-50 rounded-lg hover:bg-red-100 disabled:opacity-50"
                >
                  {busyDeviceId === device.id ? 'Unpairing...' : 'Unpair'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
