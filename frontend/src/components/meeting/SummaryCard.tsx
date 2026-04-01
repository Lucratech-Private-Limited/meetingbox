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

/** When type is missing, treat as both calendar + email for legacy summaries. */
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

function itemHasFollowUpShortcut(item: ActionItem): boolean {
  return showScheduleForItem(item) || showEmailForItem(item)
}

export default function SummaryCard({ summary, meetingId }: SummaryCardProps) {
  const token = useAuthStore((s) => s.token)
  const [planningFollowUps, setPlanningFollowUps] = useState(false)

  const parsed = useMemo(
    () => parseSummaryReport(summary?.summary ?? ''),
    [summary],
  )

  const queueCalendarIntent = async (item: ActionItem) => {
    const parts = [
      'Schedule a calendar event for this meeting action item:',
      `"${item.task}".`,
      item.due_date ? `Time or deadline mentioned: ${item.due_date}.` : '',
      item.assignee ? `Assignee: ${item.assignee}.` : '',
    ].filter(Boolean)
    await postAssistantIntent(parts.join(' '), meetingId!)
  }

  const queueEmailDraftIntent = async (item: ActionItem) => {
    const parts = [
      'Create a Gmail draft only — do not send the email. Action item:',
      `"${item.task}".`,
      item.assignee ? `Related person or assignee: ${item.assignee}.` : '',
      item.due_date ? `Mentioned deadline: ${item.due_date}.` : '',
    ].filter(Boolean)
    await postAssistantIntent(parts.join(' '), meetingId!)
  }

  const handlePlanFollowUps = async () => {
    if (!token) {
      toast.error('Sign in to queue calendar and email follow-ups.')
      return
    }
    if (!meetingId) {
      toast.error('Missing meeting id.')
      return
    }
    const items = summary?.action_items ?? []
    const toPlan = items.filter(itemHasFollowUpShortcut)
    if (toPlan.length === 0) {
      toast.error('No calendar or email action items to queue.')
      return
    }
    setPlanningFollowUps(true)
    try {
      let nCal = 0
      let nMail = 0
      for (const item of items) {
        try {
          if (showScheduleForItem(item)) {
            await queueCalendarIntent(item)
            nCal += 1
          }
          if (showEmailForItem(item)) {
            await queueEmailDraftIntent(item)
            nMail += 1
          }
        } catch (err: unknown) {
          const msg =
            err && typeof err === 'object' && 'response' in err
              ? String((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? '')
              : ''
          toast.error(msg || `Could not queue: ${item.task.slice(0, 60)}…`)
          throw err
        }
      }
      const bits = []
      if (nCal) bits.push(`${nCal} calendar request(s)`)
      if (nMail) bits.push(`${nMail} email draft request(s)`)
      toast.success(`Queued ${bits.join(' and ')} with the assistant.`)
      toast('Review and approve under Settings → Integrations → Assistant queue.', { duration: 6500 })
    } catch {
      /* toast already shown */
    } finally {
      setPlanningFollowUps(false)
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
  const actionableItems = (summary.action_items ?? []).filter(itemHasFollowUpShortcut)

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
          <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0 flex-1">
              <h3 className="text-lg font-semibold text-gray-900">Action Items</h3>
              <p className="mt-1 text-sm text-gray-600">
                Calendar invites and email drafts are sent to the assistant for approval. Connect Gmail and Calendar under
                Settings → Integrations.
              </p>
            </div>
            {actionableItems.length > 0 && (
              <button
                type="button"
                disabled={!token || planningFollowUps}
                onClick={() => void handlePlanFollowUps()}
                className="shrink-0 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-40 disabled:cursor-not-allowed sm:mt-0"
              >
                {planningFollowUps ? 'Queuing…' : 'Plan follow-ups'}
              </button>
            )}
          </div>
          <ul className="space-y-4">
            {summary.action_items.map((item, idx) => (
              <li key={`${idx}-${item.task.slice(0, 80)}`} className="border-b border-gray-100 pb-4 last:border-0 last:pb-0">
                <div className="min-w-0">
                  <p className={`text-gray-900 ${item.completed ? 'line-through text-gray-500' : ''}`}>{item.task}</p>
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
                  {normalizeActionItemType(item.type) === 'calendar_invite' && (
                    <p className="mt-1 text-xs text-gray-500">Included when you use Plan follow-ups → calendar request.</p>
                  )}
                  {normalizeActionItemType(item.type) === 'email_draft' && (
                    <p className="mt-1 text-xs text-gray-500">Included when you use Plan follow-ups → Gmail draft (not sent until you send from Gmail).</p>
                  )}
                  {normalizeActionItemType(item.type) === 'task' && (
                    <p className="mt-1 text-xs text-gray-500">General task — not queued with Plan follow-ups.</p>
                  )}
                  {normalizeActionItemType(item.type) === undefined && itemHasFollowUpShortcut(item) && (
                    <p className="mt-1 text-xs text-gray-500">Legacy item: both calendar and email requests may be queued.</p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
