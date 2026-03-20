// Hook wrapping the meeting store for convenient use in components

import { useEffect } from 'react'
import { useMeetingStore } from '../store/meetingStore'

export function useMeetings() {
  const {
    meetings,
    loading,
    error,
    fetchMeetings,
    startRecording,
    stopRecording,
    deleteMeeting,
  } = useMeetingStore()

  // Fetch meetings on mount
  useEffect(() => {
    fetchMeetings()
  }, [fetchMeetings])

  return {
    meetings,
    loading,
    error,
    fetchMeetings,
    startRecording,
    stopRecording,
    deleteMeeting,
  }
}
