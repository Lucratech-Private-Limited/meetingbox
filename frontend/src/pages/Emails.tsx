// Gmail inbox — Figma Cricket-Champs node 991:561
// Header tabs: All (default) · Today · Unread + search. Two-pane list + detail.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import LoadingSpinner from '../components/ui/LoadingSpinner'
import DashboardNavShell from '../components/dashboard/DashboardNavShell'
import { useVisiblePolling } from '../hooks/useVisiblePolling'
import { integrationsApi } from '../api/integrations'
import { useAuthStore } from '../store/authStore'
import {
  messageListSignature,
  readGmailInboxCache,
  writeGmailInboxCache,
} from '../utils/gmailInboxCache'
import { blk, pg } from '../styles/pageTypeScale'

const GMAIL_RECENT_DAYS = 90

// Thin blue scrollbar (Figma: track #061642 / thumb #006bf9)
const SCROLL: React.CSSProperties = { scrollbarWidth: 'thin', scrollbarColor: '#006bf9 #061642' }

// ── Types ─────────────────────────────────────────────────────────────────────
type GmailRow = {
  id: string
  threadId?: string
  snippet: string
  from: string
  subject: string
  date: string
  is_read?: boolean
}

type GmailDetail = {
  id: string
  sender: string
  sender_email: string
  subject: string
  body: string
  time: string
  to: string
  is_read: boolean
}

type InboxTab = 'today' | 'all' | 'unread'

// ── Icons (exported from Figma node 991:561) ──────────────────────────────────
function iconUrl(f: string) {
  return `${import.meta.env.BASE_URL ?? '/'}icons/${f}`
}
const icoNotification   = iconUrl('ic-notification.svg')
const icoEmailSearch    = iconUrl('ic-email-search.svg')
const icoEmailBack      = iconUrl('ic-email-back.svg')
const icoEmailMarkUnread = iconUrl('ic-email-mark-unread.svg')
const icoEmailArchive   = iconUrl('ic-email-archive.svg')
const icoEmailDotUnread = iconUrl('ic-email-dot-unread.svg')
const icoEmailDotRead   = iconUrl('ic-email-dot-read.svg')

// ── Helpers ───────────────────────────────────────────────────────────────────

