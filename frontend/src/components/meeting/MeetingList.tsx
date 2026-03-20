// Renders a grid of MeetingCard components

import type { Meeting } from '../../types/meeting'
import MeetingCard from './MeetingCard'

interface MeetingListProps {
  meetings: Meeting[]
  onStartRecording?: () => void
  onDeleteMeeting?: (id: string) => Promise<void>
}

export default function MeetingList({ meetings, onStartRecording, onDeleteMeeting }: MeetingListProps) {
  if (meetings.length === 0) {
    return (
      <div className="text-center py-12">
        <svg className="mx-auto h-12 w-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
        </svg>
        <h3 className="mt-2 text-sm font-medium text-gray-900">No meetings found</h3>
        <p className="mt-1 text-sm text-gray-500">
          Get started by recording your first meeting
        </p>
        {onStartRecording && (
          <div className="mt-6">
            <button
              onClick={onStartRecording}
              className="inline-flex items-center px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700"
            >
              Start Recording
            </button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {meetings.map((meeting) => (
        <MeetingCard
          key={meeting.id}
          meeting={meeting}
          onDelete={onDeleteMeeting}
        />
      ))}
    </div>
  )
}
