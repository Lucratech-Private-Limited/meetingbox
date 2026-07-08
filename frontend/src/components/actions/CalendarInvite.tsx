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
        <label className="block text-sm font-medium text-gray-700 mb-1">Meeting Title</label>
        <div className="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-900">
          {draft.title}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Attendees</label>
        <div className="flex flex-wrap gap-2">
          {draft.attendees.map((attendee, index) => (
            <span
              key={index}
              className="inline-flex items-center px-3 py-1 rounded-full text-sm bg-gray-100 text-gray-700"
            >
              {attendee}
            </span>
          ))}
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Duration</label>
        <div className="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-900">
          {draft.duration} minutes
        </div>
      </div>

      {draft.suggested_times?.length > 0 && (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Suggested Times
          </label>
          <div className="space-y-2">
            {draft.suggested_times.map((time, index) => (
              <div
                key={index}
                className={`px-4 py-3 border rounded-lg ${
                  time.available ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-gray-900">
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
          <label className="block text-sm font-medium text-gray-700 mb-1">Agenda</label>
          <div className="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm text-gray-700 whitespace-pre-wrap">
            {draft.description}
          </div>
        </div>
      )}

      {draft.context && (
        <div className="pt-4 border-t border-gray-200">
          <details>
            <summary className="text-sm font-medium text-gray-700 cursor-pointer">
              Meeting context
            </summary>
            <div className="mt-2 text-sm text-gray-600 whitespace-pre-wrap">
              {draft.context}
            </div>
          </details>
        </div>
      )}
    </div>
  )
}
