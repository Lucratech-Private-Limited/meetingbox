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

export interface SystemInfo {
  cpu_percent: number
  memory_percent: number
  memory_used_gb: number
  memory_total_gb: number
  disk_percent: number
  disk_used_gb: number
  disk_total_gb: number
}
