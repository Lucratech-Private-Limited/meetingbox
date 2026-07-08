// Meeting summaries — Figma Cricket-Champs node 991:216
// Date filter bar + expandable summary cards with key points preview.

import { useCallback, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import LoadingSpinner from '../components/ui/LoadingSpinner'
import DashboardNavShell from '../components/dashboard/DashboardNavShell'
import { useVisiblePolling } from '../hooks/useVisiblePolling'
import { useAuthStore } from '../store/authStore'
import { meetingsApi } from '../api/meetings'
import type { Meeting } from '../types/meeting'
import { parseUTC } from '../utils/formatters'
import { blk, pg } from '../styles/pageTypeScale'
import toast from 'react-hot-toast'

const SCROLL: React.CSSProperties = { scrollbarWidth: 'thin', scrollbarColor: '#006bf9 #061642' }

type MeetingTab = 'all' | 'today' | 'yesterday' | 'week' | 'month'

const FILTER_TABS: { id: MeetingTab; label: string }[] = [
  { id: 'all', label: 'All Meetings' },
  { id: 'today', label: 'Today' },
  { id: 'yesterday', label: 'Yesterday' },
  { id: 'week', label: 'This Week' },
  { id: 'month', label: 'This Month' },
]

function iconUrl(f: string) {
  return `${import.meta.env.BASE_URL ?? '/'}icons/${f}`
}

const icoNotification = iconUrl('ic-notification.svg')
const icoMeetingFile   = iconUrl('ic-meeting-file.svg')
const icoMeetingTime   = iconUrl('ic-meeting-time.svg')
const icoMeetingChevron = iconUrl('ic-meeting-chevron.svg')

function parseMeetingDate(m: Meeting): Date | null {
  const d = parseUTC(m.start_time)
  return isNaN(d.getTime()) ? null : d
}

function startOfDay(d: Date): Date {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  return x
}

function isToday(d: Date): boolean {
  return d.toDateString() === new Date().toDateString()
}

function isYesterday(d: Date): boolean {
  const y = new Date()
  y.setDate(y.getDate() - 1)
  return d.toDateString() === y.toDateString()
}

function isThisWeek(d: Date): boolean {
  const now = new Date()
  const weekAgo = new Date(now)
  weekAgo.setDate(now.getDate() - 6)
  return d >= startOfDay(weekAgo) && d <= now
}

function isThisMonth(d: Date): boolean {
  const now = new Date()
  return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear()
}

function matchesDateTab(m: Meeting, tab: MeetingTab): boolean {
  if (tab === 'all') return true
  const d = parseMeetingDate(m)
  if (!d) return false
  if (tab === 'today') return isToday(d)
  if (tab === 'yesterday') return isYesterday(d)
  if (tab === 'week') return isThisWeek(d)
  return isThisMonth(d)
}

function formatDuration(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return ''
  const mins = Math.round(seconds / 60)
  if (mins < 60) return `${mins} min`
  const hours = Math.floor(mins / 60)
  const rest = mins % 60
  return rest ? `${hours}h ${rest}m` : `${hours}h`
}

function fmtMeetingDateTime(m: Meeting): string {
  const d = parseMeetingDate(m)
  if (!d) return '—'
  return d.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function fmtEndTime(m: Meeting): string {
  const end = m.end_time ? parseUTC(m.end_time) : null
  if (end && !isNaN(end.getTime()))
    return end.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const start = parseMeetingDate(m)
  if (!start || !m.duration) return '—'
  const endCalc = new Date(start.getTime() + m.duration * 1000)
  return endCalc.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

function keyPointBullets(m: Meeting): string[] {
  if (m.status === 'recording') {
    return ['Recording in progress']
  }
  if (m.status === 'transcribing' || m.status === 'summarizing' || m.status === 'finalizing') {
    return ['Summary is being generated']
  }
  if (m.status === 'transcription_failed') {
    return ['Transcription failed — open for details']
  }
  if (m.status === 'completed') {
    return ['Open meeting for key points and action items']
  }
  return ['Meeting details available on open']
}

function Ico({ src, size, alt = '' }: { src: string; size: number; alt?: string }) {
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center"
      style={{ width: size, height: size, minWidth: size }}
    >
      <img src={src} alt={alt} className="block max-h-full max-w-full object-contain" draggable={false} />
    </span>
  )
}

function FilterTab({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`shrink-0 ${blk.filterTabPad} ${pg.filterTab} leading-none transition ${
        active
          ? 'bg-gradient-to-t from-[#011037] to-[#001857] text-[#006bf9]'
          : 'text-[#b6baf2] hover:bg-white/[0.03]'
      }`}
    >
      {label}
    </button>
  )
}

function MeetingSummaryCard({
  meeting,
  generating,
  onGenerateSummary,
}: {
  meeting: Meeting
  generating: boolean
  onGenerateSummary: (meetingId: string) => void
}) {
  const duration = formatDuration(meeting.duration)
  const bullets = keyPointBullets(meeting)
  const canGenerateSummary =
    (meeting.transcript_segments ?? 0) > 0 &&
    meeting.status !== 'recording' &&
    meeting.status !== 'transcribing' &&
    meeting.status !== 'summarizing' &&
    meeting.status !== 'finalizing'

  const handleGenerateSummary = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault()
    event.stopPropagation()
    if (!generating) onGenerateSummary(meeting.id)
  }

  return (
    <Link
      to={`/meeting/${meeting.id}`}
      className={`block ${blk.pane} px-4 py-3 transition hover:border-[#3f8cff]/40`}
    >
      <div className={`flex items-start ${blk.rowGap}`}>
        <span className={blk.meetingIconBox}>
          <Ico src={icoMeetingFile} size={blk.meetingIcon} />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className={`truncate ${pg.cardTitleMd}`}>
                {meeting.title || 'Untitled Meeting'}
              </h2>
              <p className={`mt-1 ${pg.cardMeta}`}>
                {fmtMeetingDateTime(meeting)}
              </p>
            </div>

            <div className="flex shrink-0 items-center gap-3 sm:gap-5">
              {duration ? (
                <span className={`flex items-center gap-2 ${pg.cardMeta}`}>
                  <Ico src={icoMeetingTime} size={22} />
                  {duration}
                </span>
              ) : null}
              <span className={`${pg.cardMeta} whitespace-nowrap`}>
                {fmtEndTime(meeting)}
              </span>
              <Ico src={icoMeetingChevron} size={20} />
            </div>
          </div>

          <p className={`mt-3 ${pg.section} tracking-normal`}>Key Points</p>
          <ul className="mt-2 space-y-1.5 pl-5">
            {bullets.map((point) => (
              <li
                key={point}
                className={`list-disc ${pg.cardMeta} leading-snug marker:text-[#b6baf2]`}
              >
                {point}
              </li>
            ))}
          </ul>

          {canGenerateSummary ? (
            <button
              type="button"
              onClick={handleGenerateSummary}
              disabled={generating}
              className="mt-4 rounded-[12px] border border-[#006bf9]/70 bg-[#006bf9]/15 px-4 py-2 text-[13px] font-bold text-white transition hover:bg-[#006bf9]/25 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {generating ? 'Generating Summary...' : 'Generate Summary'}
            </button>
          ) : null}
        </div>
      </div>
    </Link>
  )
}

