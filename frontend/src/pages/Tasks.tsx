// Tasks / commitments — Figma Cricket-Champs node 991:367
// Filter bar (All · Today · Upcoming · Unplanned) + Add task + sectioned task cards.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { blk, pg } from '../styles/pageTypeScale'
import { Link } from 'react-router-dom'
import LoadingSpinner from '../components/ui/LoadingSpinner'
import DashboardNavShell from '../components/dashboard/DashboardNavShell'
import { useVisiblePolling } from '../hooks/useVisiblePolling'
import { useAuthStore } from '../store/authStore'
import { commitmentsApi, type CommitmentRow } from '../api/commitments'

const SCROLL: React.CSSProperties = { scrollbarWidth: 'thin', scrollbarColor: '#006bf9 #061642' }

type TaskTab = 'all' | 'due_today' | 'upcoming' | 'unplanned'
type TaskBucket = 'due_today' | 'upcoming' | 'unplanned'

type TaskCounts = {
  all: number
  dueToday: number
  upcoming: number
  unplanned: number
}

const SECTION_META: Record<TaskBucket, { label: string; color: string; dot: string }> = {
  due_today: { label: 'TODAY', color: '#006bf9', dot: 'ic-task-dot-today.svg' },
  upcoming: { label: 'UPCOMING', color: '#8d5dec', dot: 'ic-task-dot-upcoming.svg' },
  unplanned: { label: 'UNPLANNED', color: '#f18903', dot: 'ic-task-dot-unplanned.svg' },
}

function iconUrl(f: string) {
  return `${import.meta.env.BASE_URL ?? '/'}icons/${f}`
}

const icoNotification = iconUrl('ic-notification.svg')
const icoTaskAdd      = iconUrl('ic-task-add.svg')
const icoTaskKebab    = iconUrl('ic-task-kebab.svg')
const icoTaskBack     = iconUrl('ic-email-back.svg')
const icoDotToday     = iconUrl('ic-task-dot-today.svg')
const icoDotUpcoming  = iconUrl('ic-task-dot-upcoming.svg')
const icoDotUnplanned = iconUrl('ic-task-dot-unplanned.svg')
const icoSrcCalendar  = iconUrl('ic-task-source-calendar.svg')
const icoSrcEmail     = iconUrl('ic-task-source-email.svg')
const icoSrcProfile   = iconUrl('ic-task-source-profile.svg')

const FILTER_TABS: { id: TaskTab; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'due_today', label: 'Today' },
  { id: 'upcoming', label: 'Upcoming' },
  { id: 'unplanned', label: 'Unplanned' },
]

function computeCounts(items: CommitmentRow[]): TaskCounts {
  const today = new Date()
  today.setHours(23, 59, 59, 999)
  let dueToday = 0, upcoming = 0, unplanned = 0
  for (const c of items) {
    if (c.status === 'completed' || c.status === 'cancelled') continue
    const due = new Date(c.due_at ?? c.remind_at ?? '')
    if ((!c.due_at && !c.remind_at) || isNaN(due.getTime())) {
      unplanned++
      continue
    }
    if (due <= today) dueToday++
    else upcoming++
  }
  return { all: dueToday + upcoming + unplanned, dueToday, upcoming, unplanned }
}

function categorize(row: CommitmentRow): TaskBucket | null {
  if (row.status === 'cancelled' || row.status === 'completed') return null
  const due = new Date(row.due_at ?? row.remind_at ?? '')
  if ((!row.due_at && !row.remind_at) || isNaN(due.getTime())) return 'unplanned'
  const today = new Date()
  today.setHours(23, 59, 59, 999)
  return due <= today ? 'due_today' : 'upcoming'
}

function parseDate(raw: string | null | undefined): Date | null {
  if (!raw) return null
  const d = new Date(raw)
  return isNaN(d.getTime()) ? null : d
}

