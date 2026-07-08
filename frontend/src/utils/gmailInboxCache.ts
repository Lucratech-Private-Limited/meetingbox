/** Persisted Gmail inbox list for instant display on return visits. */

export type CachedGmailRow = {
  id: string
  threadId?: string
  snippet: string
  from: string
  subject: string
  date: string
  is_read?: boolean
}

type InboxCachePayload = {
  messages: CachedGmailRow[]
  connected: boolean
  savedAt: number
}

const CACHE_VERSION = 'v1'
const CACHE_PREFIX = `meetingbox_gmail_inbox_${CACHE_VERSION}_`

function cacheKey(userId: string): string {
  return `${CACHE_PREFIX}${userId}`
}

export function readGmailInboxCache(userId: string | undefined): InboxCachePayload | null {
  if (!userId) return null
  try {
    const raw = localStorage.getItem(cacheKey(userId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as InboxCachePayload
    if (!parsed || !Array.isArray(parsed.messages) || typeof parsed.connected !== 'boolean') {
      return null
    }
    return parsed
  } catch {
    return null
  }
}

export function writeGmailInboxCache(
  userId: string | undefined,
  data: { messages: CachedGmailRow[]; connected: boolean },
): void {
  if (!userId) return
  try {
    const payload: InboxCachePayload = {
      messages: data.messages,
      connected: data.connected,
      savedAt: Date.now(),
    }
    localStorage.setItem(cacheKey(userId), JSON.stringify(payload))
  } catch {
    /* quota / private mode — ignore */
  }
}

export function messageListSignature(messages: CachedGmailRow[]): string {
  return messages.map((m) => `${m.id}:${m.is_read === false ? '0' : '1'}`).join('|')
}
