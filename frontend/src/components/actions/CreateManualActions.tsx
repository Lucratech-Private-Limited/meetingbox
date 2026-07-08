// Buttons + modals to add a manual Calendar event or Gmail message on the Actions tab.

import { useCallback, useState } from 'react'
import toast from 'react-hot-toast'
import Modal from '../ui/Modal'
import Button from '../ui/Button'
import { actionsApi } from '../../api/actions'

const DEFAULT_CALENDAR_TZ = 'Asia/Kolkata'

const COMMON_TIMEZONES = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Asia/Dubai',
  'Asia/Kolkata',
  'Asia/Tokyo',
  'Australia/Sydney',
]

function localDatePlusDays(days: number): string {
  const x = new Date()
  x.setDate(x.getDate() + days)
  const y = x.getFullYear()
  const m = String(x.getMonth() + 1).padStart(2, '0')
  const d = String(x.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function parseEmailsFromText(s: string): string[] {
  return s
    .split(/[\s,;]+/g)
    .map((p) => p.trim())
    .filter(Boolean)
}

interface CreateManualActionsProps {
  meetingId: string
  onCreated: () => void
}

export default function CreateManualActions({ meetingId, onCreated }: CreateManualActionsProps) {
  const [calOpen, setCalOpen] = useState(false)
  const [mailOpen, setMailOpen] = useState(false)
  const [calSaving, setCalSaving] = useState(false)
  const [mailSaving, setMailSaving] = useState(false)

  const [calCardTitle, setCalCardTitle] = useState('Calendar event')
  const [calDescription, setCalDescription] = useState('')
  const [calEventTitle, setCalEventTitle] = useState('')
  const [calDate, setCalDate] = useState(localDatePlusDays(1))
  const [calTime, setCalTime] = useState('10:00')
  const [calDuration, setCalDuration] = useState(30)
  const [calTz, setCalTz] = useState(
    typeof Intl !== 'undefined' && Intl.DateTimeFormat
      ? Intl.DateTimeFormat().resolvedOptions().timeZone || DEFAULT_CALENDAR_TZ
      : DEFAULT_CALENDAR_TZ,
  )
  const [calAttendees, setCalAttendees] = useState('')

  const [mailCardTitle, setMailCardTitle] = useState('Email')
  const [mailDescription, setMailDescription] = useState('')
  const [mailTo, setMailTo] = useState('')
  const [mailCc, setMailCc] = useState('')
  const [mailSubject, setMailSubject] = useState('')
  const [mailBody, setMailBody] = useState('')

  const resetCalForm = useCallback(() => {
    setCalCardTitle('Calendar event')
    setCalDescription('')
    setCalEventTitle('')
    setCalDate(localDatePlusDays(1))
    setCalTime('10:00')
    setCalDuration(30)
    setCalTz(Intl.DateTimeFormat().resolvedOptions().timeZone || DEFAULT_CALENDAR_TZ)
    setCalAttendees('')
  }, [])

  const resetMailForm = useCallback(() => {
    setMailCardTitle('Email')
    setMailDescription('')
    setMailTo('')
    setMailCc('')
    setMailSubject('')
    setMailBody('')
  }, [])

  const handleSaveCalendar = async () => {
    const t = calTime.trim()
    if (!calCardTitle.trim() || !calDate.trim() || !t) {
      toast.error('Card title, date, and time are required.')
      return
    }
    setCalSaving(true)
    try {
      await actionsApi.createManual(meetingId, {
        connector: 'calendar',
        title: calCardTitle.trim(),
        description: calDescription.trim(),
        event_title: calEventTitle.trim() || undefined,
        suggested_date: calDate.trim(),
        suggested_time: t,
        duration_minutes: calDuration,
        timezone: calTz,
        attendees: parseEmailsFromText(calAttendees),
      })
      toast.success('Calendar action added — review and execute when ready.')
      setCalOpen(false)
      resetCalForm()
      onCreated()
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Could not add calendar action')
          : 'Could not add calendar action'
      toast.error(typeof msg === 'string' ? msg : 'Could not add calendar action')
    } finally {
      setCalSaving(false)
    }
  }

  const handleSaveGmail = async () => {
    const to = parseEmailsFromText(mailTo)
    if (!mailCardTitle.trim() || to.length === 0 || !mailSubject.trim() || !mailBody.trim()) {
      toast.error('Card title, at least one recipient, subject, and message body are required.')
      return
    }
    setMailSaving(true)
    try {
      await actionsApi.createManual(meetingId, {
        connector: 'gmail',
        title: mailCardTitle.trim(),
        description: mailDescription.trim(),
        to,
        cc: parseEmailsFromText(mailCc),
        subject: mailSubject.trim(),
        email_body: mailBody,
      })
      toast.success('Email action added — review and send or save as draft.')
      setMailOpen(false)
      resetMailForm()
      onCreated()
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'response' in err
          ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Could not add email action')
          : 'Could not add email action'
      toast.error(typeof msg === 'string' ? msg : 'Could not add email action')
    } finally {
      setMailSaving(false)
    }
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => {
            resetCalForm()
            setCalOpen(true)
          }}
          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Add calendar event
        </button>
        <button
          type="button"
          onClick={() => {
            resetMailForm()
            setMailOpen(true)
          }}
          className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Add Gmail message
        </button>
      </div>

      <Modal isOpen={calOpen} onClose={() => !calSaving && setCalOpen(false)} title="New calendar event">
        <div className="space-y-3 text-sm max-w-md">
          <p className="text-gray-600">
            Creates a pending action for this meeting. Connect Google Calendar under Settings if you have not already.
          </p>
          <label className="block">
            <span className="text-gray-700 font-medium">Card title</span>
            <input
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              value={calCardTitle}
              onChange={(e) => setCalCardTitle(e.target.value)}
              placeholder="Shown in the actions list"
            />
          </label>
          <label className="block">
            <span className="text-gray-700 font-medium">Event title (optional)</span>
            <input
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              value={calEventTitle}
              onChange={(e) => setCalEventTitle(e.target.value)}
              placeholder="Defaults to card title if empty"
            />
          </label>
          <label className="block">
            <span className="text-gray-700 font-medium">Description (optional)</span>
            <textarea
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              rows={2}
              value={calDescription}
              onChange={(e) => setCalDescription(e.target.value)}
            />
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="text-gray-700 font-medium">Date</span>
              <input
                type="date"
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
                value={calDate}
                onChange={(e) => setCalDate(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="text-gray-700 font-medium">Time</span>
              <input
                type="time"
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
                value={calTime}
                onChange={(e) => setCalTime(e.target.value)}
              />
            </label>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="text-gray-700 font-medium">Duration (minutes)</span>
              <input
                type="number"
                min={1}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
                value={calDuration}
                onChange={(e) => setCalDuration(Number(e.target.value) || 30)}
              />
            </label>
            <label className="block">
              <span className="text-gray-700 font-medium">Timezone</span>
              <select
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
                value={calTz}
                onChange={(e) => setCalTz(e.target.value)}
              >
                {COMMON_TIMEZONES.map((z) => (
                  <option key={z} value={z}>
                    {z}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="block">
            <span className="text-gray-700 font-medium">Attendees (optional)</span>
            <input
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              value={calAttendees}
              onChange={(e) => setCalAttendees(e.target.value)}
              placeholder="email1@example.com, email2@example.com"
            />
          </label>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="secondary" onClick={() => setCalOpen(false)} disabled={calSaving}>
              Cancel
            </Button>
            <Button onClick={() => void handleSaveCalendar()} isLoading={calSaving}>
              Add action
            </Button>
          </div>
        </div>
      </Modal>

      <Modal isOpen={mailOpen} onClose={() => !mailSaving && setMailOpen(false)} title="New Gmail message">
        <div className="space-y-3 text-sm max-w-md">
          <p className="text-gray-600">
            Creates a pending email action for this meeting. Connect Gmail under Settings if you have not already.
          </p>
          <label className="block">
            <span className="text-gray-700 font-medium">Card title</span>
            <input
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              value={mailCardTitle}
              onChange={(e) => setMailCardTitle(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-gray-700 font-medium">Note (optional)</span>
            <textarea
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              rows={2}
              value={mailDescription}
              onChange={(e) => setMailDescription(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-gray-700 font-medium">To</span>
            <input
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              value={mailTo}
              onChange={(e) => setMailTo(e.target.value)}
              placeholder="comma-separated email addresses"
            />
          </label>
          <label className="block">
            <span className="text-gray-700 font-medium">Cc (optional)</span>
            <input
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              value={mailCc}
              onChange={(e) => setMailCc(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-gray-700 font-medium">Subject</span>
            <input
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              value={mailSubject}
              onChange={(e) => setMailSubject(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="text-gray-700 font-medium">Message</span>
            <textarea
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2"
              rows={6}
              value={mailBody}
              onChange={(e) => setMailBody(e.target.value)}
            />
          </label>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="secondary" onClick={() => setMailOpen(false)} disabled={mailSaving}>
              Cancel
            </Button>
            <Button onClick={() => void handleSaveGmail()} isLoading={mailSaving}>
              Add action
            </Button>
          </div>
        </div>
      </Modal>
    </>
  )
}