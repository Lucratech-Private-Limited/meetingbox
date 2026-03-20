// Utility functions for formatting values

/** Format seconds into MM:SS or HH:MM:SS */
export function formatDuration(seconds: number): string {
  const hrs = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)

  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, '0')}:${secs
      .toString()
      .padStart(2, '0')}`
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

/** Format seconds into a human-readable duration (e.g. "30 minutes") */
export function formatMinutes(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  if (mins < 1) return 'Less than a minute'
  if (mins === 1) return '1 minute'
  return `${mins} minutes`
}

/** Format total seconds into hours for stats display */
export function formatHours(totalSeconds: number): number {
  return Math.round(totalSeconds / 3600)
}

/**
 * Parse a backend timestamp as UTC.
 * The backend stores naive ISO strings (no tz offset) that are actually UTC.
 * Without this, the browser interprets them as local time, skewing by the
 * user's UTC offset.
 */
export function parseUTC(iso: string): Date {
  if (!iso) return new Date(0)
  if (iso.endsWith('Z') || iso.includes('+') || iso.includes('-', 10)) {
    return new Date(iso)
  }
  return new Date(iso + 'Z')
}

/** Truncate text with ellipsis */
export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text
  return text.slice(0, maxLength) + '…'
}