function fmtDueLabel(row: CommitmentRow, bucket: TaskBucket): string {
  if (bucket === 'unplanned') return 'No date'
  const d = parseDate(row.due_at ?? row.remind_at)
  if (!d) return '—'
  const now = new Date()
  if (bucket === 'due_today') {
    if (d.toDateString() === now.toDateString())
      return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  }
  const days = Math.floor((d.getTime() - now.getTime()) / 86_400_000)
  if (days === 1) return 'Tomorrow'
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function fromLine(row: CommitmentRow): string {
  const d = (row.detail ?? row.source ?? '').trim()
  if (!d) return 'From: MeetingBox'
  if (/^from:/i.test(d)) return d
  return `From: ${d}`
}

function sourceIcon(row: CommitmentRow): string | null {
  const blob = `${row.detail ?? ''} ${row.source ?? ''}`.toLowerCase()
  if (blob.includes('email') || blob.includes('mail')) return icoSrcEmail
  if (row.calendar_event_id || blob.includes('sync') || blob.includes('meeting') || blob.includes('calendar'))
    return icoSrcCalendar
  if (blob.includes('self')) return icoSrcProfile
  return null
}

function dotIcon(bucket: TaskBucket): string {
  switch (bucket) {
    case 'due_today': return icoDotToday
    case 'upcoming': return icoDotUpcoming
    case 'unplanned': return icoDotUnplanned
  }
}

function Ico({ src, size, alt = '' }: { src: string; size: number; alt?: string }) {
  return (
    <span className="inline-flex shrink-0 items-center justify-center" style={{ width: size, height: size, minWidth: size }}>
      <img src={src} alt={alt} className="block max-h-full max-w-full object-contain" draggable={false} />
    </span>
  )
}

function FilterTab({
  label,
  count,
  active,
  onClick,
}: {
  label: string
  count: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex shrink-0 items-baseline gap-2 ${blk.filterTabPad} transition ${
        active
          ? 'bg-gradient-to-t from-[#011037] to-[#001857]'
          : 'bg-transparent hover:bg-white/[0.03]'
      }`}
    >
      <span className={`${pg.filterTab} leading-none ${active ? pg.filterTabActive : pg.filterTabIdle}`}>
        {label}
      </span>
      <span className={`${pg.filterTab} leading-none ${active ? 'text-[#006bf9]' : 'text-white'}`}>
        {count}
      </span>
    </button>
  )
}

function statusLabel(status?: string): string {
  if (!status) return 'Active'
  return status.charAt(0).toUpperCase() + status.slice(1).replace(/_/g, ' ')
}

function TaskCard({
  row,
  bucket,
  selected,
  onSelect,
}: {
  row: CommitmentRow
  bucket: TaskBucket
  selected: boolean
  onSelect: () => void
}) {
  const meta = SECTION_META[bucket]
  const srcIco = sourceIcon(row)
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`flex w-full items-center ${blk.rowGap} border text-left transition ${blk.row} ${
        selected
          ? 'border-[#3f8cff]/55 bg-[#006bf9]/[0.1]'
          : 'border-[#21284b] bg-gradient-to-b from-[#011137] to-[#000a26] hover:border-[#3f4253]'
      }`}
    >
      <span className="h-8 w-8 shrink-0 rounded-[6px] border-2 border-[#595979]" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className={`truncate ${pg.cardTitle}`}>{String(row.title ?? '(no title)')}</p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <Ico src={dotIcon(bucket)} size={14} />
          <p className={`${pg.cardMeta} leading-snug`}>{fromLine(row)}</p>
          {srcIco ? <Ico src={srcIco} size={18} /> : null}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <span className={`whitespace-nowrap ${pg.cardMeta} font-semibold`} style={{ color: meta.color }}>
          {fmtDueLabel(row, bucket)}
        </span>
        <span
          role="presentation"
          onClick={(e) => e.stopPropagation()}
          className="flex items-center justify-center p-1 opacity-80"
        >
          <Ico src={icoTaskKebab} size={22} />
        </span>
      </div>
    </button>
  )
}

function TaskDetailPane({
  row,
  bucket,
  onBack,
}: {
  row: CommitmentRow
  bucket: TaskBucket
  onBack: () => void
}) {
  const meta = SECTION_META[bucket]
  const srcIco = sourceIcon(row)
  const detailText = (row.detail ?? '').trim()
  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-4 border-b border-[#1a2244] px-5 py-3 shrink-0">
        <button
          type="button"
          onClick={onBack}
          className={`inline-flex items-center gap-2 ${pg.toolbar} text-white hover:text-[#b6baf2] transition lg:hidden`}
        >
          <Ico src={icoTaskBack} size={20} />
          Back
        </button>
        <span className={`${pg.labelAccent}`} style={{ color: meta.color }}>
          {meta.label}
        </span>
      </div>
      <div className="flex-1 overflow-y-auto px-5 sm:px-7 py-5" style={SCROLL}>
        <h2 className={pg.cardTitleMd}>{String(row.title ?? '(no title)')}</h2>
        <p className={`mt-2 ${pg.cardMeta}`}>{fromLine(row)}</p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span className={`${pg.cardMeta} font-semibold`} style={{ color: meta.color }}>
            {fmtDueLabel(row, bucket)}
          </span>
          <span className={`rounded-full border border-[#21284b] px-3 py-1 ${pg.cardMetaSm} text-white/90`}>
            {statusLabel(row.status)}
          </span>
          {srcIco ? <Ico src={srcIco} size={20} /> : null}
        </div>
        {detailText ? (
          <div className={`mt-6 ${pg.body} whitespace-pre-wrap`}>{detailText}</div>
        ) : (
          <p className={`mt-6 ${pg.empty}`}>No additional details for this task.</p>
        )}
        <Link
          to={`/assistant?q=${encodeURIComponent(`Help me with task: ${row.title ?? ''}`)}`}
          className={`mt-6 inline-block ${pg.cardMeta} font-semibold text-[#006bf9] hover:underline`}
        >
          Ask Assistant about this task
        </Link>
      </div>
    </div>
  )
}

export default function Tasks() {
  const user = useAuthStore((s) => s.user)

  const [tab, setTab] = useState<TaskTab>('all')
  const [loading, setLoading] = useState(true)
  const [items, setItems] = useState<CommitmentRow[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [mobileDetail, setMobileDetail] = useState(false)

  const seqRef = useRef(0)
  const firstPoll = useRef(true)

  const reload = useCallback(async () => {
    const n = ++seqRef.current
    if (firstPoll.current) setLoading(true)
    try {
      const res = await commitmentsApi.list({ status: 'all', limit: 80 })
      if (n !== seqRef.current) return
      setItems(res.commitments ?? [])
      setLoadError(null)
    } catch {
      if (n !== seqRef.current) return
      setLoadError('Could not load tasks.')
      setItems([])
    } finally {
      if (n === seqRef.current && firstPoll.current) {
        firstPoll.current = false
        setLoading(false)
      }
    }
  }, [])

  useVisiblePolling(reload)

  const counts = useMemo(() => computeCounts(items), [items])

  const buckets = useMemo(() => {
    const due_today: CommitmentRow[] = []
    const upcoming: CommitmentRow[] = []
    const unplanned: CommitmentRow[] = []
    for (const row of items) {
      const b = categorize(row)
      if (b === 'due_today') due_today.push(row)
      else if (b === 'upcoming') upcoming.push(row)
      else if (b === 'unplanned') unplanned.push(row)
    }
    return { due_today, upcoming, unplanned }
  }, [items])

  const visibleSections = useMemo((): TaskBucket[] => {
    if (tab === 'all') return ['due_today', 'upcoming', 'unplanned']
    return [tab]
  }, [tab])

  const visibleEntries = useMemo(() => {
    const out: { row: CommitmentRow; bucket: TaskBucket }[] = []
    for (const bucket of visibleSections) {
      for (const row of buckets[bucket]) {
        out.push({ row, bucket })
      }
    }
    return out
  }, [visibleSections, buckets])

  useEffect(() => {
    if (!selectedTaskId) return
    if (!visibleEntries.some(({ row }) => String(row.id) === selectedTaskId)) {
      setSelectedTaskId(null)
      setMobileDetail(false)
    }
  }, [visibleEntries, selectedTaskId])

  const selectedEntry = useMemo(
    () => visibleEntries.find(({ row }) => String(row.id) === selectedTaskId) ?? null,
    [visibleEntries, selectedTaskId],
  )

  const selectTask = (id: string) => {
    setSelectedTaskId(id)
    setMobileDetail(true)
  }

  const name = user?.display_name ?? user?.username ?? ''

  return (
    <DashboardNavShell>
      <div className={`min-h-screen text-white ${blk.pagePad}`}>
        <div className={blk.chromeRow}>
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

        <h1 className={`mb-5 ${pg.title}`}>Tasks</h1>

        <div className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-stretch">
          <div className={`flex flex-1 flex-wrap items-center ${blk.filterBar} !mb-0`}>
            {FILTER_TABS.map((f) => (
              <FilterTab
                key={f.id}
                label={f.label}
                count={
                  f.id === 'all' ? counts.all
                  : f.id === 'due_today' ? counts.dueToday
                  : f.id === 'upcoming' ? counts.upcoming
                  : counts.unplanned
                }
                active={tab === f.id}
                onClick={() => setTab(f.id)}
              />
            ))}
          </div>

          <Link
            to="/assistant?q=Add%20a%20new%20task%20for%20me"
            className={blk.addTaskBtn}
          >
            <Ico src={icoTaskAdd} size={blk.addTaskIcon} />
            <span className={`${pg.filterTab} text-[#006bf9]`}>Add task</span>
          </Link>
        </div>

        {loadError ? <p className="mb-3 text-sm text-amber-400/85">{loadError}</p> : null}

        {loading ? (
          <div className="flex min-h-[50vh] items-center justify-center">
            <LoadingSpinner size="large" />
          </div>
        ) : visibleSections.every((b) => buckets[b].length === 0) ? (
          <p className={`py-24 text-center ${pg.empty}`}>
            {items.length === 0 ? (
              <>
                No tasks yet. Use{' '}
                <Link to="/assistant" className="text-[#006bf9] hover:underline">Add task</Link>
                {' '}or ask the Assistant.
              </>
            ) : (
              'No tasks in this filter.'
            )}
          </p>
        ) : (
          <div className={`grid grid-cols-1 lg:grid-cols-[minmax(0,44.5%)_minmax(0,55.5%)] ${blk.gridGap}`}>
            <div
              className={`${blk.paneMinH} ${blk.pane} overflow-hidden ${
                mobileDetail ? 'hidden lg:block' : 'block'
              }`}
            >
              <div className={`flex flex-col ${blk.listSectionGap} overflow-y-auto ${blk.listInner}`} style={SCROLL}>
                {visibleSections.map((bucket) => {
                  const rows = buckets[bucket]
                  if (rows.length === 0) return null
                  const meta = SECTION_META[bucket]
                  return (
                    <section key={bucket}>
                      <h2
                        className={`mb-3 ${pg.sectionColored} whitespace-pre`}
                        style={{ color: meta.color }}
                      >
                        {meta.label}    {rows.length}
                      </h2>
                      <div className={`flex flex-col ${blk.listSectionGap}`}>
                        {rows.map((row) => (
                          <TaskCard
                            key={String(row.id)}
                            row={row}
                            bucket={bucket}
                            selected={String(row.id) === selectedTaskId}
                            onSelect={() => selectTask(String(row.id))}
                          />
                        ))}
                      </div>
                    </section>
                  )
                })}
              </div>
            </div>

            <div
              className={`${blk.paneMinHDetail} ${blk.pane} overflow-hidden flex flex-col ${
                selectedTaskId ? (mobileDetail ? 'flex' : 'hidden lg:flex') : 'hidden lg:flex'
              }`}
            >
              {!selectedEntry ? (
                <div className={`flex flex-1 items-center justify-center ${pg.empty}`}>
                  Select a task to view details
                </div>
              ) : (
                <TaskDetailPane
                  row={selectedEntry.row}
                  bucket={selectedEntry.bucket}
                  onBack={() => setMobileDetail(false)}
                />
              )}
            </div>
          </div>
        )}
      </div>
    </DashboardNavShell>
  )
}