function parseSender(raw: string): string {
  const m = raw.match(/^(.*?)\s*<[^>]+>/)
  if (m) return m[1].trim().replace(/^"|"$/g, '') || raw
  return raw.replace(/<[^>]+>/g, '').replace(/"/g, '').trim() || raw
}

function senderInitials(name: string): string {
  const p = name.trim().split(/\s+/)
  return p.length >= 2 ? (p[0][0] + p[1][0]).toUpperCase() : name.slice(0, 2).toUpperCase()
}

function parseDate(raw: string): Date | null {
  if (!raw) return null
  const d = new Date(raw)
  return isNaN(d.getTime()) ? null : d
}

function isToday(raw: string): boolean {
  const d = parseDate(raw)
  return !!d && d.toDateString() === new Date().toDateString()
}

function fmtListTime(raw: string): string {
  const d = parseDate(raw)
  if (!d) return raw.slice(0, 10) || '—'
  const now = new Date()
  if (d.toDateString() === now.toDateString())
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const days = Math.floor((now.getTime() - d.getTime()) / 86_400_000)
  return days < 7
    ? d.toLocaleDateString([], { weekday: 'short', day: 'numeric' })
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function fmtToLine(to: string): string {
  const t = (to ?? '').trim()
  if (!t || t === '—') return 'me'
  const m = t.match(/^(.*?)\s*<([^>]+)>/)
  if (m) return m[1].trim().replace(/^"|"$/g, '') || m[2].split('@')[0]
  return t.includes('@') ? t.split('@')[0] : t
}

// Avatar background colours derived from first char of sender name
const AVATAR_COLORS = ['#12285e', '#0d3a2e', '#2e1a48', '#2a2e12', '#12303a']
function avatarBg(name: string) {
  return AVATAR_COLORS[(name.charCodeAt(0) ?? 0) % AVATAR_COLORS.length]
}

// ── Shared atoms ──────────────────────────────────────────────────────────────
function Ico({ src, size, alt = '' }: { src: string; size: number; alt?: string }) {
  return (
    <span className="inline-flex shrink-0 items-center justify-center" style={{ width: size, height: size, minWidth: size }}>
      <img src={src} alt={alt} className="block max-h-full max-w-full object-contain" />
    </span>
  )
}

function UnreadDot({ on }: { on: boolean }) {
  return (
    <span className="mt-[5px] shrink-0" aria-hidden>
      <Ico src={on ? icoEmailDotUnread : icoEmailDotRead} size={blk.iconDot} />
    </span>
  )
}

// ── Header tab button — Figma: 30px SemiBold, active = #006bf9 with underline bar ──
function TabBtn({ label, count, active, onClick }: { label: string; count: number; active: boolean; onClick: () => void }) {
  const tone = active ? pg.filterTabActive : pg.filterTabIdle
  const countTone = active ? 'text-[#006bf9]' : 'text-white'
  return (
    <button type="button" onClick={onClick} className={`flex items-baseline gap-2 focus:outline-none ${pg.filterTab}`}>
      <span className={tone}>{label}</span>
      <span className={countTone}>{count}</span>
    </button>
  )
}

// ── Email list row — Figma 752:2576 ───────────────────────────────────────────
// Figma sender: 32.6px → ~23px web | subject: 28.6px → ~20px | snippet: 25px → ~18px
// time: 26px → ~18px (right-aligned, blue)
function EmailRow({ row, selected, onSelect }: { row: GmailRow; selected: boolean; onSelect: () => void }) {
  const unread = row.is_read === false
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full ${blk.row} text-left transition-colors border ${
        selected
          ? 'border-[#3f8cff] bg-[#011745]/50'
          : 'border-transparent hover:bg-white/[0.03]'
      }`}
    >
      <div className={`flex ${blk.rowGap} items-start`}>
        <UnreadDot on={unread} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <span className={`flex-1 truncate ${pg.cardTitle} ${unread ? 'text-white' : 'text-white/80'}`}>
              {parseSender(row.from)}
            </span>
            <span className={`shrink-0 ${pg.cardMetaSm} font-semibold text-[#006bf9] whitespace-nowrap`}>
              {fmtListTime(row.date)}
            </span>
          </div>
          <p className={`mt-0.5 truncate ${pg.cardTitle} leading-snug ${unread ? 'text-white' : 'text-white/70'}`}>
            {row.subject || '(no subject)'}
          </p>
          {row.snippet ? (
            <p className={`mt-0.5 truncate ${pg.cardMeta} leading-snug`}>
              {row.snippet}
            </p>
          ) : null}
        </div>
      </div>
    </button>
  )
}

const UPDATED_NOTICE_MS = 4_500

/** Right-edge sync / updated indicators — does not block reading the list. */
function InboxSyncRail({ refreshing, showUpdated }: { refreshing: boolean; showUpdated: boolean }) {
  if (!refreshing && !showUpdated) return null
  return (
    <aside
      className="pointer-events-none fixed right-2 sm:right-4 top-[42%] z-40 flex flex-col items-center"
      aria-live="polite"
      aria-atomic="true"
    >
      {refreshing ? (
        <div className="flex flex-col items-center gap-2 rounded-[14px] border border-[#21284b] bg-[#000f33]/95 px-2.5 py-3 shadow-[0_8px_32px_rgba(0,0,0,0.45)] backdrop-blur-sm">
          <span className="relative flex h-10 w-1.5 overflow-hidden rounded-full bg-[#061642]">
            <span className="absolute inset-x-0 top-0 h-1/2 animate-pulse rounded-full bg-[#006bf9]" />
          </span>
          <span
            className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#b6baf2]"
            style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}
          >
            Syncing
          </span>
        </div>
      ) : showUpdated ? (
        <div className="flex flex-col items-center gap-2 rounded-[14px] border border-[#006bf9]/40 bg-[#000f33]/95 px-2.5 py-3 shadow-[0_8px_32px_rgba(0,0,0,0.45)] backdrop-blur-sm">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#006bf9]/20 text-[12px] font-bold text-[#006bf9]" aria-hidden>
            ✓
          </span>
          <span
            className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#006bf9]"
            style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}
          >
            Updated
          </span>
        </div>
      ) : null}
    </aside>
  )
}

