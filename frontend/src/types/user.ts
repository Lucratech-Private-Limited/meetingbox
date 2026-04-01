// User and settings types

export interface UserSettings {
  device_name: string
  timezone: string
  auto_record: boolean
  auto_summarize: boolean
  notification_enabled: boolean
}

export interface Integration {
  id: string
  name: string
  connected: boolean
  icon: string
  description: string
  email?: string | null
}

export interface Device {
  id: string
  device_name: string
  serial_number?: string | null
  status: string
  paired_at?: string | null
  unpaired_at?: string | null
  last_seen_at?: string | null
  created_at?: string | null
}

export interface SystemInfo {
  cpu_percent: number
  memory_percent: number
  memory_used_gb: number
  memory_total_gb: number
  disk_percent: number
  disk_used_gb: number
  disk_total_gb: number
}
