// Chat-style UI for MeetingBox assistant (calendar, email, meeting memory).

import { useCallback, useEffect, useRef, useState } from 'react'
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

function newId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

export default function AssistantChat() {
  const [searchParams] = useSearchParams()
  const meetingId = searchParams.get('meeting')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  const onSend = async () => {
    const text = input.trim()
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

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void onSend()
    }
  }

  return (
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
                </div>
              )}
            </div>
          </div>
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
  )
}
