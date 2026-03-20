// Application-wide constants

export const APP_NAME = 'MeetingBox'

export const ROUTES = {
  ONBOARDING: '/onboarding',
  DASHBOARD: '/dashboard',
  MEETING_DETAIL: '/meeting/:id',
  LIVE: '/live',
  SETTINGS: '/settings',
  SYSTEM: '/system',
} as const

export const TIMEZONES = [
  { value: 'America/New_York', label: 'Eastern Time (ET)' },
  { value: 'America/Chicago', label: 'Central Time (CT)' },
  { value: 'America/Denver', label: 'Mountain Time (MT)' },
  { value: 'America/Los_Angeles', label: 'Pacific Time (PT)' },
  { value: 'America/Phoenix', label: 'Arizona' },
  { value: 'America/Anchorage', label: 'Alaska' },
  { value: 'Pacific/Honolulu', label: 'Hawaii' },
  { value: 'Europe/London', label: 'GMT / London' },
  { value: 'Europe/Berlin', label: 'Central European (CET)' },
  { value: 'Asia/Kolkata', label: 'India (IST)' },
  { value: 'Asia/Tokyo', label: 'Japan (JST)' },
  { value: 'Australia/Sydney', label: 'Australia Eastern (AEST)' },
] as const

export const DATE_FILTERS = ['all', 'today', 'week', 'month'] as const
export type DateFilter = (typeof DATE_FILTERS)[number]

export const MEETING_STATUSES = {
  recording: { color: 'bg-red-100 text-red-800', text: 'Recording' },
  transcribing: { color: 'bg-yellow-100 text-yellow-800', text: 'Processing' },
  completed: { color: 'bg-green-100 text-green-800', text: 'Completed' },
} as const
