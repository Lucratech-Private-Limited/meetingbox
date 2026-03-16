// Agentic actions API endpoints

import client from './client'
import type { AgenticAction } from '../types/action'

export interface ExecuteResult {
  id: string
  status: string
  delivery_status: string
  artifact: Record<string, unknown> | null
  result: Record<string, unknown>
}

export const actionsApi = {
  list: async (meetingId: string): Promise<AgenticAction[]> => {
    const response = await client.get(`/api/meetings/${meetingId}/actions`)
    return response.data
  },

  generate: async (meetingId: string): Promise<AgenticAction[]> => {
    const response = await client.post(`/api/meetings/${meetingId}/actions/generate`)
    return response.data
  },

  dismiss: async (actionId: string): Promise<void> => {
    await client.post(`/api/actions/${actionId}/dismiss`)
  },

  execute: async (actionId: string): Promise<ExecuteResult> => {
    const response = await client.post(`/api/actions/${actionId}/execute`)
    return response.data
  },

  update: async (
    actionId: string,
    update: { title?: string; description?: string; payload?: Record<string, unknown> }
  ): Promise<AgenticAction> => {
    const response = await client.patch(`/api/actions/${actionId}`, update)
    return response.data
  },
}
