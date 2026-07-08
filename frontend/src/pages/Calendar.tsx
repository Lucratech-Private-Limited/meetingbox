// Calendar page — Figma node 627:1288 (updated design)
// Day/Week toggle removed; week summary moved to header top-right.
// Figma 1920px canvas → web content ~1168px → scale ×0.839 (+15%).

import { useCallback, useState, useMemo, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  format,
  startOfWeek,
  addDays,
  addWeeks,
  subWeeks,
  isSameDay,
  isToday,
  startOfDay,
  isBefore,
} from 'date-fns'
import DashboardNavShell from '../components/dashboard/DashboardNavShell'
import LoadingSpinner from '../components/ui/LoadingSpinner'
import { integrationsApi } from '../api/integrations'
import { useVisiblePolling } from '../hooks/useVisiblePolling'
import { useAuthStore } from '../store/authStore'

/** Public-folder icon URL (works when Vite `base` is not `/`). */
function iconUrl(file: string): string {
  const base = import.meta.env.BASE_URL ?? '/'
  return `${base}icons/${file}`
}

// ── SVG icons ─────────────────────────────────────────────────────────────────
const icoCalHeader    = iconUrl('ic-calendar-header.svg')
const icoCalClock     = iconUrl('ic-cal-clock.svg')
const icoCalSun       = iconUrl('ic-cal-sun.svg')
const icoCalScheduleMeeting = iconUrl('ic-cal-schedule-meeting.svg')
const icoCalVideo     = iconUrl('ic-cal-video.svg')
const icoCalAdd       = iconUrl('ic-cal-add.svg')
const icoCalCall      = iconUrl('ic-call.svg')
const icoCalReview    = iconUrl('ic-review-list.svg')
const icoNotification = iconUrl('ic-notification.svg')


// ── Types ─────────────────────────────────────────────────────────────────────
type CalEvent = {
  id: string | null
  summary: string
  start: Record<string, string>
  end?: Record<string, string>
  htmlLink?: string
  location?: string
  description?: string
  organizer?: string
  hangoutLink?: string
  reminders?: unknown
}

// ── Pure helpers ──────────────────────────────────────────────────────────────
function getEventDate(ev: CalEvent): Date | null {
  const raw = ev.start?.dateTime ?? ev.start?.date ?? ''
  if (!raw) return null
  const d = new Date(raw)
  return isNaN(d.getTime()) ? null : d
}

function formatEventTime(ev: CalEvent): string {
  if (ev.start?.date && !ev.start?.dateTime) return 'All day'
  const raw = ev.start?.dateTime ?? ''
  if (!raw) return ''
  const d = new Date(raw)
  return isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

function getEventDurationMin(ev: CalEvent): number {
  if (!ev.end) return 0
  const s = new Date(ev.start?.dateTime ?? '')
  const e = new Date(ev.end?.dateTime ?? ev.end?.date ?? '')
  if (isNaN(s.getTime()) || isNaN(e.getTime())) return 0
  return Math.max(0, Math.round((e.getTime() - s.getTime()) / 60000))
}

/** True if the event blocks the afternoon slot (Figma "Free: EEE afternoon" logic). */
function eventBlocksAfternoon(ev: CalEvent): boolean {
  if (ev.start?.date && !ev.start?.dateTime) return true
  const ed = getEventDate(ev)
  if (!ed) return false
  return ed.getHours() >= 12
}

function formatDuration(mins: number): string {
  if (mins <= 0) return ''
  if (mins < 60) return `${mins} min`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m ? `${h}h ${m}m` : `${h}h`
}

function formatEventDetailWhen(ev: CalEvent): string {
  const s = getEventDate(ev)
  if (!s) return ''
  if (ev.start?.date && !ev.start?.dateTime) {
    return `${format(s, 'EEEE, d MMMM')} · All day`
  }
  const rawEnd = ev.end?.dateTime ?? ev.end?.date ?? ''
  const e = rawEnd ? new Date(rawEnd) : null
  const endOk = e && !isNaN(e.getTime())
  const startTxt = `${format(s, 'h:mm a')}`
  const endTxt = endOk ? format(e!, 'h:mm a') : ''
  return `${format(s, 'EEEE, d MMMM')} · ${startTxt}${endTxt ? ` – ${endTxt}` : ''}`
}

function plainDescription(raw: string): string {
  if (!raw) return ''
  const d = document.createElement('div')
  d.innerHTML = raw
  const t = (d.textContent || d.innerText || '').trim()
  return t
}

function formatRemindersLine(reminders: unknown): string | null {
  if (!reminders || typeof reminders !== 'object') return null
  const r = reminders as { useDefault?: boolean; overrides?: Array<{ method?: string; minutes?: number }> }
  if (r.useDefault) return 'Default (from Calendar settings)'
  const o = r.overrides
  if (!Array.isArray(o) || o.length === 0) return null
  const parts = o.map((x) => {
    const m = x.minutes
    if (m === 0) return 'At time of event'
    if (m != null) return `${m} minutes before`
    return null
  }).filter(Boolean) as string[]
  return parts.length ? parts.join(' · ') : null
}

// ── Fixed-size icon helper ────────────────────────────────────────────────────
function Ico({ src, size, alt = '' }: { src: string; size: number; alt?: string }) {
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center"
      style={{ width: size, height: size, minWidth: size }}
    >
      <img src={src} alt={alt} className="block max-h-full max-w-full object-contain" />
    </span>
  )
}

