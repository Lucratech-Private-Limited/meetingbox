import { useCallback, useState } from 'react'
import toast from 'react-hot-toast'
import { actionsApi } from '../../api/actions'
import type { AgenticAction, ActionArtifact } from '../../types/action'

interface ActionCardProps {
  action: AgenticAction
  onChanged: () => void
}

const connectorLabels: Record<string, string> = {
  internal: 'Saved in MeetingBox',
  gmail: 'Gmail',
  calendar: 'Google Calendar',
  slack: 'Slack',
  notion: 'Notion',
}

/** Default IANA zone for calendar actions when payload/browser zone is missing (India / IST). */
const DEFAULT_CALENDAR_TZ = 'Asia/Kolkata'

const COMMON_TIMEZONES = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Asia/Dubai',
  'Asia/Kolkata',
  'Asia/Tokyo',
  'Australia/Sydney',
]

function localDatePlusDays(days: number): string {
  const x = new Date()
  x.setDate(x.getDate() + days)
  const y = x.getFullYear()
  const m = String(x.getMonth() + 1).padStart(2, '0')
  const d = String(x.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function parseEmailsFromText(s: string): string[] {
  return s
    .split(/[\s,;]+/g)
    .map((p) => p.trim())
    .filter(Boolean)
}

function ArtifactPreview({ artifact }: { artifact: ActionArtifact }) {
  const sections = Array.isArray(artifact.sections) ? artifact.sections : []

  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50/70 p-4 space-y-3">
      {artifact.headline && <h4 className="text-base font-semibold text-emerald-900">{artifact.headline}</h4>}
      {artifact.summary && <p className="text-sm text-emerald-900/80 whitespace-pre-wrap">{artifact.summary}</p>}
      {sections.length > 0 && (
        <div className="space-y-3">
          {sections.map((section) => (
            <div key={section.title}>
              <h5 className="text-sm font-semibold text-emerald-900">{section.title}</h5>
              <ul className="mt-1 space-y-1">
                {section.bullets.map((bullet) => (
                  <li key={bullet} className="text-sm text-emerald-950/80">
                    - {bullet}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {sections.length === 0 && (
        <pre className="text-xs text-emerald-950/80 whitespace-pre-wrap">{JSON.stringify(artifact, null, 2)}</pre>
      )}
    </div>
  )
}

function ResultPreview({ action }: { action: AgenticAction }) {
  if (action.artifact) {
    return <ArtifactPreview artifact={action.artifact} />
  }

  if (action.connector_target === 'gmail') {
    return (
      <div className="rounded-lg border border-app-border bg-app-page p-4 space-y-2">
        <div>
          <p className="text-xs uppercase tracking-wide text-app-ink-subtle">Recipients</p>
          <p className="text-sm text-app-ink">
            {Array.isArray(action.payload.to) ? (action.payload.to as string[]).join(', ') : ''}
          </p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-app-ink-subtle">Subject</p>
          <p className="text-sm text-app-ink">{String(action.payload.subject ?? '')}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-app-ink-subtle">Body</p>
          <p className="text-sm text-app-ink-muted whitespace-pre-wrap">{String(action.payload.body ?? '')}</p>
        </div>
      </div>
    )
  }

  if (action.connector_target === 'calendar') {
    return (
      <div className="rounded-lg border border-app-border bg-app-page p-4 space-y-2">
        <div>
          <p className="text-xs uppercase tracking-wide text-app-ink-subtle">Event</p>
          <p className="text-sm text-app-ink">{String(action.payload.title ?? action.title)}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-app-ink-subtle">Suggested time</p>
          <p className="text-sm text-app-ink">
            {String(action.payload.suggested_date ?? '')} {String(action.payload.suggested_time ?? '')}{' '}
            {action.payload.timezone ? `(${String(action.payload.timezone)})` : ''}
          </p>
        </div>
        {typeof action.payload.calendar_link === 'string' && action.payload.calendar_link && (
          <a
            href={action.payload.calendar_link}
            target="_blank"
            rel="noreferrer"
            className="text-sm font-medium text-primary-700 hover:text-primary-800"
          >
            Open calendar event
          </a>
        )}
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-app-border bg-app-page p-4">
      <pre className="text-xs text-app-ink-muted whitespace-pre-wrap">{JSON.stringify(action.payload, null, 2)}</pre>
    </div>
  )
}

export default function ActionCard({ action, onChanged }: ActionCardProps) {
  const [showReview, setShowReview] = useState(false)
  const [isExecuting, setIsExecuting] = useState(false)
  const [isDismissing, setIsDismissing] = useState(false)
  const [isIgnoring, setIsIgnoring] = useState(false)

  const [calTitle, setCalTitle] = useState('')
  const [calDescription, setCalDescription] = useState('')
  const [calDate, setCalDate] = useState('')
  const [calTime, setCalTime] = useState('10:00')
  const [calDuration, setCalDuration] = useState(30)
  const [calTimezone, setCalTimezone] = useState(DEFAULT_CALENDAR_TZ)
  const [calAttendees, setCalAttendees] = useState('')

  const [mailTo, setMailTo] = useState('')
  const [mailCc, setMailCc] = useState('')
  const [mailSubject, setMailSubject] = useState('')
  const [mailBody, setMailBody] = useState('')

  const isDone = action.status === 'executed'
  const isDismissed = action.status === 'dismissed'
  const sourceSignals = Array.isArray(action.payload.source_signals)
    ? (action.payload.source_signals as string[])
    : []

  const openReview = useCallback(() => {
    const p = action.payload || {}
    const defaultTz =
      (typeof p.timezone === 'string' && p.timezone) ||
      Intl.DateTimeFormat().resolvedOptions().timeZone ||
      DEFAULT_CALENDAR_TZ
    if (action.connector_target === 'calendar') {
      const tmatch = String(p.suggested_time ?? '10:00').match(/^(\d{1,2}):(\d{2})/)
      const hhmm = tmatch ? `${tmatch[1].padStart(2, '0')}:${tmatch[2]}` : '10:00'
      setCalTitle(String(p.title ?? action.title ?? ''))
      setCalDescription(String(p.description ?? ''))
      setCalDate(String(p.suggested_date ?? '').slice(0, 10) || localDatePlusDays(1))
      setCalTime(hhmm)
      setCalDuration(Number(p.duration_minutes) > 0 ? Number(p.duration_minutes) : 30)
      setCalTimezone(String(defaultTz))
      setCalAttendees(
        Array.isArray(p.attendees) ? (p.attendees as string[]).join(', ') : String(p.attendees ?? ''),
      )
    } else if (action.connector_target === 'gmail') {
      const to = p.to
      setMailTo(Array.isArray(to) ? (to as string[]).join(', ') : String(to ?? ''))
      const cc = p.cc
      setMailCc(Array.isArray(cc) ? (cc as string[]).join(', ') : String(cc ?? ''))
      setMailSubject(String(p.subject ?? action.title ?? ''))
      setMailBody(String(p.body ?? ''))
    }
    setShowReview(true)
  }, [action])

  const buildExecutePayload = useCallback((): Record<string, unknown> => {
    if (action.connector_target === 'calendar') {
      return {
        title: calTitle.trim(),
        description: calDescription.trim(),
        suggested_date: calDate.trim(),
        suggested_time: calTime.trim(),
        duration_minutes: calDuration,
        timezone: calTimezone.trim() || DEFAULT_CALENDAR_TZ,
        attendees: parseEmailsFromText(calAttendees),
      }
    }
    if (action.connector_target === 'gmail') {
      return {
        to: parseEmailsFromText(mailTo),
        cc: parseEmailsFromText(mailCc),
        subject: mailSubject.trim(),
        body: mailBody,
      }
    }
    return {}
  }, [
    action.connector_target,
    calTitle,
    calDescription,
    calDate,
    calTime,
    calDuration,
    calTimezone,
    calAttendees,
    mailTo,
    mailCc,
    mailSubject,
    mailBody,
  ])

  const handleConfirmExecute = async () => {
    if (action.connector_target === 'gmail') {
      const to = parseEmailsFromText(mailTo)
      if (to.length === 0) {
        toast.error('Add at least one recipient.')
        return
      }
      if (!mailSubject.trim()) {
        toast.error('Subject is required.')
        return
      }
    }
    if (action.connector_target === 'calendar') {
      if (!calDate.trim()) {
        toast.error('Event date is required.')
        return
      }
      if (!calTitle.trim()) {
        toast.error('Event title is required.')
        return
      }
    }

    try {
      setIsExecuting(true)
      const payload = buildExecutePayload()
      await actionsApi.execute(action.id, payload, {
        repeatExecution: action.status === 'executed',
      })
      toast.success(action.connector_target === 'gmail' ? 'Email sent' : 'Calendar event created')
      setShowReview(false)
      onChanged()
    } catch (error: unknown) {
      const detail =
        error && typeof error === 'object' && 'response' in error
          ? String((error as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? '')
          : ''
      toast.error(detail || 'Execution failed')
    } finally {
      setIsExecuting(false)
    }
  }

  const handleDismiss = async () => {
    try {
      setIsDismissing(true)
      await actionsApi.dismiss(action.id)
      toast.success('Action dismissed')
      onChanged()
    } catch {
      toast.error('Failed to dismiss action')
    } finally {
      setIsDismissing(false)
    }
  }

  const handleIgnore = async () => {
    try {
      setIsIgnoring(true)
      await actionsApi.ignore(action.id)
      toast.success('Suggestion hidden (not counted as pending)')
      onChanged()
    } catch {
      toast.error('Failed to ignore action')
    } finally {
      setIsIgnoring(false)
    }
  }

  const showConnectorReview = action.connector_target === 'calendar' || action.connector_target === 'gmail'

  return (
    <div
      className={`overflow-hidden rounded-2xl border ${isDismissed ? 'border-app-border bg-app-page/85 opacity-70' : 'border-app-border bg-app-surface'}`}
    >
      <div className="border-b border-app-border bg-gradient-to-r from-primary-900/50 via-app-surface to-emerald-900/35 px-6 py-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-primary-900/55 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-primary-200">
                {action.kind.replaceAll('_', ' ')}
              </span>
              <span className="rounded-full bg-app-raised px-2.5 py-1 text-xs font-medium text-app-ink-muted">
                {connectorLabels[action.connector_target] ?? action.connector_target}
              </span>
              {isDone && (
                <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-800">
                  Executed
                </span>
              )}
              {isDismissed && (
                <span className="rounded-full bg-app-border px-2.5 py-1 text-xs font-medium text-app-ink-muted">
                  Dismissed
                </span>
              )}
            </div>
            <div>
              <h3 className="text-xl font-semibold text-app-ink">{action.title}</h3>
              {action.description && <p className="mt-1 text-sm text-app-ink-muted">{action.description}</p>}
            </div>
          </div>
          {action.confidence != null && (
            <div className="rounded-xl bg-app-surface/88 px-3 py-2 text-right shadow-sm ring-1 ring-app-border">
              <p className="text-[11px] uppercase tracking-wide text-app-ink-subtle">Confidence</p>
              <p className="text-sm font-semibold text-app-ink">{Math.round(action.confidence * 100)}%</p>
            </div>
          )}
        </div>
      </div>

      <div className="grid gap-6 px-6 py-5 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-4">
          <div>
            <p className="text-xs uppercase tracking-wide text-app-ink-subtle">What happens on execute</p>
            <p className="mt-1 text-sm text-app-ink">
              {action.connector_target === 'gmail' &&
                'You review recipients and message text, then MeetingBox sends via your connected Gmail.'}
              {action.connector_target === 'calendar' &&
                'You review date, time, timezone, and guests, then MeetingBox creates the event on your primary Google calendar.'}
            </p>
          </div>

          {sourceSignals.length > 0 && (
            <div>
              <p className="text-xs uppercase tracking-wide text-app-ink-subtle">Why this matters</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {sourceSignals.map((signal) => (
                  <span
                    key={signal}
                    className="rounded-full bg-amber-50 px-3 py-1 text-xs text-amber-800 ring-1 ring-amber-200"
                  >
                    {signal}
                  </span>
                ))}
              </div>
            </div>
          )}

          {action.error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{action.error}</div>
          )}
        </div>

        <div className="space-y-3">
          <p className="text-xs uppercase tracking-wide text-app-ink-subtle">
            {isDone ? 'Saved output' : 'Prepared output'}
          </p>
          <ResultPreview action={action} />
        </div>
      </div>

      {!isDone && !isDismissed && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-app-border bg-app-page/70 px-6 py-4">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleDismiss}
              disabled={isDismissing}
              className="rounded-lg border border-app-border-light bg-app-surface px-4 py-2 text-sm font-medium text-app-ink-muted hover:bg-app-page disabled:opacity-50"
            >
              {isDismissing ? 'Dismissing...' : 'Dismiss'}
            </button>
            <button
              type="button"
              onClick={() => void handleIgnore()}
              disabled={isIgnoring}
              className="rounded-lg border border-app-border-light bg-app-surface px-4 py-2 text-sm font-medium text-app-ink-muted hover:bg-app-page disabled:opacity-50"
            >
              {isIgnoring ? 'Hiding...' : 'Ignore'}
            </button>
          </div>
          {showConnectorReview ? (
            <button
              type="button"
              onClick={openReview}
              className="rounded-lg bg-primary-600 px-5 py-2 text-sm font-medium text-white hover:bg-primary-700"
            >
              Review &amp; execute
            </button>
          ) : (
            <p className="text-sm text-app-ink-subtle">This action type is not executable from the dashboard.</p>
          )}
        </div>
      )}

      {isDone && !isDismissed && showConnectorReview && (
        <div className="flex justify-end border-t border-app-border bg-app-page/40 px-6 py-3">
          <button
            type="button"
            onClick={openReview}
            className="rounded-lg border border-app-border-light bg-app-surface px-4 py-2 text-sm font-medium text-app-ink-muted hover:bg-app-page"
          >
            Review &amp; run again
          </button>
        </div>
      )}

      {showReview && showConnectorReview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true">
          <button
            type="button"
            className="absolute inset-0 bg-black/40"
            aria-label="Close"
            onClick={() => !isExecuting && setShowReview(false)}
          />
          <div className="relative max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-app-border bg-app-surface p-6 shadow-xl">
            <h4 className="text-lg font-semibold text-app-ink">
              {action.connector_target === 'calendar' ? 'Review calendar event' : 'Review email'}
            </h4>
            <p className="mt-1 text-sm text-app-ink-muted">
              Confirm details before sending. You can edit every field; empty optional fields use AI or server defaults
              only when required fields are filled.
            </p>

            {action.connector_target === 'calendar' && (
              <div className="mt-5 space-y-4">
                <label className="block">
                  <span className="text-xs font-medium text-app-ink-muted">Title</span>
                  <input
                    type="text"
                    value={calTitle}
                    onChange={(e) => setCalTitle(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                  />
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-xs font-medium text-app-ink-muted">Date</span>
                    <input
                      type="date"
                      value={calDate}
                      onChange={(e) => setCalDate(e.target.value)}
                      className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs font-medium text-app-ink-muted">Time</span>
                    <input
                      type="time"
                      value={calTime}
                      onChange={(e) => setCalTime(e.target.value)}
                      className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                    />
                  </label>
                </div>
                <label className="block">
                  <span className="text-xs font-medium text-app-ink-muted">Timezone (IANA)</span>
                  <input
                    type="text"
                    list="tz-options"
                    value={calTimezone}
                    onChange={(e) => setCalTimezone(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                  />
                  <datalist id="tz-options">
                    {COMMON_TIMEZONES.map((tz) => (
                      <option key={tz} value={tz} />
                    ))}
                  </datalist>
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-app-ink-muted">Duration (minutes)</span>
                  <input
                    type="number"
                    min={5}
                    max={1440}
                    value={calDuration}
                    onChange={(e) => setCalDuration(Number(e.target.value) || 30)}
                    className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                  />
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-app-ink-muted">Attendees (emails, comma or space separated)</span>
                  <textarea
                    value={calAttendees}
                    onChange={(e) => setCalAttendees(e.target.value)}
                    rows={2}
                    className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                  />
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-app-ink-muted">Description</span>
                  <textarea
                    value={calDescription}
                    onChange={(e) => setCalDescription(e.target.value)}
                    rows={4}
                    className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                  />
                </label>
              </div>
            )}

            {action.connector_target === 'gmail' && (
              <div className="mt-5 space-y-4">
                <label className="block">
                  <span className="text-xs font-medium text-app-ink-muted">To</span>
                  <textarea
                    value={mailTo}
                    onChange={(e) => setMailTo(e.target.value)}
                    rows={2}
                    placeholder="a@example.com, b@example.com"
                    className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                  />
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-app-ink-muted">Cc (optional)</span>
                  <textarea
                    value={mailCc}
                    onChange={(e) => setMailCc(e.target.value)}
                    rows={2}
                    className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                  />
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-app-ink-muted">Subject</span>
                  <input
                    type="text"
                    value={mailSubject}
                    onChange={(e) => setMailSubject(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                  />
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-app-ink-muted">Body</span>
                  <textarea
                    value={mailBody}
                    onChange={(e) => setMailBody(e.target.value)}
                    rows={8}
                    className="mt-1 w-full rounded-lg border border-app-border-light px-3 py-2 text-sm"
                  />
                </label>
              </div>
            )}

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                disabled={isExecuting}
                onClick={() => setShowReview(false)}
                className="rounded-lg border border-app-border-light bg-app-surface px-4 py-2 text-sm font-medium text-app-ink-muted hover:bg-app-page disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isExecuting}
                onClick={() => void handleConfirmExecute()}
                className={
                  action.status === 'executed'
                    ? 'rounded-lg border border-app-border-light bg-app-surface px-5 py-2 text-sm font-medium text-app-ink hover:bg-app-page disabled:opacity-50'
                    : 'rounded-lg bg-primary-600 px-5 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50'
                }
              >
                {isExecuting ? 'Working...' : action.connector_target === 'gmail' ? 'Send email' : 'Create event'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
