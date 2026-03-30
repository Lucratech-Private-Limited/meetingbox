import { useCallback, useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import {
  approveAssistantPending,
  listAssistantPending,
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

interface AssistantPendingSectionProps {
  /** Increment when returning from OAuth so the queue refetches. */
  refreshKey?: number
}

export default function AssistantPendingSection({ refreshKey = 0 }: AssistantPendingSectionProps) {
  const token = useAuthStore((s) => s.token)
  const [items, setItems] = useState<AssistantPendingItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)

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

  if (!token) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-1">Assistant queue</h2>
        <p className="text-sm text-gray-600">
          Sign in to approve calendar or email actions queued from meeting summaries (Schedule button).
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
          {items.map((item) => (
            <li
              key={item.id}
              className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-lg border border-gray-100 bg-gray-50 px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium uppercase tracking-wide text-primary-700">
                  {item.tool_name.replace(/_/g, ' ')}
                </p>
                <p className="text-sm text-gray-900 mt-0.5 break-words">{summarizePayload(item)}</p>
                <p className="text-xs text-gray-500 mt-1">
                  {new Date(item.created_at).toLocaleString()} · {item.agent_id}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  isLoading={busyId === item.id}
                  disabled={busyId !== null}
                  onClick={() => void onApprove(item.id)}
                >
                  Approve
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={busyId !== null}
                  onClick={() => void onReject(item.id)}
                >
                  Reject
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
