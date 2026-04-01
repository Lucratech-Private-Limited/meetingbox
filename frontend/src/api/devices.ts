import client from './client'
import type { Device } from '../types/user'

export const devicesApi = {
  list: async (): Promise<Device[]> => {
    const response = await client.get('/api/devices')
    return response.data
  },

  createPairingCode: async (): Promise<{ code: string; expires_at: string }> => {
    const response = await client.post('/api/devices/pairing-codes')
    return response.data
  },

  unpair: async (deviceId: string): Promise<void> => {
    await client.post(`/api/devices/${deviceId}/unpair`)
  },
}
