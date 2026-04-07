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

  ignore: async (actionId: string): Promise<void> => {
    await client.post(`/api/actions/${actionId}/ignore`)
  },

  execute: async (
    actionId: string,
    payloadOverride?: Record<string, unknown> | null,
    options?: { createDraft?: boolean; repeatExecution?: boolean },
  ): Promise<ExecuteResult> => {
    const body: Record<string, unknown> = {}
    if (payloadOverride && Object.keys(payloadOverride).length > 0) {
      body.payload = payloadOverride
    }
    if (options?.createDraft) {
      body.create_draft = true
    }
    if (options?.repeatExecution) {
      body.repeat_execution = true
    }
    const response = await client.post(`/api/actions/${actionId}/execute`, body)
    return response.data
  },

  update: async (
    actionId: string,
    update: { title?: string; description?: string; payload?: Record<string, unknown> }
  ): Promise<AgenticAction> => {
    const response = await client.patch(`/api/actions/${actionId}`, update)
    return response.data
  },

  /** User-created pending action (no AI); requires connected Gmail or Calendar. */
  createManual: async (
    meetingId: string,
    body:
      | {
          connector: 'calendar'
          title: string
          description?: string
          event_title?: string
          suggested_date: string
          suggested_time: string
          duration_minutes?: number
          timezone?: string
          attendees?: string[]
        }
      | {
          connector: 'gmail'
          title: string
          description?: string
          to: string[]
          cc?: string[]
          subject: string
          email_body: string
        },
  ): Promise<AgenticAction> => {
    const response = await client.post(`/api/meetings/${meetingId}/actions/manual`, body)
    return response.data
  },
}
