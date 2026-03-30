import { useCallback, useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import {
  approveAssistantPending,
  listAssistantPending,
  patchAssistantPending,
  rejectAssistantPending,
  type AssistantPendingItem,
} from '../../api/assistant'
import { useAuthStore } from '../../store/authStore'
import Button from '../ui/Button'
import LoadingSpinner from '../ui/LoadingSpinner'

function summarizePayload(item: AssistantPendingItem): string {
  const p = item.payload || {}
  if (item.tool_name === 'calendar_create_event') {
    const title = typeof p.title === 'string' ? p.title : 'Calendar event'
    const when = typeof p.start_time === 'string' ? p.start_time : typeof p.start_iso === 'string' ? p.start_iso : ''
    return when ? `${title} — ${when}` : title
  }
  if (item.tool_name === 'gmail_send_email') {
    const to = typeof p.to === 'string' ? p.to : ''
    const sub = typeof p.subject === 'string' ? p.subject : '(no subject)'
    return to ? `To: ${to} — ${sub}` : sub
  }
  return JSON.stringify(p).slice(0, 120) + (JSON.stringify(p).length > 120 ? '…' : '')
}

function listToComma(v: unknown): string {
  if (Array.isArray(v)) return v.map((x) => String(x).trim()).filter(Boolean).join(', ')
  if (typeof v === 'string') return v
  return ''
}

type GmailDraftForm = {
  to: string
  subject: string
  body: string
  cc: string
  bcc: string
  html_body: string
  thread_id: string
}

function payloadToGmailForm(p: Record<string, unknown>): GmailDraftForm {
  const tid = p.thread_id ?? p.threadId
  const toRaw = p.to
  const to =
    Array.isArray(toRaw) ? listToComma(toRaw) : typeof toRaw === 'string' ? toRaw : ''
  return {
    to,
    subject: typeof p.subject === 'string' ? p.subject : '',
    body: typeof p.body === 'string' ? p.body : '',
    cc: listToComma(p.cc),
    bcc: listToComma(p.bcc),
    html_body: typeof p.html_body === 'string' ? p.html_body : '',
    thread_id: typeof tid === 'string' ? tid : tid != null ? String(tid) : '',
  }
}

function formToGmailPayload(
  base: Record<string, unknown>,
  form: GmailDraftForm
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base }
  out.to = form.to.trim()
  out.subject = form.subject
  out.body = form.body
  const cc = form.cc.trim()
  const bcc = form.bcc.trim()
  const html = form.html_body.trim()
  const tid = form.thread_id.trim()
  if (cc) out.cc = cc
  else delete out.cc
  if (bcc) out.bcc = bcc
  else delete out.bcc
  if (html) out.html_body = html
  else delete out.html_body
  if (tid) {
    out.thread_id = tid
  } else {
    delete out.thread_id
    delete out.threadId
  }
  return out
}

interface AssistantPendingSectionProps {
  /** Increment when returning from OAuth so the queue refetches. */
  refreshKey?: number
}

