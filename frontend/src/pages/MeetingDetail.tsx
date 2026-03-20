// Meeting detail page — summary, transcript, actions tabs, export, summarize buttons

import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { format } from 'date-fns'
import { meetingsApi } from '../api/meetings'
import { parseUTC } from '../utils/formatters'
import { actionsApi } from '../api/actions'
import type { MeetingDetail as MeetingDetailType } from '../types/meeting'
import type { AgenticAction } from '../types/action'
import LoadingSpinner from '../components/ui/LoadingSpinner'
import Modal from '../components/ui/Modal'
import Button from '../components/ui/Button'
import TranscriptView from '../components/meeting/TranscriptView'
import SummaryCard from '../components/meeting/SummaryCard'
import ActionCard from '../components/actions/ActionCard'
import toast from 'react-hot-toast'

type Tab = 'summary' | 'transcript' | 'actions' | 'recording'

export default function MeetingDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [meeting, setMeeting] = useState<MeetingDetailType | null>(null)
  const [actions, setActions] = useState<AgenticAction[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<Tab>('summary')
  const [summarizing, setSummarizing] = useState(false)
  const [summarizingLocal, setSummarizingLocal] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [isGeneratingActions, setIsGeneratingActions] = useState(false)

  const loadMeetingData = useCallback(async () => {
    if (!id) return
    try {
      setLoading(true)
      const meetingData = await meetingsApi.get(id)

      // Backend always returns { meeting, segments, summary, local_summary }
      const raw = meetingData as unknown as Record<string, unknown>
      const normalized: MeetingDetailType = {
        ...(raw.meeting as MeetingDetailType),
        segments: (raw.segments as MeetingDetailType['segments']) ?? [],
        summary: (raw.summary as MeetingDetailType['summary']) ?? null,
        local_summary: (raw.local_summary as MeetingDetailType['local_summary']) ?? null,
      }

      setMeeting(normalized)

      try {
        const actionsData = await actionsApi.list(id)
        setActions(actionsData)
      } catch {
        setActions([])
      }
    } catch {
      // Error state handled by loading/empty UI
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    loadMeetingData()
  }, [loadMeetingData])

  const handleGenerateActions = useCallback(async () => {
    if (!id) return
    try {
      setIsGeneratingActions(true)
      const generated = await actionsApi.generate(id)
      setActions(generated)
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Failed to generate actions')
          : 'Failed to generate actions'
      toast.error(msg)
    } finally {
      setIsGeneratingActions(false)
    }
  }, [id])

  useEffect(() => {
    if (!meeting || !id) return
    const hasSummarySource = !!meeting.summary || !!meeting.local_summary
    if (!hasSummarySource || actions.length > 0 || isGeneratingActions) return
    void handleGenerateActions()
  }, [actions.length, handleGenerateActions, id, isGeneratingActions, meeting])

  const handleExport = async (fmt: 'pdf' | 'txt') => {
    if (!id) return
    try {
      const blob = await meetingsApi.export(id, fmt)
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${meeting?.title || 'meeting'}.${fmt}`
      a.click()
      window.URL.revokeObjectURL(url)
      toast.success(`Exported as ${fmt.toUpperCase()}`)
    } catch {
      toast.error('Export failed')
    }
  }

  const handleSummarize = async () => {
    if (!id) return
    setSummarizing(true)
    try {
      await meetingsApi.summarize(id)
      await loadMeetingData()
      toast.success('Summary generated!')
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Summarization failed')
          : 'Summarization failed'
      toast.error(msg)
    } finally {
      setSummarizing(false)
    }
  }

  const handleSummarizeLocal = async () => {
    if (!id) return
    setSummarizingLocal(true)
    try {
      await meetingsApi.summarizeLocal(id)
      await loadMeetingData()
      toast.success('Local summary requested. It will appear when processing finishes.')
    } catch {
      toast.error('Local summarization failed. Is Ollama running?')
    } finally {
      setSummarizingLocal(false)
    }
  }

  const handleActionApproved = async () => {
    if (!id) return
    try {
      const actionsData = await actionsApi.list(id)
      setActions(actionsData)
    } catch {
      // keep current state
    }
  }

  const handleDeleteMeeting = async () => {
    if (!id) return
    try {
      setIsDeleting(true)
      await meetingsApi.delete(id)
      toast.success('Meeting deleted')
      navigate('/dashboard')
    } catch {
      toast.error('Failed to delete meeting')
    } finally {
      setIsDeleting(false)
      setShowDeleteConfirm(false)
    }
  }

  const handleStartRename = () => {
    setEditTitle(meeting?.title || '')
    setIsEditingTitle(true)
  }

  const handleSaveRename = async () => {
    if (!id || !editTitle.trim()) return
    try {
      await meetingsApi.update(id, { title: editTitle.trim() })
      setMeeting((prev) => prev ? { ...prev, title: editTitle.trim() } : prev)
      toast.success('Meeting renamed')
    } catch {
      toast.error('Failed to rename meeting')
    } finally {
      setIsEditingTitle(false)
    }
  }

  const handleRenameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSaveRename()
    if (e.key === 'Escape') setIsEditingTitle(false)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <LoadingSpinner size="large" />
      </div>
    )
  }

  if (!meeting) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-16 text-center">
        <h2 className="text-2xl font-bold text-gray-900 mb-2">Meeting not found</h2>
        <p className="text-gray-600 mb-6">This meeting may have been deleted or doesn&apos;t exist.</p>
        <button
          onClick={() => navigate('/dashboard')}
          className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700"
        >
          Back to Dashboard
        </button>
      </div>
    )
  }

  const hasTranscript = meeting.segments && meeting.segments.length > 0
  const hasSummary = !!meeting.summary
  const hasLocalSummary = !!meeting.local_summary

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

      {/* Back link */}
      <button
        onClick={() => navigate('/dashboard')}
        className="flex items-center text-sm text-gray-600 hover:text-gray-900 mb-4"
      >
        <svg className="w-5 h-5 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        Back to Dashboard
      </button>

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 mb-6">
        <div>
          {isEditingTitle ? (
            <div className="flex items-center gap-2 mb-2">
              <input
                type="text"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onKeyDown={handleRenameKeyDown}
                autoFocus
                className="text-2xl font-bold text-gray-900 border border-gray-300 rounded-lg px-3 py-1 focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              />
              <button
                onClick={handleSaveRename}
                className="px-3 py-1.5 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700"
              >
                Save
              </button>
              <button
                onClick={() => setIsEditingTitle(false)}
                className="px-3 py-1.5 text-sm font-medium text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2 mb-2 group">
              <h1 className="text-3xl font-bold text-gray-900">{meeting.title}</h1>
              <button
                onClick={handleStartRename}
                title="Rename meeting"
                className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded-lg text-gray-400 hover:text-primary-600 hover:bg-primary-50"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
              </button>
            </div>
          )}
          <div className="flex items-center space-x-4 text-sm text-gray-600">
            <span>{format(parseUTC(meeting.start_time), 'PPpp')}</span>
            {meeting.duration != null && (
              <>
                <span>&bull;</span>
                <span>{Math.floor(meeting.duration / 60)} minutes</span>
              </>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => handleExport('pdf')}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
          >
            Export PDF
          </button>
          <button
            onClick={() => handleExport('txt')}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
          >
            Export TXT
          </button>
          <button
            onClick={() => setShowDeleteConfirm(true)}
            className="px-4 py-2 text-sm font-medium text-red-700 bg-white border border-red-300 rounded-lg hover:bg-red-50"
          >
            Delete
          </button>
        </div>
      </div>

      {/* Summarize buttons — only when transcript exists and no summary yet */}
      {hasTranscript && (!hasSummary || !hasLocalSummary) && (
        <div className="mb-6 bg-white rounded-lg border border-gray-200 p-4 flex flex-wrap items-center gap-3">
          {!hasSummary && (
            <button
              onClick={handleSummarize}
              disabled={summarizing}
              className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
            >
              {summarizing ? 'Summarizing...' : 'Summarize with API'}
            </button>
          )}
          {!hasLocalSummary && (
            <button
              onClick={handleSummarizeLocal}
              disabled={summarizingLocal}
              className="px-4 py-2 bg-emerald-600 text-white text-sm font-medium rounded-lg hover:bg-emerald-700 disabled:opacity-50"
            >
              {summarizingLocal ? 'Requesting...' : 'Request Local Summary'}
            </button>
          )}
        </div>
      )}

      {/* Pending actions alert */}
      {actions.length > 0 && (
        <div className="mb-6 bg-yellow-50 border border-yellow-200 rounded-lg p-4">
          <div className="flex">
            <svg className="h-5 w-5 text-yellow-400 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            <div className="ml-3">
              <h3 className="text-sm font-medium text-yellow-800">
                {actions.length} AI action{actions.length > 1 ? 's' : ''} ready to execute
              </h3>
              <p className="mt-1 text-sm text-yellow-700">
                These are connector-aware actions the AI can carry out from this meeting.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200 mb-6">
        <nav className="flex space-x-8">
          {(['summary', 'transcript', 'actions', 'recording'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`py-4 px-1 border-b-2 font-medium text-sm ${
                activeTab === tab
                  ? 'border-primary-500 text-primary-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
              {tab === 'actions' && actions.length > 0 && (
                <span className="ml-2 bg-yellow-100 text-yellow-800 py-0.5 px-2 rounded-full text-xs">
                  {actions.length}
                </span>
              )}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab content */}
      <div>
        {activeTab === 'summary' && (
          <>
            <SummaryCard summary={meeting.summary} />
            {/* Show local summary below if it exists */}
            {meeting.local_summary && (
              <div className="mt-6">
                <h3 className="text-lg font-semibold text-gray-900 mb-4">Local Summary</h3>
                <SummaryCard summary={meeting.local_summary} />
              </div>
            )}
          </>
        )}

        {activeTab === 'transcript' && (
          <TranscriptView segments={meeting.segments ?? []} />
        )}

        {activeTab === 'actions' && (
          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-2xl border border-gray-200 bg-white px-5 py-4">
              <div>
                <h3 className="text-sm font-semibold text-gray-900">Agentic actions</h3>
                <p className="text-sm text-gray-600">
                  AI suggests only actions it can execute with active connectors or save in MeetingBox.
                </p>
              </div>
              <button
                onClick={() => void handleGenerateActions()}
                disabled={isGeneratingActions}
                className="rounded-lg border border-primary-200 bg-primary-50 px-4 py-2 text-sm font-medium text-primary-700 hover:bg-primary-100 disabled:opacity-50"
              >
                {isGeneratingActions ? 'Refreshing...' : 'Refresh Suggestions'}
              </button>
            </div>
            {actions.length === 0 ? (
              <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
                <svg className="mx-auto h-12 w-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <h3 className="mt-2 text-sm font-medium text-gray-900">No AI actions yet</h3>
                <p className="mt-1 text-sm text-gray-500">
                  Generate suggestions after the summary is ready to see connector-aware actions here.
                </p>
              </div>
            ) : (
              actions.map((action) => (
                <ActionCard
                  key={action.id}
                  action={action}
                  onChanged={handleActionApproved}
                />
              ))
            )}
          </div>
        )}

        {activeTab === 'recording' && (
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            {meeting.audio_path ? (
              <div className="space-y-4">
                <div className="flex items-center gap-3 mb-2">
                  <svg className="h-6 w-6 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072M12 6v12m-3.536-2.464a5 5 0 010-7.072M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <h3 className="text-lg font-semibold text-gray-900">Audio Recording</h3>
                </div>
                <audio
                  controls
                  className="w-full"
                  src={meetingsApi.getAudioUrl(meeting.id)}
                  preload="metadata"
                >
                  Your browser does not support the audio element.
                </audio>
                <p className="text-sm text-gray-500">
                  {meeting.duration != null
                    ? `Duration: ${Math.floor(meeting.duration / 60)}m ${meeting.duration % 60}s`
                    : 'Duration unknown'}
                </p>
              </div>
            ) : (
              <div className="text-center py-12">
                <svg className="mx-auto h-12 w-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                </svg>
                <h3 className="mt-2 text-sm font-medium text-gray-900">No recording available</h3>
                <p className="mt-1 text-sm text-gray-500">
                  The audio recording for this meeting is not available.
                </p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Delete confirmation modal */}
      <Modal
        isOpen={showDeleteConfirm}
        onClose={() => setShowDeleteConfirm(false)}
        title="Delete Meeting"
      >
        <p className="text-sm text-gray-600 mb-2">
          Are you sure you want to delete <strong>{meeting.title}</strong>?
        </p>
        <p className="text-sm text-gray-500 mb-6">
          This will permanently remove the recording, transcript, summary, and all associated actions. This cannot be undone.
        </p>
        <div className="flex justify-end gap-3">
          <Button
            variant="secondary"
            onClick={() => setShowDeleteConfirm(false)}
            disabled={isDeleting}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={handleDeleteMeeting}
            isLoading={isDeleting}
          >
            Delete Meeting
          </Button>
        </div>
      </Modal>
    </div>
  )
}