/** Inline SVG so the bar chart always renders (avoids public URL / img fetch edge cases). */
function IcoCalBarchart({ size }: { size: number }) {
  const h = size
  const w = Math.round((size * 36) / 44)
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center text-[#006BF9]"
      style={{ width: w, height: h, minWidth: w }}
      aria-hidden
    >
      <svg width={w} height={h} viewBox="0 0 36 44" fill="none" xmlns="http://www.w3.org/2000/svg" className="block">
        <rect x="0" y="24" width="9" height="20" rx="4.5" fill="currentColor" />
        <rect x="13" y="0" width="9" height="44" rx="4.5" fill="currentColor" />
        <rect x="27" y="11" width="9" height="33" rx="4.5" fill="currentColor" />
      </svg>
    </span>
  )
}

function EventDetailSheet({
  ev,
  onClose,
  calendarLabel,
}: {
  ev: CalEvent
  onClose: () => void
  calendarLabel: string
}) {
  const inviteUrl = (ev.hangoutLink || ev.htmlLink || '').trim()
  const desc = plainDescription(ev.description || '')
  const reminder = formatRemindersLine(ev.reminders)
  const organizer = (ev.organizer || '').trim()
  const calLine = organizer || calendarLabel

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const copyInvite = () => {
    if (!inviteUrl) return
    void navigator.clipboard.writeText(inviteUrl)
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center sm:items-center overflow-y-auto p-3 sm:p-6">
      <button
        type="button"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        aria-label="Dismiss"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="cal-ev-detail-title"
        className="relative z-[101] my-6 w-full max-w-[460px] rounded-[22px] sm:rounded-[28px] border border-[#3f4253] bg-gradient-to-b from-[#061536] to-[#01081a] shadow-[0_28px_90px_rgba(0,0,0,0.6)]"
      >
        <div className="flex items-center justify-between gap-2 border-b border-[#21284b] px-4 py-3 sm:px-5">
          <div className="flex items-center gap-0.5">
            {ev.htmlLink && (
              <button
                type="button"
                title="Edit in Google Calendar"
                onClick={() => window.open(ev.htmlLink, '_blank')}
                className="flex h-10 w-10 items-center justify-center rounded-xl text-[#b6baf2] hover:bg-white/5 hover:text-white transition"
              >
                <svg className="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                </svg>
              </button>
            )}
            <button
              type="button"
              disabled
              title="Delete (open Calendar to remove)"
              className="flex h-10 w-10 items-center justify-center rounded-xl text-[#4a5060] cursor-not-allowed"
            >
              <svg className="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z" />
              </svg>
            </button>
            {organizer.includes('@') && (
              <a
                href={`mailto:${organizer}`}
                className="flex h-10 w-10 items-center justify-center rounded-xl text-[#b6baf2] hover:bg-white/5 hover:text-white transition"
                title="Email organizer"
              >
                <svg className="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M4 4h16v16H4z" />
                  <path d="m22 6-10 7L2 6" />
                </svg>
              </a>
            )}
          </div>
          <button
            type="button"
            title="Close"
            onClick={onClose}
            className="flex h-11 w-11 items-center justify-center rounded-full border-2 border-[#006bf9] text-white hover:bg-[#006bf9]/15 transition"
          >
            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="px-4 sm:px-5 pt-4 flex gap-3">
          <span className="mt-1.5 h-3 w-3 shrink-0 rounded-[4px] bg-[#006bf9]" aria-hidden />
          <div className="min-w-0 pb-2">
            <h2 id="cal-ev-detail-title" className="text-[20px] sm:text-[24px] font-bold text-white leading-snug">
              {ev.summary || '(No title)'}
            </h2>
            <p className="mt-2 text-[14px] sm:text-[15px] font-medium text-[#9ba2b2] leading-snug">
              {formatEventDetailWhen(ev)}
            </p>
          </div>
        </div>

        {inviteUrl ? (
          <div className="px-4 sm:px-5 pb-4">
            <button
              type="button"
              onClick={copyInvite}
              className="inline-flex items-center gap-2 rounded-full border border-[#3f4253] bg-[#010b26] px-4 py-2.5 text-[14px] font-semibold text-[#006bf9] hover:border-[#006bf9]/50 hover:bg-[#006bf9]/10 transition"
            >
              <svg className="h-[17px] w-[17px] shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
              </svg>
              Invite via link
            </button>
          </div>
        ) : null}

        <div className="border-t border-[#21284b] px-4 sm:px-5 py-4 space-y-4">
          {desc ? (
            <div className="flex gap-3">
              <span className="mt-0.5 text-[#b6baf2] shrink-0" aria-hidden>
                <svg className="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M4 6h16M4 12h16M4 18h8" />
                </svg>
              </span>
              <p className="text-[13px] sm:text-[14px] leading-relaxed text-[#cfd3e6] whitespace-pre-wrap">{desc}</p>
            </div>
          ) : null}

          {reminder ? (
            <div className="flex gap-3">
              <span className="mt-0.5 text-[#b6baf2] shrink-0" aria-hidden>
                <svg className="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0" />
                </svg>
              </span>
              <p className="text-[13px] sm:text-[14px] font-medium text-[#cfd3e6]">{reminder}</p>
            </div>
          ) : null}

          {calLine ? (
            <div className="flex gap-3">
              <span className="mt-0.5 text-[#b6baf2] shrink-0" aria-hidden>
                <svg className="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="4" width="18" height="18" rx="2" />
                  <path d="M16 2v4M8 2v4M3 10h18" />
                </svg>
              </span>
              <p className="text-[13px] sm:text-[14px] font-medium text-[#cfd3e6]">{calLine}</p>
            </div>
          ) : null}

          {ev.location?.trim() ? (
            <div className="flex gap-3">
              <span className="mt-0.5 text-[#b6baf2] shrink-0" aria-hidden>
                <svg className="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 21s8-4.5 8-11a8 8 0 1 0-16 0c0 6.5 8 11 8 11z" />
                  <circle cx="12" cy="10" r="2.5" />
                </svg>
              </span>
              <p className="text-[13px] sm:text-[14px] font-medium text-[#cfd3e6]">{ev.location.trim()}</p>
            </div>
          ) : null}
        </div>

        {ev.htmlLink ? (
          <div className="flex flex-wrap gap-2 border-t border-[#21284b] px-4 py-4 sm:px-5">
            <button
              type="button"
              onClick={() => window.open(ev.htmlLink, '_blank')}
              className="rounded-full border border-[#3f8cff] bg-gradient-to-b from-[#0059dc] to-[#013da7] px-4 py-2.5 text-[13px] font-bold text-white hover:opacity-90 transition"
            >
              Open in Google Calendar
            </button>
          </div>
        ) : null}
      </div>
    </div>
  )
}

