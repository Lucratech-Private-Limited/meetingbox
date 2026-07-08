import { create } from 'zustand'
import type { Meeting } from '../types/meeting'
import { meetingsApi } from '../api/meetings'

interface MeetingState {
  meetings: Meeting[]
  loading: boolean
  error: string | null

  fetchMeetings: () => Promise<void>
  startRecording: () => Promise<string>
  stopRecording: (sessionId?: string) => Promise<void>
  deleteMeeting: (id: string) => Promise<void>
}

export const useMeetingStore = create<MeetingState>((set, get) => ({
  meetings: [],
  loading: false,
  error: null,

  fetchMeetings: async () => {
    set({ loading: true, error: null })
    try {
      const meetings = await meetingsApi.list()
      set({ meetings, loading: false })
    } catch {
      set({ error: 'Failed to fetch meetings', loading: false })
    }
  },

  startRecording: async () => {
    const result = await meetingsApi.start()
    await get().fetchMeetings()
    return result.session_id
  },

  stopRecording: async () => {
    await meetingsApi.stop()
    await get().fetchMeetings()
  },

  deleteMeeting: async (id: string) => {
    await meetingsApi.delete(id)
    set({ meetings: get().meetings.filter((m) => m.id !== id) })
  },
}))
