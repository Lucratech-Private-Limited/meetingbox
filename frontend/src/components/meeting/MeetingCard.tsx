// Card showing a single meeting in the dashboard list, with hover delete button

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { formatDistanceToNow } from 'date-fns'
import type { Meeting } from '../../types/meeting'
import { MEETING_STATUSES } from '../../utils/constants'
import { parseUTC } from '../../utils/formatters'
import Modal from '../ui/Modal'
import Button from '../ui/Button'

interface MeetingCardProps {
  meeting: Meeting
  onDelete?: (id: string) => Promise<void>
}

export default function MeetingCard({ meeting, onDelete }: MeetingCardProps) {
  const [showConfirm, setShowConfirm] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)

  const statusInfo = MEETING_STATUSES[meeting.status as keyof typeof MEETING_STATUSES] ?? {
    color: 'bg-gray-100 text-gray-800',
    text: meeting.status,
  }

  const handleDeleteClick = (e: React.MouseEvent) => {
    // Prevent navigating to meeting detail when clicking delete
    e.preventDefault()
    e.stopPropagation()
    setShowConfirm(true)
  }

  const handleConfirmDelete = async () => {
    if (!onDelete) return
    try {
      setIsDeleting(true)
      await onDelete(meeting.id)
    } finally {
      setIsDeleting(false)
      setShowConfirm(false)
    }
  }

  return (
    <>
      <Link
        to={`/meeting/${meeting.id}`}
        className="group block bg-white rounded-lg border border-gray-200 hover:border-primary-500 hover:shadow-md transition-all relative"
      >
        <div className="p-6">
          {/* Title and status */}
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-lg font-semibold text-gray-900 truncate flex-1 mr-2">
              {meeting.title}
            </h3>
            <div className="flex items-center gap-2">
              <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusInfo.color}`}>
                {statusInfo.text}
              </span>

              {/* Delete button — visible on hover */}
              {onDelete && (
                <button
                  onClick={handleDeleteClick}
                  title="Delete meeting"
                  className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded-lg text-gray-400 hover:text-red-600 hover:bg-red-50"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              )}
            </div>
          </div>

          {/* Meta info */}
          <div className="space-y-2 text-sm text-gray-600">
            <div className="flex items-center">
              <svg className="w-4 h-4 mr-2 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              {formatDistanceToNow(parseUTC(meeting.start_time), { addSuffix: true })}
            </div>

            {meeting.duration != null && (
              <div className="flex items-center">
                <svg className="w-4 h-4 mr-2 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
                {Math.floor(meeting.duration / 60)} minutes
              </div>
            )}

            {(meeting.pending_actions ?? 0) > 0 && (
              <div className="flex items-center text-yellow-600">
                <svg className="w-4 h-4 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                {meeting.pending_actions} pending action{meeting.pending_actions! > 1 ? 's' : ''}
              </div>
            )}
          </div>
        </div>
      </Link>

      {/* Delete confirmation modal */}
      <Modal
        isOpen={showConfirm}
        onClose={() => setShowConfirm(false)}
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
            onClick={() => setShowConfirm(false)}
            disabled={isDeleting}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={handleConfirmDelete}
            isLoading={isDeleting}
          >
            Delete Meeting
          </Button>
        </div>
      </Modal>
    </>
  )
}
