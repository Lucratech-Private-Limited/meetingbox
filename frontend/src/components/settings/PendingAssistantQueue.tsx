import { useCallback, useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import {
  approveAssistantPending,
  listAssistantPending,
  patchAssistantPending,
  rejectAssistantPending,
  type AssistantPendingItem,
} from '../../api/assistant'

export default function PendingAssistantQueue() {
  const [items, setItems] = useState<AssistantPendingItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [editJson, setEditJson] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    try {
      const pending = await listAssistantPending()
      setItems(pending)
      const init: Record<string, string> = {}
      for (const p of pending) {
        init[p.id] = JSON.stringify(p.payload ?? {}, null, 2)
      }
      setEditJson(init)
    } catch {
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const onApprove = async (id: string) => {
    setBusy(id)
    try {
      await approveAssistantPending(id)
      toast.success('Action approved')
      await load()
    } catch {
      toast.error('Approve failed')
    } finally {
      setBusy(null)
    }
  }

  const onSavePayload = async (id: string) => {
    const raw = editJson[id]
    if (raw === undefined) return
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(raw) as Record<string, unknown>
    } catch {
      toast.error('Payload must be valid JSON')
      return
    }
    setBusy(id)
    try {
      await patchAssistantPending(id, parsed)
      toast.success('Draft updated')
      await load()
    } catch {
      toast.error('Save failed')
    } finally {
      setBusy(null)
    }
  }

  const onReject = async (id: string) => {
    setBusy(id)
    try {
      await rejectAssistantPending(id)
      toast.success('Action dismissed')
      await load()
    } catch {
      toast.error('Reject failed')
    } finally {
      setBusy(null)
    }
  }

  if (loading) {
    return null
  }

  if (items.length === 0) {
    return (
      <div className="bg-app-surface rounded-lg border border-app-border p-6">
        <h3 className="text-lg font-semibold text-app-ink mb-1">Assistant pending actions</h3>
        <p className="text-sm text-app-ink-subtle">No drafts or calendar sends waiting for approval.</p>
      </div>
    )
  }

  return (
    <div className="bg-app-surface rounded-lg border border-app-border p-6 space-y-3">
      <h3 className="text-lg font-semibold text-app-ink">Assistant pending actions</h3>
      <p className="text-sm text-app-ink-subtle">
        Review and approve emails or calendar events the assistant queued for you.
      </p>
      <ul className="space-y-3">
        {items.map((p) => (
          <li
            key={p.id}
            className="rounded-lg border border-app-border bg-app-raised/40 p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3"
          >
            <div>
              <div className="text-sm font-medium text-app-ink">
                {p.tool_name.replace(/_/g, ' ')}
              </div>
              <div className="text-xs text-app-ink-subtle mt-1 font-mono break-all">{p.id}</div>
              {'to' in p.payload && typeof p.payload.to === 'string' && (
                <div className="text-xs text-app-ink-muted mt-1">To: {p.payload.to}</div>
              )}
              {'subject' in p.payload && typeof p.payload.subject === 'string' && (
                <div className="text-xs text-app-ink-muted">Subject: {p.payload.subject}</div>
              )}
              <details className="mt-2">
                <summary className="cursor-pointer text-xs text-primary-400">Edit JSON payload</summary>
                <textarea
                  className="mt-2 w-full min-h-[120px] rounded border border-app-border bg-app-surface p-2 text-xs font-mono text-app-ink"
                  value={editJson[p.id] ?? JSON.stringify(p.payload ?? {}, null, 2)}
                  onChange={(e) => setEditJson((prev) => ({ ...prev, [p.id]: e.target.value }))}
                />
                <button
                  type="button"
                  className="mt-1 text-xs text-primary-500 hover:underline"
                  disabled={busy !== null}
                  onClick={() => onSavePayload(p.id)}
                >
                  Save edits
                </button>
              </details>
            </div>
            <div className="flex gap-2 shrink-0">
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => onReject(p.id)}
                className="px-3 py-1.5 text-sm rounded-lg border border-app-border text-app-ink hover:bg-app-surface-soft disabled:opacity-50"
              >
                Dismiss
              </button>
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => onApprove(p.id)}
                className="px-3 py-1.5 text-sm rounded-lg bg-primary-600 text-white hover:bg-primary-700 disabled:opacity-50"
              >
                {busy === p.id ? 'Working…' : 'Approve'}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
