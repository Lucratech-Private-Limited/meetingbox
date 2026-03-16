import { create } from 'zustand'
import type { AgenticAction } from '../types/action'
import { actionsApi } from '../api/actions'

interface ActionState {
  actions: AgenticAction[]
  loading: boolean
  error: string | null

  fetchActions: (meetingId: string) => Promise<void>
  dismissAction: (actionId: string) => Promise<void>
}

export const useActionStore = create<ActionState>((set, get) => ({
  actions: [],
  loading: false,
  error: null,

  fetchActions: async (meetingId: string) => {
    set({ loading: true, error: null })
    try {
      const actions = await actionsApi.list(meetingId)
      set({ actions, loading: false })
    } catch {
      set({ error: 'Failed to fetch actions', loading: false })
    }
  },

  dismissAction: async (actionId: string) => {
    await actionsApi.dismiss(actionId)
    set({ actions: get().actions.map((a) => a.id === actionId ? { ...a, status: 'dismissed' } : a) })
  },
}))
