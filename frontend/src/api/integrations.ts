import client from './client'
import type { Integration } from '../types/user'

export const integrationsApi = {
  list: async (): Promise<Integration[]> => {
    const response = await client.get('/api/integrations')
    return response.data
  },

  getAuthUrl: async (provider: string): Promise<string> => {
    const response = await client.get(`/api/integrations/${provider}/auth-url`)
    return response.data.auth_url
  },

  disconnect: async (provider: string): Promise<void> => {
    await client.post(`/api/integrations/${provider}/disconnect`)
  },
}
