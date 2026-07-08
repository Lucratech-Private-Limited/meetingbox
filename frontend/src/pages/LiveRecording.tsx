// Live recording page — real-time transcript and recording controls

import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWebSocket } from '../hooks/useWebSocket'
import { meetingsApi } from '../api/meetings'
import { formatDuration } from '../utils/formatters'
import toast from 'react-hot-toast'
import DashboardNavShell from '../components/dashboard/DashboardNavShell'

export default function LiveRecording() {
  const navigate = useNavigate()
  const { lastMessage } = useWebSocket()

  const [sessionId, setSessionId] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [speakerCount, setSpeakerCount] = useState(0)
  const [liveCaption, setLiveCaption] = useState('')
  const [transcript, setTranscript] = useState<string[]>([])
  const [isStopping, setIsStopping] = useState(false)

  // Load current recording status on mount
  useEffect(() => {
    const loadStatus = async () => {
      try {
        const res = await meetingsApi.getRecordingStatus()
        if (res.session_id) setSessionId(res.session_id)
      } catch {
        // ignore
      }
    }
    loadStatus()
  }, [])

  // Elapsed timer
  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed((e) => e + 1)
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  // Handle WebSocket messages
  useEffect(() => {
    if (!lastMessage) return
    try {
      const data = JSON.parse(lastMessage.data)

      if (data.type === 'transcription_update' && typeof data.text === 'string') {
        setLiveCaption(data.text)
        setTranscript((prev) => [...prev, data.text])
      }

      // Raw audio segment events are relayed without a `type` key.
      if (data.type === 'audio_segment' || typeof data.segment_num === 'number') {
        const segNum = typeof data.segment_num === 'number' ? data.segment_num : 0
        setSpeakerCount((prev) => Math.max(prev, segNum + 1))
      }

      if (data.type === 'speaker_detected' && typeof data.count === 'number') {
        setSpeakerCount(data.count)
      }
      if (data.type === 'recording_stopped') {
        navigate(`/meeting/${data.session_id}`)
      }
    } catch {
      // ignore parse errors
    }
  }, [lastMessage, navigate])

  const handleStop = async () => {
    setIsStopping(true)
    try {
      await meetingsApi.stop()
      toast.success('Recording stopped — processing...')
      navigate('/dashboard')
    } catch {
      toast.error('Failed to stop recording')
    } finally {
      setIsStopping(false)
    }
  }

  return (
    <DashboardNavShell>
    <div className="max-w-4xl mx-auto px-4 py-8">

      {/* Status bar */}
      <div className="bg-red-50 border-2 border-red-500 rounded-lg p-6 mb-8" data-tutorial="tutorial-live-panel">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div className="flex items-center space-x-4">
            <div className="flex items-center">
              <div className="w-4 h-4 bg-red-500 rounded-full animate-pulse mr-3" />
              <span className="text-2xl font-bold text-app-ink">RECORDING</span>
            </div>
            <div className="text-3xl font-mono font-bold text-app-ink">
              {formatDuration(elapsed)}
            </div>
          </div>

          <button
            onClick={handleStop}
            disabled={isStopping}
            className="px-6 py-3 bg-red-600 text-white rounded-lg hover:bg-red-700 font-medium disabled:opacity-50"
          >
            {isStopping ? 'Stopping...' : 'Stop Recording'}
          </button>
        </div>

        {speakerCount > 0 && (
          <div className="mt-4 flex items-center text-app-ink-muted">
            <svg className="w-5 h-5 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
            <span className="font-medium">
              {speakerCount} speaker{speakerCount > 1 ? 's' : ''} detected
            </span>
          </div>
        )}
      </div>

      {/* Live caption */}
      {liveCaption && (
        <div className="bg-app-surface rounded-lg border border-app-border p-6 mb-6">
          <h3 className="text-sm font-medium text-app-ink-subtle mb-2">Live Caption</h3>
          <p className="text-lg text-app-ink">{liveCaption}</p>
        </div>
      )}

      {/* Transcript */}
      <div className="bg-app-surface rounded-lg border border-app-border p-6">
        <h3 className="text-lg font-semibold text-app-ink mb-4">Transcript</h3>
        {transcript.length === 0 ? (
          <p className="text-app-ink-subtle text-center py-8">Waiting for speech...</p>
        ) : (
          <div className="space-y-3 max-h-96 overflow-y-auto">
            {transcript.map((text, index) => (
              <p key={index} className="text-app-ink-muted leading-relaxed">{text}</p>
            ))}
          </div>
        )}
      </div>
    </div>
    </DashboardNavShell>
  )
}

