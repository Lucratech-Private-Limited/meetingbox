import client from './client'
import type { Integration } from '../types/user'

export type GmailListParams = {
  max_results?: number
  days?: number
  /** Gmail search query, e.g. is:unread, in:sent, in:drafts */
  q?: string
}

export type GmailListResponse = {
  connected: boolean
  messages: Array<{
    id: string
    threadId?: string
    snippet: string
    from: string
    subject: string
    date: string
    is_read?: boolean
  }>
  count?: number
  error?: string | null
}

export type GmailMessageDetail = {
  id: string
  sender: string
  sender_email: string
  subject: string
  body: string
  time: string
  to: string
  is_read: boolean
}

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

  listGmailRecent: async (params: GmailListParams = {}): Promise<GmailListResponse> => {
    const response = await client.get('/api/integrations/gmail/recent', { params })
    return response.data
  },

  getGmailMessage: async (messageId: string): Promise<GmailMessageDetail> => {
    const response = await client.get(`/api/integrations/gmail/messages/${messageId}`)
    return response.data
  },

  markGmailUnread: async (messageId: string): Promise<void> => {
    await client.post(`/api/integrations/gmail/messages/${messageId}/mark-unread`)
  },

  archiveGmailMessage: async (messageId: string): Promise<void> => {
    await client.post(`/api/integrations/gmail/messages/${messageId}/archive`)
  },

  listCalendarEvents: async (params: {
    days_past?: number
    days_future?: number
    max_results?: number
  } = {}): Promise<{ connected: boolean; events: Array<Record<string, unknown>>; count?: number }> => {
    const response = await client.get('/api/integrations/calendar/events', { params })
    return response.data
  },
}
