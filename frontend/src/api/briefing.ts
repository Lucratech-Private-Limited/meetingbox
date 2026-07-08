import { apiClient } from './client'

export interface BriefingContext {
  greeting?: string
  user_display_name?: string | null
  timezone?: string
  today?: string
  calendar_connected?: boolean
  days?: Record<string, { meetings: unknown[] }>
  commitments?: unknown[]
  meetings_recent?: unknown[]
  mem0_snippet?: string | null
  pending_assistant?: { count_pending: number; items: unknown[]; count?: number }
  gmail_preview?: { connected?: boolean; top?: unknown }
}

export async function getBriefingContext(daysAhead: number = 1): Promise<BriefingContext> {
  const { data } = await apiClient.get<BriefingContext>('/api/briefing/context', {
    params: { days_ahead: daysAhead },
  })
  return data
}
