// Renders the AI-generated meeting summary with decisions and action items

import { useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import type { MeetingSummary, ActionItem, ActionItemType } from '../../types/meeting'
import { postAssistantIntent } from '../../api/assistant'
import { useAuthStore } from '../../store/authStore'
import { parseSummaryReport } from '../../utils/parseSummaryReport'

interface SummaryCardProps {
  summary: MeetingSummary | null
  meetingId?: string | null
}

function normalizeActionItemType(raw: unknown): ActionItemType | undefined {
  if (raw !== 'email_draft' && raw !== 'calendar_invite' && raw !== 'task') {
    return undefined
  }
  return raw
}

/** When type is missing, show both buttons so older meeting payloads still work. */
function showScheduleForItem(item: ActionItem): boolean {
  const t = normalizeActionItemType(item.type)
  if (t === undefined) return true
  return t === 'calendar_invite'
}

function showEmailForItem(item: ActionItem): boolean {
  const t = normalizeActionItemType(item.type)
  if (t === undefined) return true
  return t === 'email_draft'
}

export default function SummaryCard({ summary, meetingId }: SummaryCardProps) {
  const token = useAuthStore((s) => s.token)
  const [schedulingIdx, setSchedulingIdx] = useState<number | null>(null)
  const [emailingIdx, setEmailingIdx] = useState<number | null>(null)

  const parsed = useMemo(
    () => parseSummaryReport(summary?.summary ?? ''),
    [summary],
  )

  const handleSchedule = async (item: ActionItem, idx: number) => {
    if (!token) {
      toast.error('Sign in to queue a calendar event.')
      return
    }
    if (!meetingId) {
      toast.error('Missing meeting id.')
      return
    }
    const parts = [
      'Schedule a calendar event for this meeting action item:',
      `"${item.task}".`,
      item.due_date ? `Time or deadline mentioned: ${item.due_date}.` : '',
      item.assignee ? `Assignee: ${item.assignee}.` : '',
    ].filter(Boolean)
    const message = parts.join(' ')
    setSchedulingIdx(idx)
    try {
      const res = await postAssistantIntent(message, meetingId)
      toast.success(res.assistant_message || 'Calendar request sent.')
      if (res.pending_actions && res.pending_actions.length > 0) {
        toast(
          'Open Settings → Integrations and use Assistant queue → Approve to create the calendar event.',
          { duration: 6000 },
        )
      }
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? String((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? '')
          : ''
      toast.error(msg || 'Could not queue calendar request.')
    } finally {
      setSchedulingIdx(null)
    }
  }

  const handleEmail = async (item: ActionItem, idx: number) => {
    if (!token) {
      toast.error('Sign in to queue an email draft.')
      return
    }
    if (!meetingId) {
      toast.error('Missing meeting id.')
      return
    }
    const parts = [
      'Draft and send an email for this meeting action item:',
      `"${item.task}".`,
      item.assignee ? `Related person or assignee: ${item.assignee}.` : '',
      item.due_date ? `Mentioned deadline: ${item.due_date}.` : '',
    ].filter(Boolean)
    const message = parts.join(' ')
    setEmailingIdx(idx)
    try {
      const res = await postAssistantIntent(message, meetingId)
      toast.success(res.assistant_message || 'Email draft queued.')
      if (res.pending_actions && res.pending_actions.length > 0) {
        toast('Open Settings → Integrations → Assistant queue to review, edit, and approve the email.', {
          duration: 6000,
        })
      }
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? String((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? '')
          : ''
      toast.error(msg || 'Could not queue email draft.')
    } finally {
      setEmailingIdx(null)
    }
  }

  if (!summary) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <p className="text-gray-500">No summary available yet. The meeting is still being processed.</p>
      </div>
    )
  }

  const hasParsedSections = Boolean(
    parsed.overview ||
      parsed.detailedAccount ||
      parsed.openQuestions.length > 0 ||
      parsed.risksConcerns.length > 0,
  )
  const useLegacySummaryOnly = !hasParsedSections && Boolean(summary.summary?.trim())

  const proseClass = 'text-gray-700 leading-relaxed whitespace-pre-wrap break-words'

  return (
    <div className="space-y-6">
      {useLegacySummaryOnly && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Summary</h3>
          <div className={proseClass}>{summary.summary}</div>
        </div>
      )}

      {!useLegacySummaryOnly && Boolean(parsed.overview) && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Overview</h3>
          <div className={proseClass}>{parsed.overview}</div>
        </div>
      )}

      {!useLegacySummaryOnly && parsed.detailedAccount && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Detailed account</h3>
          <div className={proseClass}>{parsed.detailedAccount}</div>
        </div>
      )}

      {!useLegacySummaryOnly && parsed.openQuestions.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Open questions</h3>
          <ul className="space-y-3">
            {parsed.openQuestions.map((q, i) => (
              <li key={`open-${i}-${q.slice(0, 48)}`} className="flex items-start">
                <svg
                  className="w-5 h-5 text-primary-600 mr-3 mt-0.5 flex-shrink-0"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                  aria-hidden
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                  />
                </svg>
                <span className="text-gray-700">{q}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!useLegacySummaryOnly && parsed.risksConcerns.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Risks / concerns</h3>
          <ul className="space-y-3">
            {parsed.risksConcerns.map((r, i) => (
              <li key={`risk-${i}-${r.slice(0, 48)}`} className="flex items-start">
                <svg
                  className="w-5 h-5 text-amber-500 mr-3 mt-0.5 flex-shrink-0"
                  fill="currentColor"
                  viewBox="0 0 20 20"
                  aria-hidden
                >
                  <path
                    fillRule="evenodd"
                    d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 10-2 0 1 1 0 002 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
                    clipRule="evenodd"
                  />
                </svg>
                <span className="text-gray-700">{r}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Decisions Made */}
      {summary.decisions?.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Decisions Made</h3>
          <ul className="space-y-3">
            {summary.decisions.map((decision) => (
              <li key={decision} className="flex items-start">
                <svg className="w-5 h-5 text-green-500 mr-3 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
                <span className="text-gray-700">{decision}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Action Items */}
      {summary.action_items?.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Action Items</h3>
          <p className="text-sm text-gray-600 mb-4">
            <span className="font-medium">Schedule</span> appears for calendar-style items;{' '}
            <span className="font-medium">Email</span> for email follow-ups. General tasks have no shortcut. Sign in and
            connect Google Calendar / Gmail; approve under Settings → Integrations → Assistant queue.
          </p>
          <ul className="space-y-4">
            {summary.action_items.map((item, idx) => (
              <li key={`${idx}-${item.task.slice(0, 80)}`} className="flex items-start gap-3">
                <input
                  type="checkbox"
                  checked={item.completed}
                  readOnly
                  className="mt-1 h-4 w-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500 shrink-0"
                />
                <div className="flex-1 min-w-0">
                  <p className={`text-gray-900 ${item.completed ? 'line-through' : ''}`}>
                    {item.task}
                  </p>
                  {(item.assignee || item.due_date) && (
                    <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-500">
                      {item.assignee && (
                        <span className="flex items-center">
                          <svg className="w-4 h-4 mr-1 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                          </svg>
                          {item.assignee}
                        </span>
                      )}
                      {item.due_date && (
                        <span className="flex items-center">
                          <svg className="w-4 h-4 mr-1 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                          </svg>
                          {item.due_date}
                        </span>
                      )}
                    </div>
                  )}
                  {normalizeActionItemType(item.type) === 'task' && (
                    <p className="mt-1 text-xs text-gray-500">General task (no calendar or email shortcut).</p>
                  )}
                </div>
                {(showScheduleForItem(item) || showEmailForItem(item)) && (
                  <div className="flex shrink-0 flex-col gap-1.5 sm:flex-row sm:items-center">
                    {showScheduleForItem(item) && (
                      <button
                        type="button"
                        disabled={!token || schedulingIdx !== null}
                        onClick={() => void handleSchedule(item, idx)}
                        className="rounded-lg border border-primary-200 bg-primary-50 px-3 py-1.5 text-xs font-medium text-primary-800 hover:bg-primary-100 disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        {schedulingIdx === idx ? 'Queuing…' : 'Schedule'}
                      </button>
                    )}
                    {showEmailForItem(item) && (
                      <button
                        type="button"
                        disabled={!token || emailingIdx !== null}
                        onClick={() => void handleEmail(item, idx)}
                        className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-800 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        {emailingIdx === idx ? 'Queuing…' : 'Email'}
                      </button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
