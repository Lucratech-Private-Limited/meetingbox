import { apiClient } from './client'

export interface AssistantIntentResponse {
  audit_id: string
  assistant_message: string
  routed_agent_id?: string | null
  routing_method?: string
  pending_actions?: Array<{ id: string; tool_name: string; status: string }>
  tool_results?: unknown[]
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
