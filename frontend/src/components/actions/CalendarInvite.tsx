// Displays an AI-drafted calendar invite for review before approval

import { format } from 'date-fns'

interface CalendarInviteProps {
  draft: {
    title: string
    attendees: string[]
    suggested_times: Array<{
      start: string
      end: string
      available: boolean
    }>
    duration: number
    description: string
    context?: string
  }
}

export default function CalendarInvite({ draft }: CalendarInviteProps) {
  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-app-ink-muted mb-1">Meeting Title</label>
        <div className="px-3 py-2 bg-app-page border border-app-border rounded-lg text-sm text-app-ink">
          {draft.title}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-app-ink-muted mb-1">Attendees</label>
        <div className="flex flex-wrap gap-2">
          {draft.attendees.map((attendee, index) => (
            <span
              key={index}
              className="inline-flex items-center px-3 py-1 rounded-full text-sm bg-app-raised text-app-ink-muted"
            >
              {attendee}
            </span>
          ))}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-app-ink-muted mb-1">Duration</label>
        <div className="px-3 py-2 bg-app-page border border-app-border rounded-lg text-sm text-app-ink">
          {draft.duration} minutes
        </div>
      </div>

      {draft.suggested_times?.length > 0 && (
        <div>
          <label className="block text-sm font-medium text-app-ink-muted mb-2">
            Suggested Times
          </label>
          <div className="space-y-2">
            {draft.suggested_times.map((time, index) => (
              <div
                key={index}
                className={`px-4 py-3 border rounded-lg ${
                  time.available ? 'bg-green-50 border-green-200' : 'bg-app-page border-app-border'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-app-ink">
                    {format(new Date(time.start), 'EEE, MMM d')} at{' '}
                    {format(new Date(time.start), 'h:mm a')}
                  </span>
                  {time.available && (
                    <span className="text-xs text-green-700 font-medium">All available</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {draft.description && (
        <div>
          <label className="block text-sm font-medium text-app-ink-muted mb-1">Agenda</label>
          <div className="px-3 py-2 bg-app-page border border-app-border rounded-lg text-sm text-app-ink-muted whitespace-pre-wrap">
            {draft.description}
          </div>
        </div>
      )}

      {draft.context && (
        <div className="pt-4 border-t border-app-border">
          <details>
            <summary className="text-sm font-medium text-app-ink-muted cursor-pointer">
              Meeting context
            </summary>
            <div className="mt-2 text-sm text-app-ink-muted whitespace-pre-wrap">
              {draft.context}
            </div>
          </details>
        </div>
      )}
    </div>
  )
}