export default function Meetings() {
  const user = useAuthStore((s) => s.user)

  const [loading, setLoading] = useState(true)
  const [meetings, setMeetings] = useState<Meeting[]>([])
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<MeetingTab>('all')
  const [generatingSummaryId, setGeneratingSummaryId] = useState<string | null>(null)

  const firstPoll = useRef(true)

  const reload = useCallback(async () => {
    if (firstPoll.current) setLoading(true)
    try {
      const data = await meetingsApi.list({ limit: 100 })
      setMeetings(data)
      setError(null)
    } catch {
      setError('Could not load meetings.')
    } finally {
      if (firstPoll.current) {
        firstPoll.current = false
        setLoading(false)
      }
    }
  }, [])

  useVisiblePolling(reload)

  const handleGenerateSummary = useCallback(
    async (meetingId: string) => {
      setGeneratingSummaryId(meetingId)
      try {
        await meetingsApi.summarize(meetingId, true)
        await reload()
        toast.success('Summary generated')
      } catch (err: unknown) {
        const msg =
          err && typeof err === 'object' && 'response' in err
            ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Summarization failed')
            : 'Summarization failed'
        toast.error(msg)
      } finally {
        setGeneratingSummaryId(null)
      }
    },
    [reload],
  )

  const filtered = useMemo(
    () => meetings.filter((m) => matchesDateTab(m, tab)),
    [meetings, tab],
  )

  const name = user?.display_name ?? user?.username ?? ''

  return (
    <DashboardNavShell>
      <div className={`min-h-screen text-white ${blk.pagePad}`}>
        <div className={blk.chromeRow}>
          <button type="button" aria-label="Notifications" className={blk.avatarBtn}>
            <Ico src={icoNotification} size={blk.notifIcon} />
          </button>
          <div className={`${blk.avatar} shrink-0 overflow-hidden rounded-full border border-white/10 bg-white/10`}>
            {user?.avatar_url ? (
              <img src={user.avatar_url} alt="" className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-[11px] font-bold text-white/80">
                {(name || 'U').slice(0, 2).toUpperCase()}
              </div>
            )}
          </div>
        </div>

        <h1 className={pg.title}>Meeting Summaries</h1>
        <p className={`mt-2 mb-6 ${pg.subtitle}`}>
          All meeting summaries in one place
        </p>

        <div className={blk.filterBar}>
          {FILTER_TABS.map((f) => (
            <FilterTab
              key={f.id}
              label={f.label}
              active={tab === f.id}
              onClick={() => setTab(f.id)}
            />
          ))}
        </div>

        {error ? <p className="mb-3 text-sm text-amber-400/85">{error}</p> : null}

        {loading ? (
          <div className="flex min-h-[50vh] items-center justify-center">
            <LoadingSpinner size="large" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded-[20px] border border-[#3f4253] bg-gradient-to-b from-[#000f33] to-[#000a26] px-6 py-24 text-center">
            <p className={pg.empty}>
              {meetings.length === 0 ? 'No meetings yet.' : 'No meetings match this filter.'}
            </p>
            {meetings.length === 0 ? (
              <p className="mt-3 text-[14px] text-[#b6baf2]">
                Start a recording from{' '}
                <Link to="/live" className="font-semibold text-[#006bf9] hover:underline">
                  Live Recording
                </Link>
                .
              </p>
            ) : null}
          </div>
        ) : (
          <div className={`flex flex-col ${blk.listSectionGap} overflow-y-auto pr-1`} style={SCROLL}>
            {filtered.map((m) => (
              <MeetingSummaryCard
                key={m.id}
                meeting={m}
                generating={generatingSummaryId === m.id}
                onGenerateSummary={handleGenerateSummary}
              />
            ))}
          </div>
        )}
      </div>
    </DashboardNavShell>
  )
}
