// Settings API endpoints

import client from './client'

export const settingsApi = {
  // Get current device settings
  get: async () => {
    const response = await client.get('/api/device/settings')
    return response.data
  },

  // Update device settings
  update: async (settings: Record<string, unknown>) => {
    const response = await client.patch('/api/device/settings', settings)
    return response.data
  },

  // Setup device name (onboarding)
  setDeviceName: async (name: string) => {
    const response = await client.patch('/api/device/settings', { device_name: name })
    return response.data
  },
}
