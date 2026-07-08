<<<<<<< HEAD
// Dashboard — primary landing page showing meeting list, stats, search & filter

import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiClient } from '../api/client'
import { useMeetings } from '../hooks/useMeetings'
import MeetingList from '../components/meeting/MeetingList'
import LoadingSpinner from '../components/ui/LoadingSpinner'
import type { DateFilter } from '../utils/constants'
import { DATE_FILTERS } from '../utils/constants'
=======
// Dashboard home — Figma node 550:167, scaled for real screens.

import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { format } from 'date-fns'
import DashboardNavShell from '../components/dashboard/DashboardNavShell'
import { useAuthStore } from '../store/authStore'
import { useWeather } from '../hooks/useWeather'
>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
import { meetingsApi } from '../api/meetings'
import { integrationsApi } from '../api/integrations'
import { commitmentsApi } from '../api/commitments'
import type { Meeting } from '../types/meeting'
import type { CommitmentRow } from '../api/commitments'

// ── Local icon paths (SVGs + 1 PNG from Figma, served from /public/icons/) ───
const icoWeatherCloud  = '/icons/ic-weather-cloud.png'  // real PNG
const icoLocation      = '/icons/ic-location.svg'
const icoTemperature   = '/icons/ic-temperature.svg'
const icoHumidity      = '/icons/ic-humidity.svg'
const icoWind          = '/icons/ic-wind.svg'
const dotBlue          = '/icons/dot-blue.svg'
const icoArrow         = '/icons/ic-arrow.svg'
const icoCalHeader     = '/icons/ic-calendar-header.svg'
const icoCalUpcoming   = '/icons/ic-upcoming.svg'
const icoTickBlue      = '/icons/ic-tick-blue.svg'
const icoTickLavender  = '/icons/ic-tick-lavender.svg'
const icoInbox         = '/icons/ic-inbox.svg'
const icoTaskHeader    = '/icons/ic-task.svg'
const icoMailHeader    = '/icons/ic-mail-header.svg'
const dotEmailBlue     = '/icons/dot-email.svg'
const icoMeetingHeader = '/icons/ic-meeting-header.svg'
const icoNotepad       = '/icons/ic-notepad.svg'
const icoCall          = '/icons/ic-call.svg'
const icoReviewList    = '/icons/ic-review-list.svg'
const icoMicGlow       = '/icons/ic-mic-glow.svg'
const icoSpark         = '/icons/ic-spark.svg'
const icoNotification  = '/icons/ic-notification.svg'

<<<<<<< HEAD
=======
// ── Types ─────────────────────────────────────────────────────────────────────
type CalEvent  = { id: string | null; summary: string; start: Record<string, string>; end?: Record<string, string> }
type EmailRow  = { id: string; from: string; subject: string; snippet: string; date: string }
type TaskCounts = { dueToday: number; upcoming: number; unplanned: number; completed: number }

// ── Helpers ───────────────────────────────────────────────────────────────────
function greet(name?: string) {
  const h = new Date().getHours()
  const g = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
  return name ? `${g}, ${name}` : g
}

function fmtEventTime(ev: CalEvent): string {
  const raw = ev.start?.dateTime ?? ev.start?.date ?? ''
  if (!raw) return ''
  if (ev.start?.date && !ev.start?.dateTime) return 'All day'
  try {
    const d = new Date(raw)
    return isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  } catch { return '' }
}

function fmtEventDuration(ev: CalEvent): string {
  if (!ev.end) return ''
  const s = new Date(ev.start?.dateTime ?? ev.start?.date ?? '')
  const e = new Date(ev.end?.dateTime ?? ev.end?.date ?? '')
  if (isNaN(s.getTime()) || isNaN(e.getTime())) return ''
  const m = Math.round((e.getTime() - s.getTime()) / 60000)
  return m > 0 ? `${m} min` : ''
}

function fmtDuration(seconds?: number | null) {
  if (!seconds || seconds <= 0) return ''
  const m = Math.round(seconds / 60)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60), r = m % 60
  return r ? `${h}h ${r}m` : `${h}h`
}

