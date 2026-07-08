<<<<<<< HEAD
// Chat-style UI for MeetingBox assistant (calendar, email, meeting memory).
=======
// Assistant chat — Figma Cricket-Champs node 991:780
// Layout: prompt sidebar + executive command center + bottom composer (Send + mic).
>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { postAssistantIntent, type AssistantIntentResponse } from '../api/assistant'
import DashboardNavShell from '../components/dashboard/DashboardNavShell'
import { useVoiceAssistant } from '../hooks/useVoiceAssistant'
import { useAuthStore } from '../store/authStore'
import { blk, pg } from '../styles/pageTypeScale'

type ChatRole = 'user' | 'assistant'

interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  response?: AssistantIntentResponse
}

<<<<<<< HEAD
=======
type ToolResult = {
  tool?: string
  error?: string
  queued?: boolean
  note?: string
  result?: Record<string, unknown>
  draft?: Record<string, unknown>
}

const SCROLL: React.CSSProperties = { scrollbarWidth: 'thin', scrollbarColor: '#006bf9 #061642' }

function iconUrl(f: string) {
  return `${import.meta.env.BASE_URL ?? '/'}icons/${f}`
}

const icoNotification     = iconUrl('ic-notification.svg')
const icoSidebarStar      = iconUrl('ic-assistant-sidebar-star.svg')
const icoHeroSpark        = iconUrl('ic-assistant-hero-spark.svg')
const icoPromptArrow      = iconUrl('ic-assistant-prompt-arrow.svg')
const icoMic              = iconUrl('ic-assistant-mic.svg')

const quickPrompts = [
  {
    title: 'Morning briefing',
    prompt: 'Give me my executive morning briefing for today.',
    detail: 'Calendar, unread mail, and recent meeting memory',
  },
  {
    title: 'Calendar focus',
    prompt: "What's on my calendar today and what should I prepare for?",
    detail: 'Turn schedule into priorities',
  },
  {
    title: 'Inbox scan',
    prompt: 'Show urgent unread emails and summarize what needs attention.',
    detail: 'Fast communication triage',
  },
  {
    title: 'Meeting recall',
    prompt: 'What follow-ups are open from recent meetings?',
    detail: 'Pulls from MeetingBox memory',
  },
] as const

>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
function newId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

<<<<<<< HEAD
=======
function asToolResults(response?: AssistantIntentResponse): ToolResult[] {
  if (!response?.tool_results || !Array.isArray(response.tool_results)) return []
  return response.tool_results.filter((x): x is ToolResult => !!x && typeof x === 'object')
}

