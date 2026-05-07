// Executive-grade chat UI for MeetingBox assistant (briefing, calendar, email, meeting memory).

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { postAssistantIntent, type AssistantIntentResponse } from '../api/assistant'

type ChatRole = 'user' | 'assistant'

interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  response?: AssistantIntentResponse
}

type ToolResult = {
  tool?: string
  error?: string
  queued?: boolean
  note?: string
  result?: Record<string, unknown>
  draft?: Record<string, unknown>
}

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
]

function newId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

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
        <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <div className="font-semibold">Approval needed</div>
          <div className="mt-1">{pending.length} pending action(s). Review in Settings → Integrations.</div>
        </div>
      )}

      {events.length > 0 && (
        <div className="rounded-2xl border border-sky-100 bg-sky-50/80 px-4 py-3">
          <div className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-sky-700">Calendar</div>
          <div className="space-y-2">
            {events.map((event, idx) => {
              const ev = event as Record<string, unknown>
              const start = (ev.start && typeof ev.start === 'object' ? ev.start : {}) as Record<string, unknown>
              return (
                <div key={String(ev.id ?? idx)} className="flex gap-3 rounded-xl bg-white/75 px-3 py-2">
                  <div className="mt-1 h-2.5 w-2.5 rounded-full bg-sky-500" />
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-slate-950">{getString(ev.summary) || 'Calendar event'}</div>
                    <div className="text-xs text-slate-500">{formatDateish(start.dateTime || start.date)}</div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {messages.length > 0 && (
        <div className="rounded-2xl border border-violet-100 bg-violet-50/80 px-4 py-3">
          <div className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-violet-700">Inbox</div>
          <div className="space-y-2">
            {messages.map((message, idx) => {
              const msg = message as Record<string, unknown>
              return (
                <div key={String(msg.id ?? idx)} className="rounded-xl bg-white/75 px-3 py-2">
                  <div className="truncate text-sm font-semibold text-slate-950">{getString(msg.subject) || '(no subject)'}</div>
                  <div className="truncate text-xs text-slate-500">{getString(msg.from) || getString(msg.sender)}</div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {meetings.length > 0 && (
        <div className="rounded-2xl border border-emerald-100 bg-emerald-50/80 px-4 py-3">
          <div className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-emerald-700">Meeting memory</div>
          <div className="space-y-2">
            {meetings.map((meeting, idx) => {
              const mtg = meeting as Record<string, unknown>
              return (
                <div key={String(mtg.id ?? idx)} className="rounded-xl bg-white/75 px-3 py-2">
                  <div className="truncate text-sm font-semibold text-slate-950">{getString(mtg.title) || '(untitled meeting)'}</div>
                  <div className="text-xs text-slate-500">{formatDateish(mtg.created_at || mtg.start_time)}</div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {errors.length > 0 && (
        <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
          <div className="font-semibold text-slate-800">Setup notes</div>
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

export default function AssistantChat() {
  const [searchParams] = useSearchParams()
  const meetingId = searchParams.get('meeting')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const greeting = useMemo(() => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 17) return 'Good afternoon'
    return 'Good evening'
  }, [])

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  const sendText = async (raw: string) => {
    const text = raw.trim()
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
        toast('Approval needed in Settings → Integrations.', { duration: 5000 })
      }
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? String((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? '')
          : ''
      toast.error(msg || 'Assistant request failed.')
      setMessages((m) => [
        ...m,
        {
          id: newId(),
          role: 'assistant',
          content: msg || 'Something went wrong. Try again.',
        },
      ])
    } finally {
      setSending(false)
      textareaRef.current?.focus()
    }
  }

  const onSend = async () => sendText(input)

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void onSend()
    }
  }

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[radial-gradient(circle_at_top_left,#dff3ff_0,transparent_32%),linear-gradient(135deg,#f8fafc_0%,#eef6ff_48%,#f7f5ff_100%)]">
      <div className="mx-auto flex h-[calc(100vh-4rem)] max-w-7xl gap-5 px-4 py-5 lg:px-8" data-tutorial="tutorial-assistant">
        <aside className="hidden w-80 shrink-0 flex-col overflow-hidden rounded-[2rem] border border-white/70 bg-white/70 p-5 shadow-xl shadow-slate-200/70 backdrop-blur-xl lg:flex">
          <div className="rounded-[1.5rem] bg-slate-950 p-5 text-white shadow-lg">
            <div className="mb-6 inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-white/10 text-xl">✦</div>
            <p className="text-sm text-slate-300">{greeting}, executive briefing is ready when you are.</p>
            <h1 className="mt-3 text-3xl font-semibold tracking-tight">MeetingBox Assistant</h1>
          </div>

          <div className="mt-5 space-y-3">
            {quickPrompts.map((item) => (
              <button
                key={item.title}
                type="button"
                onClick={() => void sendText(item.prompt)}
                disabled={sending}
                className="group w-full rounded-2xl border border-slate-200 bg-white/80 p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-sky-200 hover:shadow-lg disabled:opacity-50"
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="font-semibold text-slate-950">{item.title}</div>
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-100 text-slate-500 group-hover:bg-sky-100 group-hover:text-sky-700">→</div>
                </div>
                <div className="mt-1 text-sm leading-relaxed text-slate-500">{item.detail}</div>
              </button>
            ))}
          </div>

          {meetingId && (
            <div className="mt-auto rounded-2xl border border-sky-100 bg-sky-50 p-4 text-sm text-sky-900">
              Linked to meeting <span className="font-mono">{meetingId.slice(0, 8)}</span>.{' '}
              <Link to={`/meeting/${meetingId}`} className="font-semibold underline">Open</Link> ·{' '}
              <Link to="/assistant" className="font-semibold underline">clear</Link>
            </div>
          )}
        </aside>

        <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[2rem] border border-white/70 bg-white/80 shadow-2xl shadow-slate-200/80 backdrop-blur-xl">
          <div className="shrink-0 border-b border-slate-200/70 px-5 py-4 sm:px-7">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.25em] text-sky-600">Executive command center</div>
                <h2 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">Ask, brief, draft, remember</h2>
              </div>
              <Link
                to="/settings"
                className="inline-flex min-h-11 items-center justify-center rounded-2xl border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 shadow-sm hover:bg-slate-50"
              >
                Integrations & approvals
              </Link>
            </div>
          </div>

          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-5 sm:px-7">
            {messages.length === 0 && (
              <div className="mx-auto flex max-w-2xl flex-col items-center justify-center py-14 text-center">
                <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-[1.75rem] bg-slate-950 text-3xl text-white shadow-2xl shadow-slate-300">✦</div>
                <h3 className="text-3xl font-semibold tracking-tight text-slate-950">A calmer way to run the day.</h3>
                <p className="mt-3 max-w-xl text-base leading-7 text-slate-600">
                  Ask for a morning briefing, draft a follow-up, inspect your calendar, or recall past decisions — with approvals before anything is sent.
                </p>
                <div className="mt-8 grid w-full gap-3 sm:grid-cols-2 lg:hidden">
                  {quickPrompts.map((item) => (
                    <button
                      key={item.title}
                      type="button"
                      onClick={() => void sendText(item.prompt)}
                      disabled={sending}
                      className="min-h-24 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-left shadow-sm hover:border-sky-200 hover:shadow-md disabled:opacity-50"
                    >
                      <div className="font-semibold text-slate-950">{item.title}</div>
                      <div className="mt-1 text-sm text-slate-500">{item.detail}</div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="space-y-5">
              {messages.map((m) => (
                <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                  <div
                    className={
                      m.role === 'user'
                        ? 'max-w-[86%] rounded-[1.35rem] rounded-br-md bg-slate-950 px-5 py-3.5 text-white shadow-lg shadow-slate-200'
                        : 'max-w-[92%] rounded-[1.35rem] rounded-bl-md border border-slate-200 bg-white px-5 py-4 text-slate-950 shadow-lg shadow-slate-100'
                    }
                  >
                    <p className="whitespace-pre-wrap text-[15px] leading-7">{m.content}</p>
                    {m.response?.routed_agent_id && (
                      <div className="mt-3 inline-flex rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-500">
                        {m.response.routed_agent_id.replace(/_/g, ' ')}
                      </div>
                    )}
                    <ToolPreview response={m.response} />
                  </div>
                </div>
              ))}

              {sending && (
                <div className="flex justify-start">
                  <div className="rounded-[1.35rem] rounded-bl-md border border-slate-200 bg-white px-5 py-4 text-sm text-slate-500 shadow-lg shadow-slate-100">
                    <span className="inline-flex items-center gap-2">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-sky-500" /> Thinking through it…
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="shrink-0 border-t border-slate-200/70 bg-white/70 px-4 py-4 sm:px-7">
            <div className="flex gap-3 rounded-[1.5rem] border border-slate-200 bg-white p-2 shadow-lg shadow-slate-100 focus-within:border-sky-300 focus-within:ring-4 focus-within:ring-sky-100">
              <textarea
                ref={textareaRef}
                rows={2}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                disabled={sending}
                placeholder="Ask MeetingBox for a briefing, email draft, calendar check, or meeting memory…"
                className="min-h-[4rem] flex-1 resize-none border-0 bg-transparent px-3 py-3 text-base text-slate-950 placeholder:text-slate-400 focus:outline-none focus:ring-0 disabled:opacity-50"
              />
              <button
                type="button"
                disabled={sending || !input.trim()}
                onClick={() => void onSend()}
                className="min-h-[4rem] min-w-[6.5rem] rounded-[1.15rem] bg-sky-600 px-6 text-base font-semibold text-white shadow-lg shadow-sky-200 transition hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Send
              </button>
            </div>
            <p className="mt-2 text-xs text-slate-400">Enter to send · Shift+Enter for new line · writes always require approval</p>
          </div>
        </section>
      </div>
    </div>
  )
}
