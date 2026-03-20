// Hook wrapping the action store for convenient use in components

import { useEffect } from 'react'
import { useActionStore } from '../store/actionStore'

export function useActions(meetingId: string | undefined) {
  const { actions, loading, error, fetchActions, dismissAction } =
    useActionStore()

  useEffect(() => {
    if (meetingId) {
      fetchActions(meetingId)
    }
  }, [meetingId, fetchActions])

  return { actions, loading, error, dismissAction }
}
