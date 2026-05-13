export type TutorialPlacement = 'top' | 'bottom' | 'left' | 'right' | 'center'

export interface TutorialStep {
  id: string
  /** Route to open before highlighting (see InteractiveTutorial for dashboard matching). */
  path: string
  /** Matches `[data-tutorial="…"]`; omit for centered-only step. */
  targetSelector?: string
  title: string
  body: string
  placement?: TutorialPlacement
}

export const tutorialSteps: TutorialStep[] = [
  {
    id: 'welcome',
    path: '/dashboard',
    title: 'Welcome to MeetingBox',
    body:
      'This short tour walks through the web app: meetings, live capture, assistant, settings, and system tools. Use Next and Back, or Skip anytime.',
    placement: 'center',
  },
  {
    id: 'nav',
    path: '/dashboard',
    targetSelector: 'nav-main',
    title: 'Main navigation',
    body:
      'Use Dashboard for your meetings, Assistant for calendar/email/meeting Q&A, Settings for device and integrations, and System for health and storage.',
    placement: 'bottom',
  },
  {
    id: 'recording',
    path: '/dashboard',
    targetSelector: 'tutorial-recording',
    title: 'Start and stop recording',
    body:
      'Start Recording sends you to the live view. Stop ends capture; Processing means the backend is finishing up. Reset clears a stuck processing state.',
    placement: 'bottom',
  },
  {
    id: 'stats',
    path: '/dashboard',
    targetSelector: 'tutorial-stats',
    title: 'At-a-glance stats',
    body: 'This week’s meeting count, total recorded hours, and pending AI actions across meetings.',
    placement: 'bottom',
  },
  {
    id: 'filters',
    path: '/dashboard',
    targetSelector: 'tutorial-filters',
    title: 'Search and time range',
    body: 'Filter the list by Today, Week, or Month, and search by meeting title.',
    placement: 'bottom',
  },
  {
    id: 'meeting-list',
    path: '/dashboard',
    targetSelector: 'tutorial-meeting-list',
    title: 'Your meetings',
    body: 'Open a card for full detail. From the dashboard you can delete meetings with the trash control on each card.',
    placement: 'top',
  },
  {
    id: 'meeting-detail',
    path: '/dashboard',
    title: 'Inside a meeting',
    body:
      'After you open a meeting: rename with the pencil, jump to Assistant with context, export PDF or TXT, run Summarize when you have a transcript, and use tabs for Summary, Transcript, AI Actions (approve/dismiss/execute), and Recording audio playback. Delete removes the meeting permanently.',
    placement: 'center',
  },
  {
    id: 'live',
    path: '/live',
    targetSelector: 'tutorial-live-panel',
    title: 'Live recording',
    body:
      'While recording you’ll see elapsed time, live captions and transcript lines over the WebSocket, speaker hints, and Stop to finish and return to the dashboard.',
    placement: 'bottom',
  },
  {
    id: 'assistant',
    path: '/assistant',
    targetSelector: 'tutorial-assistant',
    title: 'Assistant',
    body:
      'Ask about calendar, Gmail, or past meetings. Open from a meeting (Assistant link) to pass meeting context. Pending drafts may need approval under Settings → Integrations.',
    placement: 'top',
  },
  {
    id: 'settings',
    path: '/settings',
    targetSelector: 'tutorial-settings-tabs',
    title: 'Settings',
    body:
      'General: device name, timezone, display. Devices: paired hardware. Integrations: Google Calendar, Gmail, connect/disconnect. Privacy: auto-record, auto-summarize, retention, privacy mode.',
    placement: 'bottom',
  },
  {
    id: 'system',
    path: '/system',
    targetSelector: 'tutorial-system',
    title: 'System status',
    body:
      'CPU, memory, and disk refresh automatically. When disk is high, Free up Space deletes the oldest meetings in bulk; you can also delete individually from the dashboard.',
    placement: 'bottom',
  },
  {
    id: 'done',
    path: '/dashboard',
    title: 'You’re set',
    body: 'Start the tour again anytime from Tour in the top bar. Happy recording.',
    placement: 'center',
  },
]

export const tutorialStepCount = tutorialSteps.length
