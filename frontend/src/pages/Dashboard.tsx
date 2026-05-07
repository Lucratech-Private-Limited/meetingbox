// Dashboard — executive landing page showing today's priorities, recording state, stats, and meetings.

import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiClient } from '../api/client'
import { useMeetings } from '../hooks/useMeetings'
import MeetingList from '../components/meeting/MeetingList'
import LoadingSpinner from '../components/ui/LoadingSpinner'
import type { DateFilter } from '../utils/constants'
import { DATE_FILTERS } from '../utils/constants'
import { meetingsApi } from '../api/meetings'
import { parseUTC } from '../utils/formatters'
import toast from 'react-hot-toast'

type RecordingState = 'idle' | 'recording' | 'processing'

function greeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

function formatMeetingTime(value?: string | null): string {
  if (!value) return 'Time not set'
  const date = parseUTC(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export default function Dashboard() {
  const { meetings, loading, fetchMeetings, startRecording, deleteMeeting } = useMeetings()
  const navigate = useNavigate()
  const [searchQuery, setSearchQuery] = useState('')
  const [filter, setFilter] = useState<DateFilter>('today')

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

  useEffect(() => {
    pollRecordingStatus()
    const interval = setInterval(pollRecordingStatus, 3000)
    return () => clearInterval(interval)
  }, [pollRecordingStatus])

  useEffect(() => {
    apiClient.get('/api/system/status')
      .then((res) => setDiskPercent(res.data?.system?.disk_percent ?? null))
      .catch(() => {})
  }, [])

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

  const meetingsThisWeek = meetings.filter((m) => {
    const weekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
    return parseUTC(m.start_time) >= weekAgo
  }).length

  const totalHours = Math.round(
    meetings.reduce((acc, m) => acc + (m.duration || 0), 0) / 3600
  )

  const pendingActions = meetings.reduce((acc, m) => acc + (m.pending_actions || 0), 0)
  const todayMeetings = meetings.filter((m) => parseUTC(m.start_time).toDateString() === new Date().toDateString())
  const upcomingMeetings = meetings
    .filter((m) => parseUTC(m.start_time).getTime() >= Date.now() - 60 * 60 * 1000)
    .sort((a, b) => parseUTC(a.start_time).getTime() - parseUTC(b.start_time).getTime())
  const nextMeeting = upcomingMeetings[0] ?? todayMeetings[0] ?? meetings[0]
  const latestMeeting = [...meetings].sort((a, b) => parseUTC(b.start_time).getTime() - parseUTC(a.start_time).getTime())[0]

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <LoadingSpinner size="large" />
      </div>
    )
  }

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[radial-gradient(circle_at_top_left,#dff3ff_0,transparent_34%),linear-gradient(135deg,#f8fafc_0%,#eef6ff_48%,#f7f5ff_100%)]">
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <section className="mb-6 overflow-hidden rounded-[2rem] border border-white/70 bg-slate-950 shadow-2xl shadow-slate-200">
          <div className="grid gap-0 lg:grid-cols-[1.45fr_0.9fr]">
            <div className="relative p-6 text-white sm:p-8">
              <div className="absolute right-8 top-8 h-36 w-36 rounded-full bg-sky-400/20 blur-3xl" />
              <div className="relative">
                <div className="mb-5 inline-flex rounded-full border border-white/10 bg-white/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.25em] text-sky-100">
                  Executive workspace
                </div>
                <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">{greeting()}, Stark.</h1>
                <p className="mt-4 max-w-2xl text-base leading-7 text-slate-300">
                  Your meetings, approvals, assistant briefing, and recording controls are gathered here so the day starts clear.
                </p>

                <div className="mt-8 flex flex-wrap gap-3" data-tutorial="tutorial-recording">
                  {recordingState === 'idle' && (
                    <button
                      onClick={handleStartRecording}
                      className="inline-flex min-h-12 items-center rounded-2xl bg-sky-500 px-5 text-base font-semibold text-white shadow-lg shadow-sky-900/30 hover:bg-sky-400"
                    >
                      <span className="mr-2 h-3 w-3 rounded-full bg-white" />
                      Start Recording
                    </button>
                  )}
                  {recordingState === 'recording' && (
                    <button
                      onClick={handleStopRecording}
                      className="inline-flex min-h-12 items-center rounded-2xl bg-red-500 px-5 text-base font-semibold text-white shadow-lg shadow-red-900/30 hover:bg-red-400"
                    >
                      Stop Recording
                    </button>
                  )}
                  <Link
                    to="/assistant"
                    className="inline-flex min-h-12 items-center rounded-2xl border border-white/15 bg-white/10 px-5 text-base font-semibold text-white hover:bg-white/15"
                  >
                    Open Tony Assistant
                  </Link>
                  {recordingState === 'processing' && (
                    <button
                      onClick={handleResetRecording}
                      className="inline-flex min-h-12 items-center rounded-2xl border border-white/15 bg-white/10 px-5 text-base font-semibold text-white hover:bg-white/15"
                    >
                      Reset Processing
                    </button>
                  )}
                </div>

                {recordingState !== 'idle' && (
                  <p className="mt-4 text-sm text-sky-100">
                    Status: {recordingState === 'recording' ? 'Recording' : 'Processing'}
                    {sessionId && ` · ${sessionId.slice(0, 8)}`}
                  </p>
                )}
              </div>
            </div>

            <div className="border-t border-white/10 bg-white/8 p-6 backdrop-blur lg:border-l lg:border-t-0 sm:p-8">
              <div className="rounded-[1.5rem] border border-white/10 bg-white/10 p-5 text-white">
                <div className="text-xs font-semibold uppercase tracking-[0.22em] text-sky-100">Today at a glance</div>
                <div className="mt-5 space-y-4">
                  <div>
                    <div className="text-sm text-slate-300">Next focus</div>
                    <div className="mt-1 text-xl font-semibold">{nextMeeting?.title || 'No meeting selected'}</div>
                    <div className="mt-1 text-sm text-slate-400">{nextMeeting ? formatMeetingTime(nextMeeting.start_time) : 'Ask Tony for a briefing'}</div>
                  </div>
                  <div className="grid grid-cols-3 gap-3 text-center">
                    <div className="rounded-2xl bg-white/10 p-3">
                      <div className="text-2xl font-semibold">{todayMeetings.length}</div>
                      <div className="text-xs text-slate-300">Today</div>
                    </div>
                    <div className="rounded-2xl bg-white/10 p-3">
                      <div className="text-2xl font-semibold">{pendingActions}</div>
                      <div className="text-xs text-slate-300">Actions</div>
                    </div>
                    <div className="rounded-2xl bg-white/10 p-3">
                      <div className="text-2xl font-semibold">{diskPercent !== null ? `${diskPercent.toFixed(0)}%` : '—'}</div>
                      <div className="text-xs text-slate-300">Disk</div>
                    </div>
                  </div>
                  <Link to="/assistant" className="flex min-h-12 items-center justify-center rounded-2xl bg-white text-sm font-semibold text-slate-950 hover:bg-sky-50">
                    Run morning briefing
                  </Link>
                </div>
              </div>
            </div>
          </div>
        </section>

        {diskPercent !== null && diskPercent > 80 && (
          <div className="mb-6 flex items-center justify-between rounded-2xl border border-red-200 bg-red-50 p-4 shadow-sm">
            <div>
              <h3 className="text-sm font-semibold text-red-800">Disk usage is at {diskPercent.toFixed(0)}%</h3>
              <p className="mt-1 text-sm text-red-700">Free up space to keep recording smoothly.</p>
            </div>
            <button
              onClick={() => navigate('/system')}
              className="ml-4 min-h-11 rounded-xl border border-red-300 bg-white px-4 text-sm font-semibold text-red-700 hover:bg-red-50"
            >
              Free up Space
            </button>
          </div>
        )}

        <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-4" data-tutorial="tutorial-stats">
          <div className="rounded-[1.5rem] border border-white/80 bg-white/85 p-5 shadow-lg shadow-slate-200/70 backdrop-blur">
            <p className="text-sm font-medium text-slate-500">This week</p>
            <p className="mt-2 text-3xl font-semibold text-slate-950">{meetingsThisWeek}</p>
            <p className="mt-1 text-xs text-slate-400">meetings captured</p>
          </div>
          <div className="rounded-[1.5rem] border border-white/80 bg-white/85 p-5 shadow-lg shadow-slate-200/70 backdrop-blur">
            <p className="text-sm font-medium text-slate-500">Total hours</p>
            <p className="mt-2 text-3xl font-semibold text-slate-950">{totalHours}</p>
            <p className="mt-1 text-xs text-slate-400">recorded knowledge</p>
          </div>
          <div className="rounded-[1.5rem] border border-white/80 bg-white/85 p-5 shadow-lg shadow-slate-200/70 backdrop-blur">
            <p className="text-sm font-medium text-slate-500">Pending actions</p>
            <p className="mt-2 text-3xl font-semibold text-slate-950">{pendingActions}</p>
            <p className="mt-1 text-xs text-slate-400">awaiting review</p>
          </div>
          <div className="rounded-[1.5rem] border border-sky-100 bg-sky-50/90 p-5 shadow-lg shadow-slate-200/70 backdrop-blur">
            <p className="text-sm font-medium text-sky-700">Latest memory</p>
            <p className="mt-2 truncate text-lg font-semibold text-slate-950">{latestMeeting?.title || 'No meetings yet'}</p>
            <p className="mt-1 text-xs text-slate-500">{latestMeeting ? formatMeetingTime(latestMeeting.start_time) : 'Start recording to build memory'}</p>
          </div>
        </div>

        <div className="mb-6 rounded-[1.5rem] border border-white/80 bg-white/85 p-4 shadow-lg shadow-slate-200/70 backdrop-blur" data-tutorial="tutorial-filters">
          <div className="flex flex-col gap-4 sm:flex-row">
            <div className="flex-1">
              <div className="relative">
                <div className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4">
                  <svg className="h-5 w-5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                </div>
                <input
                  type="text"
                  placeholder="Search meetings, decisions, topics..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="block min-h-12 w-full rounded-2xl border border-slate-200 bg-white pl-11 pr-4 text-base text-slate-950 placeholder:text-slate-400 focus:border-sky-300 focus:ring-4 focus:ring-sky-100"
                />
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              {DATE_FILTERS.map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={`min-h-12 rounded-2xl px-4 text-sm font-semibold transition ${
                    filter === f
                      ? 'bg-slate-950 text-white shadow-lg shadow-slate-200'
                      : 'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
                  }`}
                >
                  {f.charAt(0).toUpperCase() + f.slice(1)}
                </button>
              ))}
            </div>
          </div>
        </div>

        <MeetingList
          meetings={filteredMeetings}
          onStartRecording={handleStartRecording}
          onDeleteMeeting={handleDeleteMeeting}
        />
      </div>
    </div>
  )
}
