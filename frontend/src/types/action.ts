export type ActionKind =
  | 'cost_analysis'
  | 'decision_brief'
  | 'risk_register'
  | 'task_digest'
  | 'followup_email'
  | 'schedule_followup'

export type ConnectorTarget = 'internal' | 'gmail' | 'calendar' | 'slack' | 'notion'
export type ExecutionMode = 'artifact_create' | 'message_send' | 'event_create'
export type ActionStatus = 'pending' | 'executed' | 'dismissed'

export interface ActionArtifactSection {
  title: string
  bullets: string[]
}

export interface ActionArtifact {
  artifact_type?: string
  headline?: string
  summary?: string
  sections?: ActionArtifactSection[]
  [key: string]: unknown
}

export interface AgenticAction {
  id: string
  meeting_id: string
  type: string
  kind: ActionKind
  connector_target: ConnectorTarget
  execution_mode: ExecutionMode
  title: string
  description: string | null
  assignee: string | null
  confidence: number | null
  payload: Record<string, unknown>
  artifact: ActionArtifact | null
  status: ActionStatus
  delivery_status: string | null
  error: string | null
  selected_at: string | null
  executed_at: string | null
  created_at: string | null
}