export default function AssistantPendingSection({ refreshKey = 0 }: AssistantPendingSectionProps) {
  const token = useAuthStore((s) => s.token)
  const [items, setItems] = useState<AssistantPendingItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [gmailForm, setGmailForm] = useState<GmailDraftForm | null>(null)

  const load = useCallback(async () => {
    if (!token) {
      setItems([])
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const pending = await listAssistantPending()
      setItems(pending)
    } catch {
      setItems([])
      toast.error('Could not load assistant pending actions.')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    void load()
  }, [load, refreshKey])

  const onApprove = async (id: string) => {
    setBusyId(id)
    try {
      await approveAssistantPending(id)
      toast.success('Approved and executed.')
      await load()
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? String((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? '')
          : ''
      toast.error(msg || 'Approve failed.')
    } finally {
      setBusyId(null)
    }
  }

  const onReject = async (id: string) => {
    setBusyId(id)
    try {
      await rejectAssistantPending(id)
      toast.success('Rejected.')
      await load()
    } catch {
      toast.error('Reject failed.')
    } finally {
      setBusyId(null)
    }
  }

  const startEditGmail = (item: AssistantPendingItem) => {
    setEditingId(item.id)
    setGmailForm(payloadToGmailForm(item.payload || {}))
  }

  const cancelEditGmail = () => {
    setEditingId(null)
    setGmailForm(null)
  }

  const onSaveGmailDraft = async (item: AssistantPendingItem) => {
    if (!gmailForm) return
    if (!gmailForm.to.trim()) {
      toast.error('To is required.')
      return
    }
    setBusyId(item.id)
    try {
      const payload = formToGmailPayload(item.payload || {}, gmailForm)
      await patchAssistantPending(item.id, payload)
      toast.success('Draft updated.')
      cancelEditGmail()
      await load()
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? String((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? '')
          : ''
      toast.error(msg || 'Could not save draft.')
    } finally {
      setBusyId(null)
    }
  }

  if (!token) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-1">Assistant queue</h2>
        <p className="text-sm text-gray-600">
          Sign in to approve calendar or email actions queued from meeting summaries (Schedule / Email).
        </p>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6 flex justify-center">
        <LoadingSpinner />
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6">
      <h2 className="text-lg font-semibold text-gray-900 mb-1">Assistant queue</h2>
      <p className="text-sm text-gray-600 mb-4">
        Draft calendar events and emails from the assistant stay here until you approve. Connect Google
        Calendar / Gmail in the cards below first.
      </p>

      {items.length === 0 ? (
        <p className="text-sm text-gray-500 py-2">No pending items.</p>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => {
            const isGmail = item.tool_name === 'gmail_send_email'
            const isEditing = editingId === item.id && isGmail && gmailForm

            return (
              <li
                key={item.id}
                className="flex flex-col gap-3 rounded-lg border border-gray-100 bg-gray-50 px-4 py-3"
              >
                <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium uppercase tracking-wide text-primary-700">
                      {item.tool_name.replace(/_/g, ' ')}
                    </p>
                    <p className="text-sm text-gray-900 mt-0.5 break-words">{summarizePayload(item)}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      {new Date(item.created_at).toLocaleString()} · {item.agent_id}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    {isGmail && !isEditing && (
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        disabled={busyId !== null}
                        onClick={() => startEditGmail(item)}
                      >
                        Edit
                      </Button>
                    )}
                    <Button
                      type="button"
                      variant="primary"
                      size="sm"
                      isLoading={busyId === item.id && !isEditing}
                      disabled={busyId !== null || Boolean(isEditing)}
                      onClick={() => void onApprove(item.id)}
                    >
                      Approve
                    </Button>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      disabled={busyId !== null || Boolean(isEditing)}
                      onClick={() => void onReject(item.id)}
                    >
                      Reject
                    </Button>
                  </div>
                </div>

                {isEditing && gmailForm && (
                  <div className="rounded-md border border-gray-200 bg-white p-3 space-y-2 text-sm">
                    <label className="block">
                      <span className="text-xs font-medium text-gray-600">To</span>
                      <input
                        className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                        value={gmailForm.to}
                        onChange={(e) => setGmailForm({ ...gmailForm, to: e.target.value })}
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs font-medium text-gray-600">CC</span>
                      <input
                        className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                        value={gmailForm.cc}
                        onChange={(e) => setGmailForm({ ...gmailForm, cc: e.target.value })}
                        placeholder="comma-separated"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs font-medium text-gray-600">BCC</span>
                      <input
                        className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                        value={gmailForm.bcc}
                        onChange={(e) => setGmailForm({ ...gmailForm, bcc: e.target.value })}
                        placeholder="comma-separated"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs font-medium text-gray-600">Subject</span>
                      <input
                        className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                        value={gmailForm.subject}
                        onChange={(e) => setGmailForm({ ...gmailForm, subject: e.target.value })}
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs font-medium text-gray-600">Body (plain)</span>
                      <textarea
                        className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1.5 text-sm min-h-[72px]"
                        value={gmailForm.body}
                        onChange={(e) => setGmailForm({ ...gmailForm, body: e.target.value })}
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs font-medium text-gray-600">HTML body (optional)</span>
                      <textarea
                        className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1.5 text-sm min-h-[56px] font-mono text-xs"
                        value={gmailForm.html_body}
                        onChange={(e) => setGmailForm({ ...gmailForm, html_body: e.target.value })}
                        placeholder="If set, Gmail may prefer this over plain body"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs font-medium text-gray-600">Thread ID (optional, reply in thread)</span>
                      <input
                        className="mt-0.5 w-full rounded border border-gray-300 px-2 py-1.5 text-sm font-mono text-xs"
                        value={gmailForm.thread_id}
                        onChange={(e) => setGmailForm({ ...gmailForm, thread_id: e.target.value })}
                      />
                    </label>
                    <div className="flex gap-2 pt-1">
                      <Button
                        type="button"
                        variant="primary"
                        size="sm"
                        isLoading={busyId === item.id}
                        disabled={busyId !== null}
                        onClick={() => void onSaveGmailDraft(item)}
                      >
                        Save draft
                      </Button>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        disabled={busyId !== null}
                        onClick={cancelEditGmail}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