function computeCounts(items: CommitmentRow[]): TaskCounts {
  const today = new Date(); today.setHours(23, 59, 59, 999)
  let dueToday = 0, upcoming = 0, unplanned = 0, completed = 0
  for (const c of items) {
    if (c.status === 'completed') { completed++; continue }
    if (c.status === 'cancelled') continue
    const due = new Date(c.due_at ?? c.remind_at ?? '')
    if ((!c.due_at && !c.remind_at) || isNaN(due.getTime())) { unplanned++; continue }
    due <= today ? dueToday++ : upcoming++
  }
  return { dueToday, upcoming, unplanned, completed }
}

// ── Ico — fixed-size icon box, immune to flex stretching ─────────────────────
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

// ── Shared atoms ──────────────────────────────────────────────────────────────
function HGradDivider() {
  return (
    <div
      className="shrink-0 self-stretch w-px"
      style={{ background: 'linear-gradient(180deg,rgba(2,23,77,0) 0%,#02174d 47%,rgba(2,23,77,0) 100%)' }}
    />
  )
}

function VGradDivider() {
  return (
    <div
      className="h-px w-full"
      style={{ background: 'linear-gradient(90deg,rgba(2,23,77,0) 0%,#0f296c 47%,rgba(2,23,77,0) 100%)' }}
    />
  )
}

