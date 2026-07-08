// Meeting detail page — summary, transcript, actions tabs, export, summarize buttons

import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { format } from 'date-fns'
import { meetingsApi } from '../api/meetings'
import { parseUTC } from '../utils/formatters'
import { actionsApi } from '../api/actions'
import type { MeetingDetail as MeetingDetailType } from '../types/meeting'
import type { AgenticAction } from '../types/action'
import LoadingSpinner from '../components/ui/LoadingSpinner'
import DashboardNavShell from '../components/dashboard/DashboardNavShell'
import Modal from '../components/ui/Modal'
import Button from '../components/ui/Button'
import TranscriptView from '../components/meeting/TranscriptView'
import SummaryCard from '../components/meeting/SummaryCard'
import ActionCard from '../components/actions/ActionCard'
import CreateManualActions from '../components/actions/CreateManualActions'
import toast from 'react-hot-toast'

/** Pending Gmail/Calendar actions that count toward dashboard alerts (matches device home-summary). */
function isExecutablePendingAction(a: AgenticAction): boolean {
  if (a.status !== 'pending') return false
  const t = String(a.connector_target || '').toLowerCase()
  return t === 'gmail' || t === 'calendar'
}

type Tab = 'summary' | 'transcript' | 'actions' | 'recording'

export default function MeetingDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [meeting, setMeeting] = useState<MeetingDetailType | null>(null)
  const [actions, setActions] = useState<AgenticAction[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<Tab>('summary')
  const [summarizing, setSummarizing] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [isGeneratingActions, setIsGeneratingActions] = useState(false)
  /**
   * Last meeting "content fingerprint" we auto-generated for (id + whether summary/transcript exist).
   * When a summary is added after load (e.g. Summarize), fingerprint changes and we try again.
   */
  const autoGenerateFingerprintRef = useRef<string | null>(null)

  useEffect(() => {
    autoGenerateFingerprintRef.current = null
  }, [id])

  const loadMeetingData = useCallback(async () => {
    if (!id) return
    try {
      setLoading(true)
      setActions([])
      const meetingData = await meetingsApi.get(id)

      const raw = meetingData as unknown as Record<string, unknown>
      const normalized: MeetingDetailType = {
        ...(raw.meeting as MeetingDetailType),
        segments: (raw.segments as MeetingDetailType['segments']) ?? [],
        summary: (raw.summary as MeetingDetailType['summary']) ?? null,
      }

      setMeeting(normalized)

      let actionsResult: AgenticAction[] = []
      let listOk = false
      try {
        actionsResult = await actionsApi.list(id)
        listOk = true
      } catch {
        toast.error('Could not load meeting actions.')
      }

      if (listOk) {
        const segmentsLen = normalized.segments?.length ?? 0
        const hasSourceForActions = !!(normalized.summary || segmentsLen > 0)
        const fingerprint = `${id}:sum=${Boolean(normalized.summary)}:seg=${segmentsLen}`
        if (
          hasSourceForActions &&
          actionsResult.length === 0 &&
          autoGenerateFingerprintRef.current !== fingerprint
        ) {
          autoGenerateFingerprintRef.current = fingerprint
          try {
            actionsResult = await actionsApi.generate(id)
          } catch {
            /* integrations or LLM may be unavailable — user can use Refresh Suggestions */
          }
        }
      }

      setActions(actionsResult)
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
      await actionsApi.generate(id)
      const actionsData = await actionsApi.list(id)
      setActions(actionsData)
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
      await meetingsApi.summarize(id, true)
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

  const pendingDashboardCount = actions.filter(isExecutablePendingAction).length
  if (loading) {
    return (
      <DashboardNavShell>
        <div className="flex items-center justify-center min-h-[60vh]">
          <LoadingSpinner size="large" />
        </div>
      </DashboardNavShell>
    )
  }

  if (!meeting) {
    return (
      <DashboardNavShell>
        <div className="max-w-7xl mx-auto px-4 py-16 text-center">
          <h2 className="text-2xl font-bold text-app-ink mb-2">Meeting not found</h2>
          <p className="text-app-ink-muted mb-6">This meeting may have been deleted or doesn&apos;t exist.</p>
          <button
            onClick={() => navigate('/dashboard')}
            className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700"
          >
            Back to Dashboard
          </button>
        </div>
      </DashboardNavShell>
    )
  }

  const hasTranscript = meeting.segments && meeting.segments.length > 0
  const hasSummary = !!meeting.summary

  return (
    <DashboardNavShell>
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

      {/* Back link */}
      <button
        onClick={() => navigate('/dashboard')}
        className="flex items-center text-sm text-app-ink-muted hover:text-app-ink mb-4"
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
                className="text-2xl font-bold text-app-ink border border-app-border-light rounded-lg px-3 py-1 focus:ring-2 focus:ring-primary-500 focus:border-transparent"
              />
              <button
                onClick={handleSaveRename}
                className="px-3 py-1.5 text-sm font-medium text-white bg-primary-600 rounded-lg hover:bg-primary-700"
              >
                Save
              </button>
              <button
                onClick={() => setIsEditingTitle(false)}
                className="px-3 py-1.5 text-sm font-medium text-app-ink-muted bg-app-surface border border-app-border-light rounded-lg hover:bg-app-page"
              >
                Cancel
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2 mb-2 group">
              <h1 className="text-3xl font-bold text-app-ink">{meeting.title}</h1>
              <button
                onClick={handleStartRename}
                title="Rename meeting"
                className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded-lg text-app-ink-faint hover:text-primary-400 hover:bg-primary-900/40"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
              </button>
            </div>
          )}
          <div className="flex items-center space-x-4 text-sm text-app-ink-muted">
            <span>{format(parseUTC(meeting.start_time), 'PPpp')}</span>
            {meeting.duration != null && (
              <>
                <span>&bull;</span>
                <span>{Math.floor(meeting.duration / 60)} minutes</span>
              </>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2" data-tutorial="meeting-toolbar">
          <Link
            to={`/assistant?meeting=${encodeURIComponent(id || '')}`}
            className="px-4 py-2 text-sm font-medium text-primary-200 bg-primary-900/45 border border-primary-600/50 rounded-lg hover:bg-primary-800/55"
          >
            Assistant
          </Link>
          <button
            onClick={handleSummarize}
            disabled={summarizing}
            className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {summarizing ? 'Generating Summary...' : 'Generate Summary'}
          </button>
          <button
            onClick={() => handleExport('pdf')}
            className="px-4 py-2 text-sm font-medium text-app-ink-muted bg-app-surface border border-app-border-light rounded-lg hover:bg-app-page"
          >
            Export PDF
          </button>
          <button
            onClick={() => handleExport('txt')}
            className="px-4 py-2 text-sm font-medium text-app-ink-muted bg-app-surface border border-app-border-light rounded-lg hover:bg-app-page"
          >
            Export TXT
          </button>
          <button
            onClick={() => setShowDeleteConfirm(true)}
            className="px-4 py-2 text-sm font-medium text-red-700 bg-app-surface border border-red-300 rounded-lg hover:bg-red-50"
          >
            Delete
          </button>
        </div>
      </div>

      {/* Pending actions alert */}
      {pendingDashboardCount > 0 && (
        <div className="mb-6 bg-yellow-50 border border-yellow-200 rounded-lg p-4">
          <div className="flex">
            <svg className="h-5 w-5 text-yellow-400 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            <div className="ml-3">
              <h3 className="text-sm font-medium text-yellow-800">
                {pendingDashboardCount} AI action{pendingDashboardCount > 1 ? 's' : ''} ready to execute
              </h3>
              <p className="mt-1 text-sm text-yellow-700">
                Gmail and Google Calendar follow-ups only. Review details before each send or event creation.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-app-border mb-6" data-tutorial="meeting-tabs">
        <nav className="flex space-x-8">
          {(['summary', 'transcript', 'actions', 'recording'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`py-4 px-1 border-b-2 font-medium text-sm ${
                activeTab === tab
                  ? 'border-primary-500 text-primary-600'
                  : 'border-transparent text-app-ink-subtle hover:text-app-ink-muted hover:border-app-border-light'
              }`}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
              {tab === 'actions' && pendingDashboardCount > 0 && (
                <span className="ml-2 bg-yellow-100 text-yellow-800 py-0.5 px-2 rounded-full text-xs">
                  {pendingDashboardCount}
                </span>
              )}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab content */}
      <div>
        {activeTab === 'summary' && (
          <SummaryCard summary={meeting.summary} meetingId={meeting.id} />
        )}

        {activeTab === 'transcript' && (
          <TranscriptView segments={meeting.segments ?? []} />
        )}

        {activeTab === 'actions' && (
          <div className="space-y-4">
            <div className="flex flex-col gap-4 rounded-2xl border border-app-border bg-app-surface px-5 py-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0 flex-1">
                <h3 className="text-sm font-semibold text-app-ink">Calendar &amp; email</h3>
                <p className="text-sm text-app-ink-muted">
                  Suggested follow-ups load automatically when this meeting has a summary or transcript and your
                  accounts are connected. Use Refresh when you want new suggestions; connect accounts under Settings →
                  Integrations. You can also add your own calendar event or Gmail message below.
                </p>
              </div>
              <div className="flex shrink-0 flex-col items-stretch gap-3 sm:items-end">
                {id ? <CreateManualActions meetingId={id} onCreated={handleActionApproved} /> : null}
                <button
                  onClick={() => void handleGenerateActions()}
                  disabled={isGeneratingActions}
                  className="rounded-lg border border-primary-600/45 bg-primary-900/40 px-4 py-2 text-sm font-medium text-primary-200 hover:bg-primary-800/50 disabled:opacity-50"
                >
                  {isGeneratingActions ? 'Refreshing...' : 'Refresh Suggestions'}
                </button>
              </div>
            </div>
            {actions.length === 0 ? (
              <div className="text-center py-12 bg-app-surface rounded-lg border border-app-border">
                <svg className="mx-auto h-12 w-12 text-app-ink-faint" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <h3 className="mt-2 text-sm font-medium text-app-ink">No calendar or email actions yet</h3>
                <p className="mt-1 text-sm text-app-ink-subtle">
                  Connect Gmail and/or Calendar under Settings, ensure this meeting has a summary or transcript, then open
                  this tab again or click <span className="font-medium">Refresh Suggestions</span>.
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
          <div className="bg-app-surface rounded-lg border border-app-border p-6">
            {meeting.audio_path ? (
              <div className="space-y-4">
                <div className="flex items-center gap-3 mb-2">
                  <svg className="h-6 w-6 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072M12 6v12m-3.536-2.464a5 5 0 010-7.072M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <h3 className="text-lg font-semibold text-app-ink">Audio Recording</h3>
                </div>
                <audio
                  controls
                  className="w-full"
                  src={meetingsApi.getAudioUrl(meeting.id)}
                  preload="metadata"
                >
                  Your browser does not support the audio element.
                </audio>
                <p className="text-sm text-app-ink-subtle">
                  {meeting.duration != null
                    ? `Duration: ${Math.floor(meeting.duration / 60)}m ${meeting.duration % 60}s`
                    : 'Duration unknown'}
                </p>
              </div>
            ) : (
              <div className="text-center py-12">
                <svg className="mx-auto h-12 w-12 text-app-ink-faint" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                </svg>
                <h3 className="mt-2 text-sm font-medium text-app-ink">No recording available</h3>
                <p className="mt-1 text-sm text-app-ink-subtle">
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
        <p className="text-sm text-app-ink-muted mb-2">
          Are you sure you want to delete <strong>{meeting.title}</strong>?
        </p>
        <p className="text-sm text-app-ink-subtle mb-6">
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
    </DashboardNavShell>
  )
}