function getString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function formatDateish(value: unknown): string {
  const raw = getString(value)
  if (!raw) return ''
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw
  return date.toLocaleString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function Ico({ src, size, alt = '', className = '' }: { src: string; size: number; alt?: string; className?: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center ${className}`}
      style={{ width: size, height: size, minWidth: size }}
    >
      <img src={src} alt={alt} className="block max-h-full max-w-full object-contain" draggable={false} />
    </span>
  )
}

function ToolPreview({ response }: { response?: AssistantIntentResponse }) {
  const tools = asToolResults(response)
  if (!tools.length) return null

  const errors = tools.filter((t) => t.error)
  const calendar = tools.find((t) => t.tool === 'calendar_list_upcoming')?.result
  const gmail = tools.find((t) => t.tool === 'gmail_list_recent')?.result
  const memory = tools.find((t) => t.tool === 'memory_search_meetings')?.result
  const pending = response?.pending_actions ?? []

  const events = Array.isArray(calendar?.events) ? calendar.events.slice(0, 4) : []
  const messages = Array.isArray(gmail?.messages) ? gmail.messages.slice(0, 4) : []
  const meetings = Array.isArray(memory?.meetings) ? memory.meetings.slice(0, 4) : []

  if (!errors.length && !events.length && !messages.length && !meetings.length && !pending.length) {
    return null
  }

  return (
    <div className="mt-4 grid gap-3 text-left">
      {pending.length > 0 && (
        <div className="rounded-[14px] border border-[#f5a623]/40 bg-[#f5a623]/10 px-4 py-3 text-sm text-[#f5d89a]">
          <div className="font-semibold text-white">Approval needed</div>
          <div className="mt-1 text-[#b6baf2]">
            {pending.length} pending action(s). Review in Settings → Integrations.
          </div>
        </div>
      )}

      {events.length > 0 && (
        <div className="rounded-[14px] border border-[#3f8cff]/30 bg-[#006bf9]/10 px-4 py-3">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[#006bf9]">Calendar</div>
          <div className="space-y-2">
            {events.map((event, idx) => {
              const ev = event as Record<string, unknown>
              const start = (ev.start && typeof ev.start === 'object' ? ev.start : {}) as Record<string, unknown>
              return (
                <div key={String(ev.id ?? idx)} className="flex gap-3 rounded-[12px] border border-[#3f4253]/60 bg-[#010b26]/80 px-3 py-2">
                  <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-[#006bf9]" />
                  <div className="min-w-0">
                    <div className="truncate text-[14px] font-semibold text-white">{getString(ev.summary) || 'Calendar event'}</div>
                    <div className="text-[12px] text-[#b6baf2]">{formatDateish(start.dateTime || start.date)}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {messages.length > 0 && (
        <div className="rounded-[14px] border border-[#7c6cf0]/35 bg-[#4a3f9a]/15 px-4 py-3">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[#b6baf2]">Inbox</div>
          <div className="space-y-2">
            {messages.map((message, idx) => {
              const msg = message as Record<string, unknown>
              return (
                <div key={String(msg.id ?? idx)} className="rounded-[12px] border border-[#3f4253]/60 bg-[#010b26]/80 px-3 py-2">
                  <div className="truncate text-[14px] font-semibold text-white">{getString(msg.subject) || '(no subject)'}</div>
                  <div className="truncate text-[12px] text-[#b6baf2]">{getString(msg.from) || getString(msg.sender)}</div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {meetings.length > 0 && (
        <div className="rounded-[14px] border border-[#19d385]/35 bg-[#19d385]/10 px-4 py-3">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-[#19d385]">Meeting memory</div>
          <div className="space-y-2">
            {meetings.map((meeting, idx) => {
              const mtg = meeting as Record<string, unknown>
              return (
                <div key={String(mtg.id ?? idx)} className="rounded-[12px] border border-[#3f4253]/60 bg-[#010b26]/80 px-3 py-2">
                  <div className="truncate text-[14px] font-semibold text-white">{getString(mtg.title) || '(untitled meeting)'}</div>
                  <div className="text-[12px] text-[#b6baf2]">{formatDateish(mtg.created_at || mtg.start_time)}</div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {errors.length > 0 && (
        <div className="rounded-[14px] border border-[#3f4253] bg-white/[0.04] px-4 py-3 text-sm text-[#b6baf2]">
          <div className="font-semibold text-white">Setup notes</div>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {errors.slice(0, 3).map((err, idx) => (
              <li key={`${err.tool}-${idx}`}>{err.error}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function PromptCard({
  title,
  detail,
  onClick,
  disabled,
}: {
  title: string
  detail: string
  onClick: () => void
  disabled: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`relative w-full ${blk.assistantPrompt} text-left transition hover:border-[#3f8cff]/90 disabled:opacity-50`}
    >
      <p className={`pr-12 ${pg.promptTitle}`}>{title}</p>
      <p className={`mt-1.5 max-w-[204px] ${pg.promptDetail}`}>{detail}</p>
      <span className="absolute right-4 top-4 flex h-10 w-10 items-center justify-center rounded-[8px] border border-[#3f4253] bg-[#052557]">
        <Ico src={icoPromptArrow} size={14} className="rotate-90" />
      </span>
    </button>
  )
}

function EmptyHero() {
  return (
    <div className="flex h-full min-h-[357px] flex-col items-center justify-center px-5 py-8 text-center">
      <div className="mb-8 w-full max-w-2xl text-left">
        <p className={pg.labelAccent}>Executive command center</p>
        <p className={`mt-1 ${pg.heroTitle}`}>Ask, brief, draft, remember</p>
      </div>
      <div className="mb-8 flex items-center justify-center">
        <Ico src={icoHeroSpark} size={99} className="max-h-[119px] max-w-[119px]" />
      </div>
      <h2 className={pg.title}>
        A calmer way to run the day.
      </h2>
      <div className={`mt-4 max-w-3xl ${pg.heroSub} leading-relaxed`}>
        <p>Ask for a morning briefing, draft a follow-up, inspect your calendar, or recall</p>
        <p>past decision—with approvals before anything is sent.</p>
      </div>
    </div>
  )
}

>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
export default function AssistantChat() {
  const [searchParams] = useSearchParams()
  const user = useAuthStore((s) => s.user)
  const meetingId = searchParams.get('meeting')
  const prefilledQuery = searchParams.get('q') ?? ''
  const autoVoice = searchParams.get('voice') === '1'

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const autoSentRef = useRef(false)

  const { startListening, isListening, transcript, state: voiceState } = useVoiceAssistant()

<<<<<<< HEAD
=======
  const greeting = useMemo(() => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 17) return 'Good afternoon'
    return 'Good evening'
  }, [])

  const name = user?.display_name ?? user?.username ?? ''

>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

<<<<<<< HEAD
  const onSend = async () => {
    const text = input.trim()
=======
  useEffect(() => {
    if (prefilledQuery && !autoSentRef.current) {
      autoSentRef.current = true
      void sendText(prefilledQuery)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (autoVoice) {
      startListening()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (voiceState === 'processing' && transcript) {
      void sendText(transcript)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceState, transcript])

  const sendText = async (raw: string) => {
    const text = raw.trim()
>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
    if (!text || sending) return
    setInput('')
    const userMsg: ChatMessage = { id: newId(), role: 'user', content: text }
    setMessages((m) => [...m, userMsg])
    setSending(true)
    try {
      const res = await postAssistantIntent(text, meetingId || null)
      setMessages((m) => [
        ...m,
        {
          id: newId(),
          role: 'assistant',
          content: res.assistant_message || 'Done.',
          response: res,
        },
      ])
      if (res.pending_actions && res.pending_actions.length > 0) {
        toast('Open Settings → Integrations → Assistant queue to approve edits.', { duration: 5000 })
      }
    } catch (err: unknown) {
      const ax = err as { response?: { data?: { detail?: string } } }
      const detail =
        ax?.response?.data?.detail != null ? String(ax.response.data.detail) : ''
      const network = !ax?.response
      if (!network) {
        toast.error(detail || 'Assistant request failed.')
      }
      setMessages((m) => [
        ...m,
        {
          id: newId(),
          role: 'assistant',
          content: network
            ? 'Cannot reach the MeetingBox API. Rebuild the SPA with VITE_API_URL pointing at your server (same host/port as /health), open HTTPS/port on the server, then reload.'
            : detail || 'Something went wrong. Try again.',
        },
      ])
    } finally {
      setSending(false)
      textareaRef.current?.focus()
    }
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void onSend()
    }
  }

  const promptClick = (prompt: string) => () => { void sendText(prompt) }

  return (
<<<<<<< HEAD
    <div
      className="flex flex-col h-[calc(100vh-4rem)] max-w-4xl mx-auto px-4 py-6"
      data-tutorial="tutorial-assistant"
    >
      <div className="mb-4 shrink-0">
        <h1 className="text-2xl font-semibold text-gray-900">Assistant</h1>
        <p className="text-sm text-gray-600 mt-1">
          Ask about your calendar, Gmail, or past meetings stored on this MeetingBox. For drafts that need
          approval, use{' '}
          <Link to="/settings" className="text-primary-600 hover:underline">
            Settings → Integrations → Assistant queue
          </Link>
          .
        </p>
        {meetingId && (
          <p className="text-xs text-primary-700 mt-2">
            Context: this chat is linked to meeting <span className="font-mono">{meetingId}</span> (
            <Link to={`/meeting/${meetingId}`} className="underline">
              open meeting
            </Link>
            ) ·{' '}
            <Link to="/assistant" className="underline">
              clear context
            </Link>
          </p>
        )}
      </div>

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto rounded-xl border border-gray-200 bg-gray-50/80 px-3 py-4 space-y-4 min-h-0"
      >
        {messages.length === 0 && (
          <div className="text-center text-gray-500 text-sm py-12 px-4">
            <p className="mb-2">Try:</p>
            <ul className="text-left max-w-md mx-auto space-y-1 list-disc list-inside">
              <li>What&apos;s on my calendar this week?</li>
              <li>Show recent emails / unread in my inbox.</li>
              <li>What did we discuss about the budget in past meetings?</li>
              <li>Search meetings from last month.</li>
            </ul>
          </div>
        )}

        {messages.map((m) => (
          <div
            key={m.id}
            className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={
                m.role === 'user'
                  ? 'max-w-[85%] rounded-2xl rounded-br-md bg-primary-600 text-white px-4 py-2.5 shadow-sm'
                  : 'max-w-[90%] rounded-2xl rounded-bl-md bg-white border border-gray-200 text-gray-900 px-4 py-3 shadow-sm'
              }
            >
              <p className="text-sm whitespace-pre-wrap leading-relaxed">{m.content}</p>
              {m.response && (
                <div className="mt-3 pt-3 border-t border-gray-100 text-xs text-gray-500 space-y-1">
                  {m.response.routed_agent_id && (
                    <p>
                      <span className="font-medium text-gray-600">Agent:</span>{' '}
                      {m.response.routed_agent_id}
                    </p>
                  )}
                  {m.response.pending_actions && m.response.pending_actions.length > 0 && (
                    <p className="text-amber-700">
                      {m.response.pending_actions.length} pending action(s) — approve in Settings.
                    </p>
                  )}
                  {m.response.tool_results && m.response.tool_results.length > 0 && (
                    <details className="mt-1">
                      <summary className="cursor-pointer text-primary-600 hover:underline">
                        Tool details
                      </summary>
                      <pre className="mt-2 p-2 bg-gray-50 rounded text-[10px] overflow-x-auto max-h-48 overflow-y-auto">
                        {JSON.stringify(m.response.tool_results, null, 2)}
                      </pre>
                    </details>
                  )}
=======
    <DashboardNavShell>
      <div
        className={`flex h-[100dvh] max-h-[100dvh] flex-col overflow-hidden text-white px-5 pt-4 pb-4`}
        data-tutorial="tutorial-assistant"
      >
        <div className={`${blk.chromeRow} shrink-0`}>
          <button type="button" aria-label="Notifications" className={blk.avatarBtn}>
            <Ico src={icoNotification} size={blk.notifIcon} />
          </button>
          <div className={`${blk.avatar} shrink-0 overflow-hidden rounded-full border border-white/10 bg-white/10`}>
            {user?.avatar_url ? (
              <img src={user.avatar_url} alt="" className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-[11px] font-bold text-white/80">
                {(name || 'U').slice(0, 2).toUpperCase()}
              </div>
            )}
          </div>
        </div>

        <div className={`flex min-h-0 flex-1 flex-col overflow-hidden ${blk.gridGap} lg:flex-row`}>
          <aside className={`flex w-full shrink-0 flex-col gap-4 overflow-hidden ${blk.assistantSidebar} lg:max-h-full`}>
            <div className={`relative ${blk.assistantIntro}`}>
              <Ico src={icoSidebarStar} size={52} className="mb-4" />
              <p className={pg.heroSub}>
                {greeting}, executive briefing
                <br />
                is ready when you are.
              </p>
              <h1 className={`mt-4 ${pg.title}`}>
                MeetingBox
                <br />
                Assistant
              </h1>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto pr-1" style={SCROLL}>
              {quickPrompts.map((item) => (
                <PromptCard
                  key={item.title}
                  title={item.title}
                  detail={item.detail}
                  onClick={promptClick(item.prompt)}
                  disabled={sending}
                />
              ))}
            </div>

            {meetingId ? (
              <div className="rounded-[14px] border border-[#3f8cff]/30 bg-[#006bf9]/10 p-4 text-[13px] text-[#b6baf2]">
                Linked to meeting <span className="font-mono text-white">{meetingId.slice(0, 8)}</span>.{' '}
                <Link to={`/meeting/${meetingId}`} className="font-semibold text-[#006bf9] hover:underline">Open</Link>
                {' · '}
                <Link to="/assistant" className="font-semibold text-[#006bf9] hover:underline">Clear</Link>
              </div>
            ) : null}
          </aside>

          {/* Main chat + composer — Figma 991:852 + 991:860 */}
          <div className={`flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden ${blk.gridGap}`}>
            <section className={`flex min-h-0 flex-1 flex-col overflow-hidden ${blk.assistantMain}`}>
              <div ref={scrollRef} className="flex-1 overflow-y-auto" style={SCROLL}>
                {messages.length === 0 ? (
                  <EmptyHero />
                ) : (
                  <div className="space-y-4 px-4 py-5 sm:px-6">
                    {messages.map((m) => (
                      <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                        <div
                          className={
                            m.role === 'user'
                              ? 'max-w-[88%] rounded-[16px] rounded-br-[6px] border border-[#3f8cff]/40 bg-gradient-to-b from-[#006bf9] to-[#0048a8] px-4 py-3 text-white shadow-lg shadow-black/25'
                              : 'max-w-[92%] rounded-[16px] rounded-bl-[6px] border border-[#3f4253] bg-[#010b26]/90 px-4 py-3.5 text-white shadow-lg shadow-black/20'
                          }
                        >
                          <p className="whitespace-pre-wrap text-[14px] sm:text-[15px] font-medium leading-7">{m.content}</p>
                          {m.response?.routed_agent_id && (
                            <div className="mt-2 inline-flex rounded-full border border-[#3f4253] bg-[#000a26] px-3 py-1 text-[11px] font-semibold text-[#b6baf2]">
                              {m.response.routed_agent_id.replace(/_/g, ' ')}
                            </div>
                          )}
                          <ToolPreview response={m.response} />
                        </div>
                      </div>
                    ))}

                    {sending && (
                      <div className="flex justify-start">
                        <div className="rounded-[16px] rounded-bl-[6px] border border-[#3f4253] bg-[#010b26]/90 px-4 py-3 text-[14px] text-[#b6baf2]">
                          <span className="inline-flex items-center gap-2">
                            <span className="h-2 w-2 animate-pulse rounded-full bg-[#006bf9]" />
                            Thinking through it…
                          </span>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </section>

            <div className={`shrink-0 overflow-hidden ${blk.composer}`}>
              {isListening && (
                <div className="flex items-center gap-2 border-b border-[#3f4253]/60 px-4 py-2">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-[#006bf9]" />
                  <span className="text-[13px] font-semibold text-[#006bf9]">Listening… speak now</span>
>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
                </div>
              )}
              <div className="flex items-center gap-2 px-3 py-3 sm:gap-4 sm:px-6 sm:py-4">
                <textarea
                  ref={textareaRef}
                  rows={1}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onKeyDown}
                  disabled={sending || isListening}
                  placeholder={
                    isListening
                      ? 'Listening…'
                      : 'Ask MeetingBox for a briefing, email draft, calendar check, or meeting memory...'
                  }
                  className={`min-h-[44px] flex-1 resize-none border-0 bg-transparent ${pg.body} text-white placeholder:text-[#9f9f9f] focus:outline-none disabled:opacity-50`}
                />
                <button
                  type="button"
                  onClick={isListening ? undefined : startListening}
                  disabled={sending}
                  aria-label={isListening ? 'Listening' : 'Voice input'}
                  className="flex shrink-0 items-center justify-center disabled:opacity-50"
                  style={{ width: 44, height: 44 }}
                >
                  <Ico src={icoMic} size={44} alt="Voice" />
                </button>
                <button
                  type="button"
                  onClick={() => void onSend()}
                  disabled={sending || !input.trim()}
                  className={`shrink-0 rounded-[14px] border-2 border-[#3f8cff] bg-gradient-to-b from-[#0059dc] to-[#013da7] px-5 py-2.5 ${pg.filterTab} text-white transition hover:from-[#006bf9] hover:to-[#0048a8] disabled:cursor-not-allowed disabled:opacity-40 sm:min-w-[85px]`}
                >
                  Send
                </button>
              </div>
            </div>
          </div>
<<<<<<< HEAD
        ))}

        {sending && (
          <div className="flex justify-start">
            <div className="rounded-2xl rounded-bl-md bg-white border border-gray-200 px-4 py-2.5 text-sm text-gray-500">
              …
            </div>
          </div>
        )}
      </div>

      <div className="mt-4 shrink-0 flex gap-2 items-end">
        <textarea
          ref={textareaRef}
          rows={3}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={sending}
          placeholder="Message MeetingBox…"
          className="flex-1 resize-none rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 disabled:opacity-50"
        />
        <button
          type="button"
          disabled={sending || !input.trim()}
          onClick={() => void onSend()}
          className="shrink-0 h-[4.5rem] px-5 rounded-xl bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Send
        </button>
      </div>
      <p className="text-xs text-gray-400 mt-2">Enter to send · Shift+Enter for new line</p>
    </div>
=======
        </div>
      </div>
    </DashboardNavShell>
>>>>>>> 2b79a526e149f70ac1781d4f2a16da7fe38695db
  )
}