function DCard({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`flex flex-col overflow-hidden rounded-[18px] border border-[#3f4253] bg-gradient-to-b from-[#011137] to-[#000a26] ${className ?? ''}`}>
      {children}
    </div>
  )
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
export default function Dashboard() {
  const navigate  = useNavigate()
  const user      = useAuthStore((s) => s.user)
  const { data: weather, loading: weatherLoading } = useWeather()

<<<<<<< HEAD
  // Device recording state (polled)
  const [recordingState, setRecordingState] = useState<RecordingState>('idle')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [diskPercent, setDiskPercent] = useState<number | null>(null)
  const previousRecordingState = useRef<RecordingState>('idle')

  const pollRecordingStatus = useCallback(async () => {
    try {
      const res = await meetingsApi.getRecordingStatus()
      const nextState = res.state as RecordingState
      if (previousRecordingState.current === 'processing' && nextState === 'idle') {
        await fetchMeetings()
      }
      previousRecordingState.current = nextState
      setRecordingState(nextState)
      setSessionId(res.session_id)
    } catch {
      // Backend may be offline — stay idle
    }
  }, [fetchMeetings])
=======
  const [calEvents,  setCalEvents]  = useState<CalEvent[]>([])
  const [emails,     setEmails]     = useState<EmailRow[]>([])
  const [meetings,   setMeetings]   = useState<Meeting[]>([])
  const [counts,     setCounts]     = useState<TaskCounts>({ dueToday: 0, upcoming: 0, unplanned: 0, completed: 0 })
  const [notifCount, setNotifCount] = useState(0)
  const [draft,      setDraft]      = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db

  useEffect(() => {
    integrationsApi.listCalendarEvents({ days_past: 0, days_future: 3, max_results: 5 })
      .then((r) => setCalEvents((r.events ?? []).slice(0, 5) as CalEvent[]))
      .catch(() => {})
    integrationsApi.listGmailRecent({ max_results: 3 })
      .then((r) => setEmails((r.messages ?? []).slice(0, 3)))
      .catch(() => {})
    meetingsApi.list({ limit: 3 })
      .then((r) => setMeetings(r.slice(0, 3)))
      .catch(() => {})
    commitmentsApi.list({ status: 'all', limit: 100 })
      .then((r) => setCounts(computeCounts(r.commitments ?? [])))
      .catch(() => {})
    commitmentsApi.list({ status: 'active', limit: 100 })
      .then((r) => setNotifCount(r.count ?? 0))
      .catch(() => {})
  }, [])

<<<<<<< HEAD
  // Filter meetings by search and date
  const filteredMeetings = meetings.filter((meeting) => {
    if (searchQuery && !(meeting.title ?? '').toLowerCase().includes(searchQuery.toLowerCase())) {
      return false
    }
    const now = new Date()
    const meetingDate = parseUTC(meeting.start_time)
    switch (filter) {
      case 'today':
        return meetingDate.toDateString() === now.toDateString()
      case 'week': {
        const weekAgo = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000)
        return meetingDate >= weekAgo
      }
      case 'month': {
        const monthAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)
        return meetingDate >= monthAgo
      }
      default:
        return true
    }
  })

  const handleDeleteMeeting = async (id: string) => {
    try {
      await deleteMeeting(id)
      toast.success('Meeting deleted')
    } catch {
      toast.error('Failed to delete meeting')
    }
  }

  const handleStartRecording = async () => {
    try {
      const sid = await startRecording()
      toast.success('Recording started!')
      navigate('/live')
      setSessionId(sid)
    } catch {
      toast.error('Failed to start recording')
    }
  }

  const handleStopRecording = async () => {
    try {
      await meetingsApi.stop()
      toast.success('Recording stopped — processing...')
      pollRecordingStatus()
    } catch {
      toast.error('Failed to stop recording')
    }
  }

  const handleResetRecording = async () => {
    try {
      await meetingsApi.resetRecordingState()
      setRecordingState('idle')
      setSessionId(null)
      toast.success('Recording state reset')
    } catch {
      toast.error('Failed to reset recording state')
    }
  }

  // Stat helpers
  const meetingsThisWeek = meetings.filter((m) => {
    const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
    return parseUTC(m.start_time) >= weekAgo
  }).length

  const totalHours = Math.round(
    meetings.reduce((acc, m) => acc + (m.duration || 0), 0) / 3600
  )

  const pendingActions = meetings.reduce((acc, m) => acc + (m.pending_actions || 0), 0)

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <LoadingSpinner size="large" />
      </div>
    )
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

      {/* Header */}
      <div className="mb-8">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-4">
          <div>
            <h1 className="text-3xl font-bold text-gray-900">Meetings</h1>
            <p className="text-gray-600 mt-1">{meetings.length} total meetings</p>
          </div>

          <div className="flex items-center gap-2" data-tutorial="tutorial-recording">
            {recordingState === 'idle' && (
              <button
                onClick={handleStartRecording}
                className="inline-flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 font-medium"
              >
                <svg className="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 20 20">
                  <circle cx="10" cy="10" r="6" />
                </svg>
                Start Recording
              </button>
            )}
            {recordingState === 'recording' && (
              <button
                onClick={handleStopRecording}
                className="inline-flex items-center px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 font-medium"
              >
                Stop Recording
              </button>
            )}
            {recordingState !== 'idle' && (
              <span className="text-sm text-gray-500">
                {recordingState === 'recording' && 'Recording...'}
                {recordingState === 'processing' && 'Processing...'}
                {sessionId && ` (${sessionId.slice(0, 8)})`}
              </span>
            )}
            {recordingState === 'processing' && (
              <button
                onClick={handleResetRecording}
                className="ml-2 px-3 py-1 text-xs font-medium text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Reset
              </button>
            )}
          </div>
        </div>

        {/* Disk warning */}
        {diskPercent !== null && diskPercent > 80 && (
          <div className="mb-6 bg-red-50 border border-red-200 rounded-lg p-4 flex items-center justify-between">
            <div className="flex items-start">
              <svg className="h-5 w-5 text-red-400 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
              </svg>
              <div className="ml-3">
                <h3 className="text-sm font-medium text-red-800">
                  Disk usage is at {diskPercent.toFixed(0)}%
                </h3>
                <p className="mt-1 text-sm text-red-700">
                  Free up space to keep recording smoothly.
                </p>
              </div>
            </div>
            <button
              onClick={() => navigate('/system')}
              className="ml-4 px-4 py-2 text-sm font-medium text-red-700 bg-white border border-red-300 rounded-lg hover:bg-red-50 whitespace-nowrap"
            >
              Free up Space
            </button>
          </div>
        )}

        {/* Stats cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6" data-tutorial="tutorial-stats">
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <svg className="w-8 h-8 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
              </div>
              <div className="ml-4">
                <p className="text-sm text-gray-500">This Week</p>
                <p className="text-2xl font-bold text-gray-900">{meetingsThisWeek}</p>
=======
  const openAssistant = () => {
    const t = draft.trim()
    navigate(t ? `/assistant?q=${encodeURIComponent(t)}` : '/assistant')
  }

  const name = user?.display_name ?? user?.username ?? ''
  const meetingIcons = [icoNotepad, icoCall, icoReviewList]

  return (
    <DashboardNavShell>
      {/* Extra bottom padding so content clears the fixed assistant bar */}
      <div className="flex min-h-screen flex-col pb-20 text-white">

        {/* ── Top row ──────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between gap-3 px-5 pb-3 pt-4">
          <div className="min-w-0">
            <h1 className="truncate text-[28px] font-semibold leading-tight text-white">
              {greet(name)}
            </h1>
            <p className="mt-0.5 text-[13px] font-semibold text-[#9ba2b2]">
              {format(new Date(), 'EEEE, MMMM d')}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {/* Search bar */}
            <div className="hidden md:flex items-center gap-2 rounded-[14px] border border-[#3f4253] bg-[#000a26] px-4 py-2.5 w-[280px] xl:w-[360px]">
              <svg className="h-4 w-4 shrink-0 text-[#b6baf2]" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                <circle cx="11" cy="11" r="8" strokeWidth="2" />
                <path strokeWidth="2" strokeLinecap="round" d="M21 21l-4.35-4.35" />
              </svg>
              <span className="text-[13px] font-semibold text-[#b6baf2] truncate">
                Search meetings, emails, tasks, notes....
              </span>
            </div>

            {/* Notification */}
            <div className="relative">
              <button type="button" aria-label="Notifications"
                className="flex h-[38px] w-[38px] items-center justify-center rounded-full border border-[#21284b] bg-gradient-to-b from-[#000f33] to-[#000a26]">
                <Ico src={icoNotification} size={22} />
              </button>
              {notifCount > 0 && (
                <span className="absolute -right-1 -top-1 flex h-5 w-5 items-center justify-center rounded-full bg-[#006bf9] text-[10px] font-bold text-white">
                  {notifCount > 9 ? '9+' : notifCount}
                </span>
              )}
            </div>

            {/* Avatar */}
            <div className="h-[38px] w-[38px] shrink-0 overflow-hidden rounded-full border border-white/10 bg-white/10">
              {user?.avatar_url ? (
                <img src={user.avatar_url} alt="" className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full w-full items-center justify-center text-[11px] font-bold text-white/80">
                  {(name || 'U').slice(0, 2).toUpperCase()}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* ── Weather widget ────────────────────────────────────────────────── */}
        <div className="px-5 pb-3">
          <div className="flex items-stretch overflow-hidden rounded-[18px] border border-[#3f4253] bg-gradient-to-b from-[#02123c] to-[#000a26] min-h-[100px]">
            {/* Icon + temp + city */}
            <div className="flex items-center gap-3 px-5 py-3 min-w-[200px]">
              <Ico src={icoWeatherCloud} size={56} alt="weather" />
              <div>
                <p className="text-[11px] font-semibold text-[#9ba2b2] uppercase tracking-wide">Weather Update</p>
                <div className="flex items-baseline gap-1.5 mt-0.5">
                  <span className="text-[26px] font-bold text-white leading-none">
                    {weatherLoading ? '—' : weather?.temperature != null ? `${weather.temperature}°c` : '—'}
                  </span>
                  <span className="text-[13px] font-semibold text-[#9ba2b2]">
                    {weatherLoading ? '' : (weather?.condition ?? 'partly cloudy')}
                  </span>
                </div>
                <div className="flex items-center gap-1 mt-1">
                  <Ico src={icoLocation} size={13} />
                  <span className="text-[12px] font-semibold text-[#b6baf2]">
                    {weather?.city ?? 'Bengaluru'}
                  </span>
                </div>
>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
              </div>
            </div>
          </div>

<<<<<<< HEAD
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <svg className="w-8 h-8 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <div className="ml-4">
                <p className="text-sm text-gray-500">Total Hours</p>
                <p className="text-2xl font-bold text-gray-900">{totalHours}</p>
              </div>
            </div>
          </div>

          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <div className="flex items-center">
              <div className="flex-shrink-0">
                <svg className="w-8 h-8 text-yellow-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                </svg>
              </div>
              <div className="ml-4">
                <p className="text-sm text-gray-500">Pending Actions</p>
                <p className="text-2xl font-bold text-gray-900">{pendingActions}</p>
              </div>
=======
            <HGradDivider />
            <WeatherMetric icon={icoTemperature} iconSize={20}
              label="High / Low"
              value={weather?.high != null && weather?.low != null ? `${weather.high}° / ${weather.low}°` : '— / —'} />

            <HGradDivider />
            <WeatherMetric icon={icoHumidity} iconSize={18}
              label="Humidity"
              value={weather?.humidity != null ? `${weather.humidity}%` : '—'} />

            <HGradDivider />
            <WeatherMetric icon={icoWind} iconSize={22}
              label="Wind"
              value={weather?.wind_kph != null ? `${weather.wind_kph} km/h` : '—'} />

            <HGradDivider />
            {/* AQI — matches Figma: "AQI" label + colored number */}
            <div className="flex flex-1 flex-col items-center justify-center px-5 py-3">
              <div className="flex items-baseline gap-1.5">
                <span className="text-[19px] font-bold text-white">AQI</span>
                <span
                  className="text-[19px] font-bold"
                  style={{
                    color: weather?.aqi != null
                      ? weather.aqi <= 50 ? '#19d385'
                      : weather.aqi <= 100 ? '#f59e0b'
                      : '#ef4444'
                      : '#19d385',
                  }}
                >
                  {weather?.aqi ?? '—'}
                </span>
              </div>
              <span className="mt-0.5 text-[11px] font-semibold text-[#b6baf2]">
                {weather?.aqi_label ?? 'Good'}
              </span>
            </div>
          </div>
        </div>

        {/* ── 2×2 Cards ─────────────────────────────────────────────────────── */}
        <div className="grid flex-1 grid-cols-1 gap-3 px-5 lg:grid-cols-2">

          {/* Today's Schedule */}
          <DCard>
            <div className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-2">
                <Ico src={icoCalHeader} size={18} />
                <span className="text-[15px] font-semibold text-white">Today's Schedule</span>
              </div>
              <Link to="/calendar" className="flex items-center gap-1 text-[13px] font-semibold text-[#006bf9] hover:text-[#3f8cff] transition cursor-pointer">
                View full calendar
                <Ico src={icoArrow} size={11} />
              </Link>
            </div>
            <div className="flex flex-col">
              {calEvents.length === 0 ? (
                <>
                  <VGradDivider />
                  <p className="px-4 py-5 text-[12px] text-center text-[#9ba2b2]">
                    No events — connect Google Calendar in Settings.
                  </p>
                </>
              ) : calEvents.map((ev, i) => (
                <div key={ev.id ?? i}>
                  <VGradDivider />
                  <div className="flex items-center gap-2 px-4 py-2.5">
                    <span className="w-[70px] shrink-0 text-[13px] font-medium text-[#006bf9]">
                      {fmtEventTime(ev)}
                    </span>
                    <Ico src={dotBlue} size={9} />
                    <span className="flex-1 truncate text-[13px] font-semibold text-white">
                      {ev.summary || '(No title)'}
                    </span>
                    {fmtEventDuration(ev) && (
                      <span className="shrink-0 text-[13px] font-semibold text-[#b6baf2]">
                        {fmtEventDuration(ev)}
                      </span>
                    )}
                  </div>
                </div>
              ))}
>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
            </div>
          </DCard>

          {/* Tasks Overview */}
          <DCard>
            <div className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-2">
                <Ico src={icoTaskHeader} size={20} />
                <span className="text-[15px] font-semibold text-white">Tasks Overview</span>
              </div>
              <Link to="/tasks" className="text-[13px] font-semibold text-[#006bf9] hover:text-[#3f8cff] transition cursor-pointer">
                View all tasks
              </Link>
            </div>
            <div className="grid grid-cols-2 gap-2.5 px-4 pb-4">
              <TaskSubCard icon={icoTickBlue}     iconBg="rgba(0,107,249,0.2)"    count={counts.dueToday}  countColor="#006bf9"  label="Due Today"  sub="high priority" />
              <TaskSubCard icon={icoCalUpcoming}  iconBg="rgba(169,113,212,0.2)"  count={counts.upcoming}  countColor="#a971d4"  label="Upcoming"   sub="Next: Tomorrow" />
              <TaskSubCard icon={icoInbox}        iconBg="rgba(25,211,133,0.2)"   count={counts.unplanned} countColor="#19d385"  label="Unplanned"  sub="In Inbox" />
              <TaskSubCard icon={icoTickLavender} iconBg="rgba(182,186,242,0.2)"  count={counts.completed} countColor="#b6baf2"  label="Completed"  sub="Today" />
            </div>
          </DCard>

          {/* Email Highlights */}
          <DCard>
            <div className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-2">
                <Ico src={icoMailHeader} size={18} />
                <span className="text-[15px] font-semibold text-white">Email Highlights</span>
              </div>
              <Link to="/emails" className="text-[13px] font-semibold text-[#006bf9] hover:text-[#3f8cff] transition cursor-pointer">
                View all emails
              </Link>
            </div>
            <div className="flex flex-col">
              {emails.length === 0 ? (
                <p className="px-4 py-5 text-[12px] text-center text-[#9ba2b2]">
                  No recent emails — connect Gmail in Settings.
                </p>
              ) : emails.map((m, i) => (
                <div key={m.id}>
                  {i > 0 && <VGradDivider />}
                  <div className="flex items-start gap-2.5 px-4 py-3">
                    <span className="mt-[5px]"><Ico src={dotEmailBlue} size={10} /></span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-[14px] font-semibold text-white truncate">
                          {m.from.replace(/<[^>]+>/g, '').replace(/"/g, '').trim()}
                        </span>
                        <span className="shrink-0 text-[12px] font-semibold text-white whitespace-nowrap">
                          {m.date}
                        </span>
                      </div>
                      <p className="mt-0.5 truncate text-[12px] font-medium text-[#b6baf2]">
                        {m.snippet || m.subject}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </DCard>

          {/* Recent Meeting Summaries */}
          <DCard>
            <div className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-2">
                <Ico src={icoMeetingHeader} size={20} />
                <span className="text-[15px] font-semibold text-white">Recent Meeting Summaries</span>
              </div>
              <Link to="/meetings" className="text-[13px] font-semibold text-[#006bf9] hover:text-[#3f8cff] transition cursor-pointer">
                View all
              </Link>
            </div>
            <div className="flex flex-col">
              {meetings.length === 0 ? (
                <p className="px-4 py-5 text-[12px] text-center text-[#9ba2b2]">
                  No meetings yet.
                </p>
              ) : meetings.map((m, i) => (
                <div key={m.id}>
                  {i > 0 && <VGradDivider />}
                  <Link to={`/meeting/${m.id}`} className="flex items-start gap-3 px-4 py-3 group">
                    <div className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-[10px] border border-[#3f4253] bg-[#010b26]">
                      <Ico src={meetingIcons[i % meetingIcons.length]} size={22} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[14px] font-semibold text-white group-hover:text-[#006bf9] transition">
                        {m.title || 'Untitled Meeting'}
                      </p>
                      <p className="mt-0.5 text-[12px] font-medium text-[#b6baf2]">
                        {fmtDuration(m.duration)}{m.duration && m.status ? ' · ' : ''}{m.status?.replace(/_/g, ' ') ?? ''}
                      </p>
                    </div>
                  </Link>
                </div>
              ))}
            </div>
          </DCard>
        </div>
      </div>

      {/* ── Fixed assistant bar ───────────────────────────────────────────── */}
      <div className="fixed bottom-0 right-0 z-40 border-t border-[#3f4253]/60 bg-[#01081a]/90 backdrop-blur-xl lg:left-[272px] left-0">
        <div className="px-5 py-2.5">
          <div className="flex items-center overflow-hidden rounded-[14px] border border-[#3f4253] bg-gradient-to-b from-[#011137] to-[#000a26]" style={{ height: 52 }}>
            <div className="flex items-center gap-2 px-4 shrink-0">
              <Ico src={icoSpark} size={16} />
              <span className="text-[14px] font-semibold text-[#006bf9] whitespace-nowrap">
                Try saying....
              </span>
            </div>
            <input
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); openAssistant() } }}
              placeholder='"Summarize my meetings this week"'
              className="min-w-0 flex-1 bg-transparent text-[13px] text-[#9f9f9f] placeholder:text-[#9f9f9f] outline-none"
            />
            <button
              type="button"
              onClick={() => navigate('/assistant?voice=1')}
              className="flex shrink-0 items-center justify-center"
              aria-label="Voice assistant"
              style={{ width: 52, height: 52 }}
            >
              <Ico src={icoMicGlow} size={52} alt="mic" />
            </button>
          </div>
        </div>
      </div>
    </DashboardNavShell>
  )
}

<<<<<<< HEAD
        {/* Search and filter */}
        <div className="flex flex-col sm:flex-row gap-4" data-tutorial="tutorial-filters">
          <div className="flex-1">
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <svg className="h-5 w-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
              </div>
              <input
                type="text"
                placeholder="Search meetings..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="block w-full pl-10 pr-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              />
            </div>
          </div>

          <div className="flex space-x-2">
            {DATE_FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-4 py-2 text-sm font-medium rounded-lg ${
                  filter === f
                    ? 'bg-primary-600 text-white'
                    : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50'
                }`}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
        </div>
=======
// ── WeatherMetric ─────────────────────────────────────────────────────────────
function WeatherMetric({ icon, iconSize, label, value }: {
  icon: string; iconSize: number; label: string; value: string
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-0.5 px-4 py-3">
      <div className="flex items-center gap-1.5">
        <Ico src={icon} size={iconSize} />
        <span className="text-[19px] font-bold text-white leading-none">{value}</span>
      </div>
      <span className="text-[11px] font-semibold text-[#b6baf2]">{label}</span>
    </div>
  )
}

// ── TaskSubCard ───────────────────────────────────────────────────────────────
function TaskSubCard({ icon, iconBg, count, countColor, label, sub }: {
  icon: string; iconBg: string; count: number; countColor: string; label: string; sub: string
}) {
  return (
    <div className="flex items-center gap-3 overflow-hidden rounded-[14px] border-2 border-[#232a4f] py-3 px-3">
      <div
        className="flex h-[48px] w-[48px] shrink-0 items-center justify-center rounded-full border border-[#3f4253]"
        style={{ background: iconBg }}
      >
        <Ico src={icon} size={26} />
      </div>
      <div className="flex flex-col min-w-0">
        <span className="text-[24px] font-bold leading-tight" style={{ color: countColor }}>{count}</span>
        <span className="text-[13px] font-semibold text-white leading-tight">{label}</span>
        <span className="text-[11px] font-medium text-[#b6baf2] leading-tight">{sub}</span>
>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
      </div>

      {/* Meeting list */}
      <MeetingList
        meetings={filteredMeetings}
        onStartRecording={handleStartRecording}
        onDeleteMeeting={handleDeleteMeeting}
      />
    </div>
  )
}
