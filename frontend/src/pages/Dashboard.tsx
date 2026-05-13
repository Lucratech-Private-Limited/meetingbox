// Dashboard — primary landing page showing meeting list, stats, search & filter

import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
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

export default function Dashboard() {
  const { meetings, loading, fetchMeetings, startRecording, deleteMeeting } = useMeetings()
  const navigate = useNavigate()
  const [searchQuery, setSearchQuery] = useState('')
  const [filter, setFilter] = useState<DateFilter>('today')

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
              </div>
            </div>
          </div>

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
            </div>
          </div>
        </div>

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
