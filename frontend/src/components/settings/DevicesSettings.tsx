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
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Pair a new device</h2>
            <p className="mt-1 text-sm text-gray-600">
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
          <div className="mt-6 rounded-lg border border-primary-200 bg-primary-50 p-5">
            <p className="text-sm text-primary-700">Enter this code on the device within 15 minutes.</p>
            <p className="mt-3 text-3xl font-bold tracking-[0.35em] text-primary-900">{pairingCode}</p>
            {pairingExpiresAt && (
              <p className="mt-2 text-xs text-primary-700">
                Expires at {new Date(pairingExpiresAt).toLocaleString()}
              </p>
            )}
          </div>
        )}
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900">Your devices</h2>
        <p className="mt-1 text-sm text-gray-600">
          Meetings recorded by these devices will be linked to this account.
        </p>

        {loading ? (
          <div className="py-8 text-center text-gray-500">Loading devices...</div>
        ) : devices.length === 0 ? (
          <div className="py-8 text-center text-gray-500">No devices paired yet.</div>
        ) : (
          <div className="mt-6 space-y-4">
            {devices.map((device) => (
              <div key={device.id} className="flex items-center justify-between rounded-lg border border-gray-200 p-4">
                <div>
                  <div className="text-sm font-medium text-gray-900">{device.device_name || 'MeetingBox'}</div>
                  <div className="mt-1 text-xs text-gray-500">
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