const DAY_ABBREVS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
const EVENT_ICONS = [icoCalScheduleMeeting, icoCalCall, icoCalReview]

// ─────────────────────────────────────────────────────────────────────────────
export default function Calendar() {
  const navigate = useNavigate()
  const user     = useAuthStore((s) => s.user)

  const [weekStart,   setWeekStart]   = useState<Date>(() => startOfWeek(new Date(), { weekStartsOn: 1 }))
  const [selectedDay, setSelectedDay] = useState<Date>(() => new Date())
  const [events,      setEvents]      = useState<CalEvent[]>([])
  const [loading,     setLoading]     = useState(true)
  const [connected,   setConnected]   = useState(false)
  const [detailEvent, setDetailEvent] = useState<CalEvent | null>(null)

  const reload = useCallback(async () => {
    try {
      const res = await integrationsApi.listCalendarEvents({ days_past: 14, days_future: 21, max_results: 200 })
      setConnected(Boolean(res.connected))
      setEvents((res.events ?? []) as CalEvent[])
    } catch {
      // silent — empty-state UI handles feedback
    } finally {
      setLoading(false)
    }
  }, [])

  useVisiblePolling(reload)

  const weekDays = useMemo(
    () => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)),
    [weekStart],
  )

  const eventsOnDay = useCallback(
    (day: Date) =>
      events.filter((ev) => {
        const d = getEventDate(ev)
        return d ? isSameDay(d, day) : false
      }),
    [events],
  )

  const selectedDayEvents = useMemo(
    () =>
      eventsOnDay(selectedDay).sort(
        (a, b) => (getEventDate(a)?.getTime() ?? 0) - (getEventDate(b)?.getTime() ?? 0),
      ),
    [eventsOnDay, selectedDay],
  )

  const freeUntilText = useMemo(() => {
    if (selectedDayEvents.length === 0) return 'No events scheduled'
    const first = getEventDate(selectedDayEvents[0])
    if (!first) return 'No events scheduled'
    return `You're free till ${first.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
  }, [selectedDayEvents])

  // Week summary — "Free" = first remaining day in the strip (today → Sun) with no
  // afternoon/all-day conflict. Without filtering past days, Monday was always first.
  const weekSummary = useMemo(() => {
    const today = startOfDay(new Date())

    const busyDaysAllWeek = weekDays
      .filter((d) => eventsOnDay(d).length >= 1)
      .map((d) => format(d, 'EEE'))

    const busyLabel = busyDaysAllWeek.length
      ? `This Week Busy: ${busyDaysAllWeek.join(', ')}`
      : 'This Week: All free'

    const freeDaysFromToday = weekDays
      .filter((d) => !isBefore(startOfDay(d), today))
      .filter((d) => !eventsOnDay(d).some(eventBlocksAfternoon))

    const freeLine =
      freeDaysFromToday.length > 0 ? `Free: ${format(freeDaysFromToday[0], 'EEE')} afternoon` : ''

    return {
      busy: busyLabel,
      free: freeLine,
    }
  }, [weekDays, eventsOnDay])

  const name = user?.display_name ?? user?.username ?? ''

  return (
    <DashboardNavShell>
      <div className="min-h-screen bg-[#01081a] text-white px-3 sm:px-5 lg:px-7 pt-5 pb-12">

        {/* ════════════════════════════════════════════════════════════════════
            HEADER
            Left : "Today" + calendar icon + date
            Right: bar-chart icon + week summary | bell | avatar
        ════════════════════════════════════════════════════════════════════ */}
        <div className="flex items-start justify-between mb-4">

          {/* Left: Today + date */}
          <div>
            <h1 className="text-[30px] sm:text-[35px] font-bold text-white leading-none">Today</h1>
            <div className="flex items-center gap-2 mt-1">
              <Ico src={icoCalHeader} size={20} />
              <span className="text-[17px] sm:text-[21px] font-semibold text-[#006bf9] leading-tight">
                {format(selectedDay, 'EEE , MMM d')}
              </span>
            </div>
          </div>

          {/* Right: week summary + bell + avatar */}
          <div className="flex items-center gap-3 sm:gap-4">

            {/* Week summary — visible md and above */}
            <div className="hidden md:flex items-center gap-3 shrink-0">
              <IcoCalBarchart size={36} />
              <div className="min-w-0">
                <p className="text-[13px] lg:text-[15px] font-semibold text-[#b6baf2] whitespace-nowrap leading-snug">
                  {weekSummary.busy}
                </p>
                {weekSummary.free && (
                  <p className="text-[12px] lg:text-[14px] font-semibold text-[#b6baf2] whitespace-nowrap leading-snug">
                    {weekSummary.free}
                  </p>
                )}
              </div>
            </div>

            {/* Notification bell */}
            <button
              type="button"
              aria-label="Notifications"
              className="flex h-[36px] w-[36px] sm:h-[42px] sm:w-[42px] items-center justify-center rounded-full border border-[#21284b] bg-gradient-to-b from-[#000f33] to-[#000a26]"
            >
              <Ico src={icoNotification} size={19} />
            </button>

            {/* Avatar */}
            <div className="h-[36px] w-[36px] sm:h-[42px] sm:w-[42px] shrink-0 overflow-hidden rounded-full border border-white/10 bg-white/10">
              {user?.avatar_url ? (
                <img src={user.avatar_url} alt="" className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full w-full items-center justify-center text-[10px] font-bold text-white/80">
                  {(name || 'U').slice(0, 2).toUpperCase()}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Week summary on mobile — shown below header */}
        <div className="md:hidden flex items-center gap-2.5 mb-3 shrink-0">
          <IcoCalBarchart size={26} />
          <div className="min-w-0">
            <p className="text-[11px] font-semibold text-[#b6baf2] leading-snug">{weekSummary.busy}</p>
            {weekSummary.free && (
              <p className="text-[11px] font-semibold text-[#b6baf2] leading-snug">{weekSummary.free}</p>
            )}
          </div>
        </div>

        {/* ════════════════════════════════════════════════════════════════════
            LOADING
        ════════════════════════════════════════════════════════════════════ */}
        {loading ? (
          <div className="flex min-h-[40vh] items-center justify-center">
            <LoadingSpinner size="large" />
          </div>
        ) : (
          <>
            {/* ══════════════════════════════════════════════════════════════
                WEEK STRIP  — 7 equally-spaced day columns with ← / → arrows
                No right panel (week summary is in the header now).
                Figma: h=162px, rounded=29.7px → web: ~136px, ~24px
            ══════════════════════════════════════════════════════════════ */}
            <div className="mb-4 overflow-hidden rounded-[20px] sm:rounded-[24px] border border-[#3f4253] bg-gradient-to-b from-[#02123c] to-[#000a26]">
              <div className="flex items-stretch min-h-[108px] sm:min-h-[136px]">

                {/* ← Prev week */}
                <button
                  type="button"
                  onClick={() => setWeekStart((w) => subWeeks(w, 1))}
                  aria-label="Previous week"
                  className="flex items-center justify-center w-8 sm:w-11 shrink-0 text-white/60 hover:text-white transition"
                >
                  <svg className="h-[18px] w-[18px] sm:h-[20px] sm:w-[20px]" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                    <path strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
                  </svg>
                </button>

                {/* 7 equal day columns */}
                <div className="flex flex-1 items-stretch min-w-0">
                  {weekDays.map((day, i) => {
                    const dayEvs     = eventsOnDay(day)
                    const isSelected = isSameDay(day, selectedDay)
                    const isCurrent  = isToday(day)
                    const dotCount   = Math.min(dayEvs.length, 3)

                    return (
                      <div key={day.toISOString()} className="flex flex-1 items-stretch min-w-0">
                        {/* Gradient column divider */}
                        {i > 0 && (
                          <div
                            className="w-px self-stretch my-3 sm:my-4 shrink-0"
                            style={{
                              background: 'linear-gradient(180deg,rgba(2,23,77,0) 0%,#02174d 47%,rgba(2,23,77,0) 100%)',
                            }}
                          />
                        )}

                        <button
                          type="button"
                          onClick={() => setSelectedDay(day)}
                          className="relative flex flex-1 flex-col items-center justify-center gap-0.5 sm:gap-1 py-2 sm:py-3 transition min-w-0"
                        >
                          {/* Active day highlight ring */}
                          {isSelected && (
                            <>
                              <div className="pointer-events-none absolute inset-y-1 inset-x-px rounded-[10px] sm:rounded-[12px] border border-[#0484ff] blur-[4px] opacity-60" />
                              <div className="pointer-events-none absolute inset-y-1 inset-x-px rounded-[10px] sm:rounded-[12px] border border-[#0484ff] shadow-[0_4px_6px_rgba(0,0,0,0.3)]" />
                            </>
                          )}

                          {/* Day abbreviation — e.g. MON */}
                          <span
                            className={`relative z-10 text-[8px] sm:text-[12px] font-semibold tracking-widest leading-none text-center
                              ${isSelected || isCurrent ? 'text-white' : 'text-[#b6baf2]'}`}
                          >
                            {DAY_ABBREVS[i]}
                          </span>

                          {/* Date number — Figma 42.4px → web ~26px */}
                          <span
                            className={`relative z-10 text-[17px] sm:text-[26px] font-bold leading-tight text-center
                              ${isSelected ? 'text-white' : isCurrent ? 'text-[#006bf9]' : 'text-white'}`}
                          >
                            {format(day, 'd')}
                          </span>

                          {/* Event indicator dots */}
                          <div className="relative z-10 flex items-center justify-center gap-[4px] sm:gap-[5px] mt-0.5 h-[10px] sm:h-[13px]">
                            {dotCount > 0
                              ? Array.from({ length: dotCount }).map((_, j) => (
                                  <span
                                    key={j}
                                    className="h-[8px] w-[8px] sm:h-[11px] sm:w-[11px] rounded-full bg-[#006bf9] block shrink-0"
                                  />
                                ))
                              : (
                                <span className="h-[8px] w-[8px] sm:h-[11px] sm:w-[11px] rounded-full border border-[#b6baf2]/40 block shrink-0" />
                              )
                            }
                          </div>
                        </button>
                      </div>
                    )
                  })}
                </div>

                {/* → Next week */}
                <button
                  type="button"
                  onClick={() => setWeekStart((w) => addWeeks(w, 1))}
                  aria-label="Next week"
                  className="flex items-center justify-center w-8 sm:w-11 shrink-0 text-white/60 hover:text-white transition"
                >
                  <svg className="h-[18px] w-[18px] sm:h-[20px] sm:w-[20px]" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                    <path strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                </button>
              </div>
            </div>

            {/* ══════════════════════════════════════════════════════════════
                STATUS BANNER — "You're free till…" | "N meetings today"
                Figma: h=100px, rounded=24px; text 32.5px / 31.1px
                Web: auto height, rounded=22px; text ~21px / ~19px
            ══════════════════════════════════════════════════════════════ */}
            <div className="mb-4 overflow-hidden rounded-[18px] sm:rounded-[22px] border border-[#3f4253] bg-gradient-to-b from-[#02123c] to-[#000a26]">
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 sm:gap-0 px-4 sm:px-6 py-3 sm:py-4">
                <div className="flex items-center gap-3">
                  <Ico src={icoCalClock} size={36} />
                  <span className="text-[16px] sm:text-[21px] font-bold text-white leading-snug">{freeUntilText}</span>
                </div>
                <div className="flex items-center gap-2.5">
                  <Ico src={icoCalSun} size={30} />
                  <span className="text-[15px] sm:text-[19px] font-bold text-white">
                    {selectedDayEvents.length} meeting{selectedDayEvents.length !== 1 ? 's' : ''} today
                  </span>
                </div>
              </div>
            </div>

            {/* ══════════════════════════════════════════════════════════════
                TIMELINE + EVENT CARDS
                time-col: 80px desktop, 60px mobile
                dot-col:  36px desktop, 28px mobile
                line left = time-col + dot-col/2
            ══════════════════════════════════════════════════════════════ */}
            {selectedDayEvents.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                {!connected ? (
                  <>
                    <p className="mb-4 text-[14px] sm:text-[16px] text-[#9ba2b2]">
                      Connect Google Calendar to see your events.
                    </p>
                    <button
                      type="button"
                      onClick={() => navigate('/settings?tab=integrations')}
                      className="rounded-2xl border border-[rgba(0,107,249,0.35)] bg-[rgba(0,107,249,0.12)] px-5 py-2.5 text-[13px] sm:text-[15px] font-semibold text-[#006bf9] hover:bg-[rgba(0,107,249,0.2)] transition"
                    >
                      Connect Google Calendar
                    </button>
                  </>
                ) : (
                  <p className="text-[14px] sm:text-[16px] text-[#9ba2b2]">
                    No events for {format(selectedDay, 'EEEE, MMMM d')}
                  </p>
                )}
              </div>
            ) : (
              <div className="relative">
                {/* Vertical timeline gradient line */}
                <div
                  className="pointer-events-none absolute top-6 bottom-6 w-px sm:w-[2px]"
                  style={{
                    left: 'clamp(74px, 6.8vw, 98px)',
                    background:
                      'linear-gradient(180deg,rgba(154,189,255,0) 0%,rgb(154,189,255) 12%,rgb(154,189,255) 82%,rgba(154,189,255,0) 100%)',
                  }}
                />

                <div className="space-y-3 sm:space-y-4">
                  {selectedDayEvents.map((ev, i) => {
                    const timeStr  = formatEventTime(ev)
                    const parts    = timeStr.split(' ')
                    const timePart = parts[0] ?? ''
                    const period   = parts[1] ?? ''
                    const durText  = formatDuration(getEventDurationMin(ev))
                    const icon     = EVENT_ICONS[i % EVENT_ICONS.length]
                    const isFirst  = i === 0

                    return (
                      <div key={ev.id ?? i} className="relative flex items-center gap-0">

                        {/* Time label — Figma 28.25px time / 22.6px period → 23px / 18px web */}
                        <div className="w-[60px] sm:w-[80px] shrink-0 pr-2 text-right">
                          <span className="block text-[13px] sm:text-[17px] font-bold text-white leading-tight">{timePart}</span>
                          <span className="block text-[9px] sm:text-[13px] font-semibold text-[#b6baf2] leading-tight">{period}</span>
                        </div>

                        {/* Timeline dot — Figma ~30px → 23px desktop */}
                        <div className="relative z-10 flex w-[28px] sm:w-[36px] shrink-0 items-center justify-center">
                          <div className="h-[18px] w-[18px] sm:h-[23px] sm:w-[23px] rounded-full bg-[#006bf9] shadow-[0_0_10px_3px_rgba(0,107,249,0.55)]" />
                        </div>

                        {/* Event card — Figma h=142px, rounded=34.6px → ~116px, ~29px */}
                        <div className="flex-1 min-w-0 overflow-hidden rounded-[20px] sm:rounded-[28px] border border-[#21284b] bg-gradient-to-b from-[#011137] to-[#000a26]">
                          <div className="flex items-center gap-3 sm:gap-4 px-3 sm:px-5 py-3 sm:py-4">

                            {/* Icon box — Figma 96px → ~80px (always flex: Tailwind has no `xs` breakpoint, so `hidden xs:flex` never showed) */}
                            <div className="flex h-[58px] w-[58px] sm:h-[78px] sm:w-[78px] shrink-0 items-center justify-center rounded-[13px] sm:rounded-[18px] border border-[#3f4253] bg-[#010b26]">
                              <img src={icon} alt="" className="h-[34px] w-[34px] sm:h-[46px] sm:w-[46px] object-contain" />
                            </div>

                            {/* Title + duration */}
                            <div className="flex flex-1 flex-col min-w-0">
                              {/* Figma title 38.5px → 32px web */}
                              <span className="truncate text-[15px] sm:text-[23px] font-bold text-white leading-snug">
                                {ev.summary || '(No title)'}
                              </span>
                              <div className="flex items-center gap-1.5 mt-1 sm:mt-1.5">
                                <img src={icoCalClock} alt="" className="h-[15px] w-[15px] sm:h-[20px] sm:w-[20px] shrink-0 object-contain" />
                                {/* Figma duration 30.8px → 26px web */}
                                <span className="text-[11px] sm:text-[16px] font-semibold text-[#b6baf2]">
                                  {durText || 'All day'}
                                </span>
                              </div>
                            </div>

                            {/* Buttons — Figma h=76.9px → 64px; text 36.5px → 19px capped */}
                            <div className="flex items-center gap-2 sm:gap-3 shrink-0">
                              {isFirst && (
                                <button
                                  type="button"
                                  onClick={() => ev.htmlLink ? window.open(ev.htmlLink, '_blank') : undefined}
                                  className="hidden sm:flex items-center gap-2 rounded-[13px] sm:rounded-[16px] border border-[#3f8cff] bg-gradient-to-b from-[#0059dc] to-[#013da7] px-4 sm:px-5 h-[46px] sm:h-[62px] text-[14px] sm:text-[19px] font-bold text-white hover:opacity-90 transition"
                                >
                                  <img src={icoCalVideo} alt="" className="h-[20px] w-[20px] sm:h-[25px] sm:w-[25px] shrink-0 object-contain" />
                                  Join
                                </button>
                              )}
                              <button
                                type="button"
                                onClick={() => setDetailEvent(ev)}
                                className="flex items-center gap-1.5 sm:gap-2 rounded-[13px] sm:rounded-[16px] border border-[#3f8cff] px-3 sm:px-5 h-[40px] sm:h-[62px] text-[12px] sm:text-[19px] font-bold text-white hover:bg-white/5 transition whitespace-nowrap"
                              >
                                Details
                                <svg className="h-[13px] w-[13px] sm:h-[18px] sm:w-[18px] shrink-0 text-white/70" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                                  <path strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                                </svg>
                              </button>
                            </div>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* ══════════════════════════════════════════════════════════════
                ADD EVENT BUTTON
                Figma: h=72.7px, w=453px, rounded=20.3px, text=33.8px
                Web: h=66px, px-14, rounded=18px, text=23px
            ══════════════════════════════════════════════════════════════ */}
            <div className="mt-6 flex justify-center">
              <button
                type="button"
                onClick={() =>
                  connected
                    ? window.open('https://calendar.google.com/calendar/r/eventedit', '_blank')
                    : navigate('/settings?tab=integrations')
                }
                className="flex items-center gap-3 overflow-hidden rounded-[16px] sm:rounded-[18px] border border-[#21284b] bg-gradient-to-b from-[#011137] to-[#000a26] px-8 sm:px-14 h-[50px] sm:h-[66px] hover:border-[#3f8cff]/60 transition"
              >
                <img src={icoCalAdd} alt="" className="h-[22px] w-[22px] sm:h-[28px] sm:w-[28px] shrink-0 object-contain" />
                <span className="text-[16px] sm:text-[23px] font-bold text-[#006bf9]">Add event</span>
              </button>
            </div>
          </>
        )}
      </div>
      {detailEvent ? (
        <EventDetailSheet
          ev={detailEvent}
          onClose={() => setDetailEvent(null)}
          calendarLabel={user?.email ?? name ?? 'Primary calendar'}
        />
      ) : null}
    </DashboardNavShell>
  )
}