// ── Sender avatar in detail pane ──────────────────────────────────────────────
function SenderAvatar({ name }: { name: string }) {
  return (
    <span
      className="inline-flex h-[48px] w-[48px] sm:h-[56px] sm:w-[56px] shrink-0 items-center justify-center rounded-full border border-white/10 text-[16px] sm:text-[18px] font-bold text-white"
      style={{ backgroundColor: avatarBg(name || 'U') }}
    >
      {senderInitials(name || 'U')}
    </span>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
export default function Emails() {
  const user = useAuthStore((s) => s.user)
  const userId = user?.id

  const [loading,         setLoading]         = useState(true)
  const [refreshing,      setRefreshing]      = useState(false)
  const [showUpdated,     setShowUpdated]     = useState(false)
  const [connected,       setConnected]       = useState(false)
  const [messages,        setMessages]        = useState<GmailRow[]>([])
  const [inboxMessages,   setInboxMessages]   = useState<GmailRow[]>([])
  const [error,           setError]           = useState<string | null>(null)
  const [searchQuery,     setSearchQuery]     = useState('')
  const [tab,             setTab]             = useState<InboxTab>('all')
  const [selectedId,      setSelectedId]      = useState<string | null>(null)
  const [detail,          setDetail]          = useState<GmailDetail | null>(null)
  const [detailLoading,   setDetailLoading]   = useState(false)
  const [mobileDetail,    setMobileDetail]    = useState(false)

  const seqRef            = useRef(0)
  const firstPoll         = useRef(true)
  const cacheHydratedRef  = useRef(false)
  const listSigRef        = useRef('')
  const hadCacheDisplayRef = useRef(false)

  // Hydrate from local cache as soon as we know the user (instant inbox on revisit).
  useEffect(() => {
    if (!userId || cacheHydratedRef.current) return
    cacheHydratedRef.current = true
    const cached = readGmailInboxCache(userId)
    if (!cached) return
    hadCacheDisplayRef.current = true
    const cachedRows = cached.messages as GmailRow[]
      setInboxMessages(cachedRows)
      setMessages(cachedRows)
      setConnected(cached.connected)
    listSigRef.current = messageListSignature(cached.messages)
    setLoading(false)
  }, [userId])

  useEffect(() => {
    if (!showUpdated) return
    const t = window.setTimeout(() => setShowUpdated(false), UPDATED_NOTICE_MS)
    return () => window.clearTimeout(t)
  }, [showUpdated])

  const persistInbox = useCallback(
    (nextMessages: GmailRow[], nextConnected: boolean) => {
      writeGmailInboxCache(userId, { messages: nextMessages, connected: nextConnected })
    },
    [userId],
  )

  // ── Data loading ────────────────────────────────────────────────────────────
  const fetchGmailList = useCallback(async (q: string, n: number) => {
    const res = await integrationsApi.listGmailRecent({ max_results: 40, days: GMAIL_RECENT_DAYS, q })
    if (n !== seqRef.current) return null
    return {
      rows: (res.messages ?? []) as GmailRow[],
      connected: Boolean(res.connected),
      error: res.error ?? null,
    }
  }, [])

  const reload = useCallback(async () => {
    const n = ++seqRef.current
    const useCacheUi = hadCacheDisplayRef.current || Boolean(readGmailInboxCache(userId))
    if (firstPoll.current && !useCacheUi) setLoading(true)
    else if (useCacheUi || readGmailInboxCache(userId)) setRefreshing(true)

    const prevSig = listSigRef.current

    try {
      const inboxRes = await fetchGmailList('', n)
      if (n !== seqRef.current) return

      if (inboxRes) {
        setInboxMessages(inboxRes.rows)
        setMessages(inboxRes.rows)
        persistInbox(inboxRes.rows, inboxRes.connected)
        listSigRef.current = messageListSignature(inboxRes.rows)
        setConnected(inboxRes.connected)
        setError(inboxRes.error)
      }

      if (useCacheUi && inboxRes) {
        const nextSig = messageListSignature(inboxRes.rows)
        if (firstPoll.current || (prevSig && prevSig !== nextSig)) {
          setShowUpdated(true)
        }
      }
    } catch {
      if (n !== seqRef.current) return
      setError('Could not load Gmail.')
      if (!useCacheUi) {
        setConnected(false)
        setInboxMessages([])
        setMessages([])
      }
    } finally {
      if (n === seqRef.current) {
        firstPoll.current = false
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [userId, persistInbox, fetchGmailList])

  useVisiblePolling(reload)

  useEffect(() => {
    setMessages(inboxMessages)
  }, [inboxMessages])

  const counts = useMemo(() => ({
    today:  inboxMessages.filter((m) => isToday(m.date)).length,
    all:    inboxMessages.length,
    unread: inboxMessages.filter((m) => m.is_read === false).length,
  }), [inboxMessages])

  const tabFiltered = useMemo(() => {
    if (tab === 'today')  return messages.filter((m) => isToday(m.date))
    if (tab === 'unread') return messages.filter((m) => m.is_read === false)
    return messages
  }, [messages, tab])

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return tabFiltered
    return tabFiltered.filter((m) =>
      `${m.subject} ${m.from} ${m.snippet}`.toLowerCase().includes(q),
    )
  }, [tabFiltered, searchQuery])

  const { newRows, earlierRows } = useMemo(() => {
    const n: GmailRow[] = [], e: GmailRow[] = []
    filtered.forEach((m) => (isToday(m.date) ? n : e).push(m))
    return { newRows: n, earlierRows: e }
  }, [filtered])

  // Clear selection only when the selected message leaves the filtered list (no auto-pick first).
  useEffect(() => {
    if (!selectedId) return
    if (!filtered.some((m) => m.id === selectedId)) {
      setSelectedId(null)
      setDetail(null)
      setMobileDetail(false)
    }
  }, [filtered, selectedId])

  // ── Fetch detail when selection changes ─────────────────────────────────────
  useEffect(() => {
    if (!selectedId || !connected) { setDetail(null); return }
    let cancelled = false
    setDetailLoading(true)
    integrationsApi.getGmailMessage(selectedId)
      .then((d) => {
        if (cancelled) return
        setDetail({ id: d.id, sender: d.sender, sender_email: d.sender_email, subject: d.subject, body: d.body, time: d.time, to: d.to, is_read: d.is_read })
      })
      .catch(() => { if (!cancelled) setDetail(null) })
      .finally(() => { if (!cancelled) setDetailLoading(false) })
    return () => { cancelled = true }
  }, [selectedId, connected])

  // ── Actions ─────────────────────────────────────────────────────────────────
  const selectMessage = (id: string) => { setSelectedId(id); setMobileDetail(true) }

  const handleMarkUnread = async () => {
    if (!selectedId) return
    try {
      await integrationsApi.markGmailUnread(selectedId)
      const markUnread = (rows: GmailRow[]) =>
        rows.map((m) => m.id === selectedId ? { ...m, is_read: false } : m)
      setInboxMessages((prev) => {
        const next = markUnread(prev)
        listSigRef.current = messageListSignature(next)
        persistInbox(next, connected)
        return next
      })
      setMessages((prev) => markUnread(prev))
      setDetail((d) => d ? { ...d, is_read: false } : d)
    } catch { /* silent */ }
  }

  const handleArchive = async () => {
    if (!selectedId) return
    try {
      await integrationsApi.archiveGmailMessage(selectedId)
      const drop = (rows: GmailRow[]) => rows.filter((m) => m.id !== selectedId)
      const nextInbox = drop(inboxMessages)
      listSigRef.current = messageListSignature(nextInbox)
      persistInbox(nextInbox, connected)
      setInboxMessages(nextInbox)
      setMessages(drop(messages))
      setSelectedId(null)
      setDetail(null)
      setMobileDetail(false)
    } catch { /* silent */ }
  }

  // ── List section renderer ────────────────────────────────────────────────────
  const renderSection = (label: string, rows: GmailRow[]) => {
    if (rows.length === 0) return null
    return (
      <div>
        {/* Figma: 25.6px SemiBold #006bf9, all-caps */}
        <p className={`px-4 pt-3 pb-0.5 ${pg.section}`}>
          {label}
        </p>
        <div className={`flex flex-col ${blk.listSectionGap} px-3 pb-3`}>
          {rows.map((m) => (
            <EmailRow key={m.id} row={m} selected={m.id === selectedId} onSelect={() => selectMessage(m.id)} />
          ))}
        </div>
      </div>
    )
  }

  const name = user?.display_name ?? user?.username ?? ''

  // ── Render ───────────────────────────────────────────────────────────────────
  return (
    <DashboardNavShell>
      <InboxSyncRail refreshing={refreshing} showUpdated={showUpdated} />
      <div className={`min-h-screen text-white ${blk.pagePad}`}>

        <div className={blk.chromeRow}>
          <button type="button" aria-label="Notifications" className={blk.avatarBtn}>
            <Ico src={icoNotification} size={blk.notifIcon} />
          </button>
          <div className={`${blk.avatar} shrink-0 overflow-hidden rounded-full border border-white/10 bg-white/10`}>
            {user?.avatar_url
              ? <img src={user.avatar_url} alt="" className="h-full w-full object-cover" />
              : <div className="flex h-full w-full items-center justify-center text-[10px] font-bold text-white/80">{(name || 'U').slice(0, 2).toUpperCase()}</div>
            }
          </div>
        </div>

        {/* ══ Header card — Figma 751:2347 ══════════════════════════════════════
            h=136px, rounded=22.6px, gradient from #02123c to #000a26
            Title "Emails": 35px → ~25px web
            Tabs: 30px → ~21px web
            Search: 300px wide, rounded 15px, border #21284b
        ════════════════════════════════════════════════════════════════════════ */}
        <div className={blk.headerCard}>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">

            <div>
              <h1 className={pg.title}>Emails</h1>
              <div className="mt-3 flex flex-wrap items-end gap-x-6 gap-y-2">
                <TabBtn label="All"    count={counts.all}    active={tab === 'all'}    onClick={() => setTab('all')}    />
                <TabBtn label="Today"  count={counts.today}  active={tab === 'today'}  onClick={() => setTab('today')}  />
                <TabBtn label="Unread" count={counts.unread} active={tab === 'unread'} onClick={() => setTab('unread')} />
              </div>
            </div>

            {/* Right: search bar */}
            <label className={`relative block w-full shrink-0 ${blk.searchWrap}`}>
              <span className="sr-only">Search emails</span>
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2">
                <Ico src={icoEmailSearch} size={16} />
              </span>
              <input
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search emails"
                className={`w-full ${blk.searchInput} ${pg.search} text-white placeholder:text-[#b6baf2] focus:border-[#3f8cff]/60 focus:outline-none transition-colors`}
              />
            </label>

          </div>
        </div>

        {error && <p className="mb-3 text-sm text-amber-400/85">{error}</p>}

        {/* ══ Content area ══════════════════════════════════════════════════════ */}
        {loading ? (
          <div className="flex min-h-[50vh] items-center justify-center">
            <LoadingSpinner size="large" />
          </div>

        ) : !connected ? (
          <div className="mt-6 rounded-[22px] border border-[#3f4253] bg-gradient-to-b from-[#000f33] to-[#000a26] px-6 py-14 text-center">
            <p className="text-[16px] sm:text-[18px] font-medium text-white/70">
              Connect Gmail in Settings → Integrations to see your inbox here.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-4">
              <Link
                to="/settings"
                className="rounded-2xl border border-[#3f8cff]/40 bg-[#006bf9]/[0.14] px-6 py-2.5 text-[14px] font-semibold text-white hover:bg-[#006bf9]/25 transition"
              >
                Open Settings
              </Link>
            </div>
          </div>

        ) : (
          /* ══ Two-column inbox ══════════════════════════════════════════════════
              Figma: list col 684px (41.5%), detail col 853px (51.8%) + gap
          ══════════════════════════════════════════════════════════════════════ */
          <div className={`grid grid-cols-1 lg:grid-cols-[minmax(0,44.5%)_minmax(0,55.5%)] ${blk.gridGap}`}>

            {/* ── List pane — Figma 752:2572 ──────────────────────────────────
                h=762px, rounded=37.9px, gradient from #000f33 to #000a26
                Scrollbar: track #061642, thumb #006bf9
            ────────────────────────────────────────────────────────────────── */}
            <div
              className={`${blk.paneMinH} ${blk.pane} overflow-hidden flex flex-col ${
                mobileDetail ? 'hidden lg:flex' : 'flex'
              }`}
            >
              <div className={`flex-1 overflow-y-auto ${blk.listInner}`} style={SCROLL}>
                {filtered.length === 0 ? (
                  <p className={`py-20 text-center ${pg.empty}`}>
                    {messages.length === 0 ? 'No recent messages returned.' : 'No messages match your filters.'}
                  </p>
                ) : (
                  <>
                    {renderSection('NEW', newRows)}
                    {renderSection('EARLIER', earlierRows)}
                  </>
                )}
              </div>
            </div>

            {/* ── Detail pane — Figma 752:2623 ────────────────────────────────
                h=762px, rounded=37.9px, same background
            ────────────────────────────────────────────────────────────────── */}
            <div
              className={`${blk.paneMinHDetail} ${blk.pane} overflow-hidden flex flex-col ${
                selectedId ? (mobileDetail ? 'flex' : 'hidden lg:flex') : 'hidden lg:flex'
              }`}
            >
              {!selectedId ? (
                <div className={`flex flex-1 items-center justify-center ${pg.empty}`}>
                  Select an email to read
                </div>
              ) : (
                <>
                  {/* Toolbar — Figma: Back · Mark unread · Archive · More (23px Medium) */}
                  <div className="flex flex-wrap items-center gap-5 sm:gap-8 border-b border-[#1a2244] px-5 sm:px-6 py-3 shrink-0">
                    <button
                      type="button"
                      onClick={() => setMobileDetail(false)}
                      className={`inline-flex items-center gap-2 ${pg.toolbar} text-white hover:text-[#b6baf2] transition lg:hidden`}
                    >
                      <Ico src={icoEmailBack} size={24} />
                      Back
                    </button>

                    <button
                      type="button"
                      onClick={handleMarkUnread}
                      disabled={detailLoading}
                      className={`inline-flex items-center gap-2 ${pg.toolbar} text-white hover:text-[#b6baf2] transition disabled:opacity-40`}
                    >
                      <Ico src={icoEmailMarkUnread} size={20} />
                      Mark unread
                    </button>

                    <button
                      type="button"
                      onClick={handleArchive}
                      disabled={detailLoading}
                      className={`inline-flex items-center gap-2 ${pg.toolbar} text-white hover:text-[#b6baf2] transition disabled:opacity-40`}
                    >
                      <Ico src={icoEmailArchive} size={20} />
                      Archive
                    </button>

                    <button
                      type="button"
                      disabled
                      className={`ml-auto inline-flex items-center gap-2 rounded-[14px] border border-[#21284b] bg-gradient-to-b from-[#011137] to-[#000a26] px-4 py-2 ${pg.toolbar} text-[#006bf9] disabled:opacity-60`}
                    >
                      <span className="text-[16px] leading-none tracking-widest" aria-hidden>····</span>
                      More
                    </button>
                  </div>

                  {/* Email body */}
                  <div className="flex-1 overflow-y-auto px-5 sm:px-7 py-5" style={SCROLL}>
                    {detailLoading && !detail ? (
                      <div className="flex justify-center py-20">
                        <LoadingSpinner size="medium" />
                      </div>
                    ) : detail ? (
                      <>
                        {/* Sender row — Figma: avatar + "Neha Sharma" 29.4px + "To: Vivek" 25.6px */}
                        <div className="flex items-start gap-4 pb-5 mb-5 border-b border-[#1a2244]">
                          <div className="flex shrink-0 items-start gap-2 pt-2">
                            {!detail.is_read ? <UnreadDot on /> : null}
                            <SenderAvatar name={detail.sender} />
                          </div>
                          <div className="min-w-0 flex-1 pt-1">
                            <p className={`${pg.cardTitleMd} truncate`}>
                              {detail.sender}
                            </p>
                            <p className={`mt-1 ${pg.cardMeta} font-semibold leading-none`}>
                              <span className="text-[#b6baf2]">To: </span>
                              <span className="text-[#006bf9]">{fmtToLine(detail.to)}</span>
                            </p>
                          </div>
                          <span className={`shrink-0 ${pg.cardMetaSm} text-[#9ba2b2] whitespace-nowrap pt-2`}>
                            {detail.time}
                          </span>
                        </div>

                        <h2 className={`${pg.cardTitleMd} leading-snug mb-4`}>
                          {detail.subject}
                        </h2>

                        <div className={`${pg.body} whitespace-pre-wrap`}>
                          {detail.body || '(No content)'}
                        </div>
                      </>
                    ) : (
                      <p className="text-center text-[14px] text-[#9ba2b2] py-20">
                        Could not load this message.
                      </p>
                    )}
                  </div>
                </>
              )}
            </div>

          </div>
        )}

      </div>
    </DashboardNavShell>
  )
}
