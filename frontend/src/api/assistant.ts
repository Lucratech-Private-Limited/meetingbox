import { apiClient } from './client'

export interface AssistantIntentResponse {
  audit_id: string
  assistant_message: string
  routed_agent_id?: string | null
  routing_method?: string
  pending_actions?: Array<{ id: string; tool_name: string; status: string }>
  tool_results?: unknown[]
}

export interface AssistantPendingItem {
  id: string
  created_at: string
  audit_id: string
  agent_id: string
  tool_name: string
  payload: Record<string, unknown>
  status: string
}

export async function postAssistantIntent(
  message: string,
  meetingId?: string | null
): Promise<AssistantIntentResponse> {
  const { data } = await apiClient.post<AssistantIntentResponse>('/api/assistant/intent', {
    message,
    meeting_id: meetingId ?? null,
  })
  return data
}

export async function listAssistantPending(): Promise<AssistantPendingItem[]> {
  const { data } = await apiClient.get<{ pending: AssistantPendingItem[] }>(
    '/api/assistant/pending-actions'
  )
  return data.pending ?? []
}

export async function approveAssistantPending(pendingId: string): Promise<unknown> {
  const { data } = await apiClient.post<unknown>(
    `/api/assistant/pending-actions/${pendingId}/approve`
  )
  return data
}

export async function rejectAssistantPending(pendingId: string): Promise<unknown> {
  const { data } = await apiClient.post<unknown>(
    `/api/assistant/pending-actions/${pendingId}/reject`
  )
  return data
}
