import { useState } from 'react'
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
        <pre className="text-xs text-emerald-950/80 whitespace-pre-wrap">
          {JSON.stringify(artifact, null, 2)}
        </pre>
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
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-2">
        <div>
          <p className="text-xs uppercase tracking-wide text-gray-500">Recipients</p>
          <p className="text-sm text-gray-900">
            {Array.isArray(action.payload.to) ? (action.payload.to as string[]).join(', ') : ''}
          </p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-gray-500">Subject</p>
          <p className="text-sm text-gray-900">{String(action.payload.subject ?? '')}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-gray-500">Body</p>
          <p className="text-sm text-gray-700 whitespace-pre-wrap">{String(action.payload.body ?? '')}</p>
        </div>
      </div>
    )
  }

  if (action.connector_target === 'calendar') {
    return (
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-2">
        <div>
          <p className="text-xs uppercase tracking-wide text-gray-500">Event</p>
          <p className="text-sm text-gray-900">{String(action.payload.title ?? action.title)}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-gray-500">Suggested time</p>
          <p className="text-sm text-gray-900">
            {String(action.payload.suggested_date ?? '')} {String(action.payload.suggested_time ?? '')}
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
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
      <pre className="text-xs text-gray-700 whitespace-pre-wrap">{JSON.stringify(action.payload, null, 2)}</pre>
    </div>
  )
}

export default function ActionCard({ action, onChanged }: ActionCardProps) {
  const [isExecuting, setIsExecuting] = useState(false)
  const [isDismissing, setIsDismissing] = useState(false)

  const isDone = action.status === 'executed'
  const isDismissed = action.status === 'dismissed'
  const sourceSignals = Array.isArray(action.payload.source_signals)
    ? (action.payload.source_signals as string[])
    : []

  const handleExecute = async () => {
    try {
      setIsExecuting(true)
      await actionsApi.execute(action.id)
      toast.success(action.connector_target === 'internal' ? 'Action executed and saved' : 'Action executed')
      onChanged()
    } catch (error: unknown) {
      const detail =
        error && typeof error === 'object' && 'response' in error
          ? ((error as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Execution failed')
          : 'Execution failed'
      toast.error(detail)
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

  return (
    <div className={`overflow-hidden rounded-2xl border ${isDismissed ? 'border-gray-200 bg-gray-50/60 opacity-70' : 'border-gray-200 bg-white'}`}>
      <div className="border-b border-gray-100 bg-gradient-to-r from-primary-50 via-white to-emerald-50 px-6 py-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-primary-100 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-primary-800">
                {action.kind.replaceAll('_', ' ')}
              </span>
              <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">
                {connectorLabels[action.connector_target] ?? action.connector_target}
              </span>
              {isDone && (
                <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-800">
                  Executed
                </span>
              )}
              {isDismissed && (
                <span className="rounded-full bg-gray-200 px-2.5 py-1 text-xs font-medium text-gray-700">
                  Dismissed
                </span>
              )}
            </div>
            <div>
              <h3 className="text-xl font-semibold text-gray-950">{action.title}</h3>
              {action.description && <p className="mt-1 text-sm text-gray-600">{action.description}</p>}
            </div>
          </div>
          {action.confidence != null && (
            <div className="rounded-xl bg-white/80 px-3 py-2 text-right shadow-sm ring-1 ring-gray-100">
              <p className="text-[11px] uppercase tracking-wide text-gray-500">Confidence</p>
              <p className="text-sm font-semibold text-gray-900">{Math.round(action.confidence * 100)}%</p>
            </div>
          )}
        </div>
      </div>

      <div className="grid gap-6 px-6 py-5 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-4">
          <div>
            <p className="text-xs uppercase tracking-wide text-gray-500">What happens on execute</p>
            <p className="mt-1 text-sm text-gray-800">
              {action.connector_target === 'internal'
                ? 'MeetingBox will create and save an internal artifact you can review later.'
                : `MeetingBox will complete this action through ${connectorLabels[action.connector_target] ?? action.connector_target}.`}
            </p>
          </div>

          {sourceSignals.length > 0 && (
            <div>
              <p className="text-xs uppercase tracking-wide text-gray-500">Why this matters</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {sourceSignals.map((signal) => (
                  <span key={signal} className="rounded-full bg-amber-50 px-3 py-1 text-xs text-amber-800 ring-1 ring-amber-200">
                    {signal}
                  </span>
                ))}
              </div>
            </div>
          )}

          {action.error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {action.error}
            </div>
          )}
        </div>

        <div className="space-y-3">
          <p className="text-xs uppercase tracking-wide text-gray-500">
            {isDone ? 'Saved output' : 'Prepared output'}
          </p>
          <ResultPreview action={action} />
        </div>
      </div>

      {!isDone && !isDismissed && (
        <div className="flex items-center justify-between border-t border-gray-100 bg-gray-50/70 px-6 py-4">
          <button
            onClick={handleDismiss}
            disabled={isDismissing}
            className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {isDismissing ? 'Dismissing...' : 'Dismiss'}
          </button>
          <button
            onClick={handleExecute}
            disabled={isExecuting}
            className="rounded-lg bg-primary-600 px-5 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            {isExecuting ? 'Executing...' : 'Execute'}
          </button>
        </div>
      )}
    </div>
  )
}
