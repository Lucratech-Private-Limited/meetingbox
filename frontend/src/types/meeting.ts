// Meeting-related TypeScript types

export interface Meeting {
  id: string
  title: string
  start_time: string
  end_time: string | null
  duration: number | null // in seconds
  status: 'recording' | 'transcribing' | 'summarizing' | 'finalizing' | 'completed' | 'transcription_failed'
  audio_path: string | null
  created_at: string
  pending_actions?: number
}

export interface MeetingDetail extends Meeting {
  segments: TranscriptSegment[]
  summary: MeetingSummary | null
  local_summary?: LocalSummary | null
}

export interface TranscriptSegment {
  segment_num: number
  start_time: number // seconds from start
  end_time: number
  text: string
  speaker_id: string | null
  confidence?: number
}

export interface MeetingSummary {
  summary: string
  action_items: ActionItem[]
  decisions: string[]
  topics: string[]
  sentiment: string
}

export interface LocalSummary extends MeetingSummary {
  model_name: string
}

export interface ActionItem {
  task: string
  assignee: string | null
  due_date: string | null
  completed?: boolean
}
