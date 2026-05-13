import { create } from 'zustand'
import { tutorialStepCount } from '../components/tutorial/tutorialSteps'

export const TUTORIAL_STORAGE_KEY = 'meetingbox_tutorial_completed'

type TutorialState = {
  active: boolean
  index: number
  start: () => void
  stop: () => void
  next: () => void
  back: () => void
  skip: () => void
}

export const useTutorialStore = create<TutorialState>((set, get) => ({
  active: false,
  index: 0,
  start: () => set({ active: true, index: 0 }),
  stop: () => set({ active: false, index: 0 }),
  next: () => {
    const { index } = get()
    const next = index + 1
    if (next >= tutorialStepCount) {
      try {
        localStorage.setItem(TUTORIAL_STORAGE_KEY, 'true')
      } catch {
        /* ignore */
      }
      set({ active: false, index: 0 })
    } else {
      set({ index: next })
    }
  },
  back: () => set((s) => ({ index: Math.max(0, s.index - 1) })),
  skip: () => {
    try {
      localStorage.setItem(TUTORIAL_STORAGE_KEY, 'true')
    } catch {
      /* ignore */
    }
    set({ active: false, index: 0 })
  },
}))
