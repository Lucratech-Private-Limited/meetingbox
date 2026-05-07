import { useEffect, useMemo, useRef, useState } from 'react'
import { format } from 'date-fns'

import './kioskFrame.css'
import { useKioskIdleScale } from './useKioskIdleScale'

/** Public URL for kiosk assets (under `frontend/public`). */
export const KIOSK_IDLE_ASSET = {
  background: '/assets/kiosk-idle/background_landscape.png',
  micOrb: '/assets/kiosk-idle/mic_orb.png',
  sunIcon: '/assets/kiosk-idle/icon_sun.png',
  calendarIcon: '/assets/kiosk-idle/icon_calendar.png',
}

export interface KioskIdleFrameProps {
  greeting: string
  now: Date
  temperature: string
  condition: string
  meetingTimeLabel: string
  meetingTitle: string
  /** e.g. "+2 more"; omit or empty when none */
  moreLabel?: string
}

/**
 * Idle screen — fullscreen landscape frame preserving Figma 892×573 (#338:60).
 * Structure: flex/grid only inside safe area; background uses absolute decorator.
 */
export function KioskIdleFrame(props: KioskIdleFrameProps) {
  const {
    greeting,
    now,
    temperature,
    condition,
    meetingTimeLabel,
    meetingTitle,
    moreLabel,
  } = props
  const rootRef = useRef<HTMLDivElement>(null)
  useKioskIdleScale(rootRef)

  const { clockDigits, suffix } = useMemo(() => splitTime(now), [now])

  const dateLine = useMemo(() => format(now, 'EEEE, MMMM d'), [now])

  return (
    <div
      ref={rootRef}
      className="kiosk-idle-frame text-white"
      style={{
        fontFamily:
          '"42dot Sans", "Inter", ui-sans-serif, system-ui, sans-serif',
      }}
    >
      <div
        className="kiosk-bg-decor"
        style={{ backgroundImage: `url("${KIOSK_IDLE_ASSET.background}")` }}
        aria-hidden
      />

      <div className="kiosk-safe">
        {/* Top: clock cluster | weather (space-between aligns Figma left/right anchors) */}
        <div className="flex min-h-0 w-full shrink-0 flex-row flex-wrap items-start justify-between gap-x-8 gap-y-4">
          <section aria-label="Clock">
            <p className="kiosk-text-greeting truncate whitespace-nowrap text-left">{greeting}</p>
            {/* Time row height 119 design px — keeps AM vertically aligned */}
            <div
              className="flex flex-row flex-nowrap items-end"
              style={{
                gap: 'calc(29 * var(--kiosk-s, 1) * 1px)',
                minHeight: 'calc(119 * var(--kiosk-s, 1) * 1px)',
              }}
            >
              <span
                className="kiosk-text-time tabular-nums leading-none tracking-tight"
                aria-live="polite"
              >
                {clockDigits}
              </span>
              <span className="kiosk-text-ampm">{suffix}</span>
            </div>
            <p className="kiosk-text-date mt-0 text-left">{dateLine}</p>
          </section>

          <WeatherBlock temperature={temperature} condition={condition} />
        </div>

        {/* Fills slack between heading block (~y214) and bottom row (~y333+) */}
        <div className="min-h-px w-full min-w-0 flex-1" aria-hidden />

        <BottomRow
          meetingTimeLabel={meetingTimeLabel}
          meetingTitle={meetingTitle}
          moreLabel={moreLabel ?? ''}
        />
      </div>
    </div>
  )
}

function splitTime(d: Date) {
  const h24 = d.getHours()
  const mins = String(d.getMinutes()).padStart(2, '0')
  const suffix = h24 >= 12 ? 'PM' : 'AM'
  let h12 = h24 % 12
  if (h12 === 0) h12 = 12
  return { clockDigits: `${h12}:${mins}`, suffix }
}

function WeatherBlock({
  temperature,
  condition,
}: {
  temperature: string
  condition: string
}) {
  return (
    <section
      aria-label="Weather"
      className="flex shrink-0 flex-row flex-nowrap items-start"
      style={{ gap: 'calc(18 * var(--kiosk-s, 1) * 1px)' }}
    >
      <div
        className="flex-shrink-0 overflow-hidden rounded-full"
        style={{
          width: 'calc(64 * var(--kiosk-s, 1) * 1px)',
          height: 'calc(64 * var(--kiosk-s, 1) * 1px)',
          boxShadow: '0 0 0 1px rgba(253,205,118,0.12)',
        }}
      >
        <img src={KIOSK_IDLE_ASSET.sunIcon} alt="" className="h-full w-full object-contain p-1" />
      </div>
      <div className="flex min-w-0 flex-col justify-start gap-y-[calc(6*var(--kiosk-s,1)*1px)] text-right leading-tight">
        <p className="kiosk-text-weather-temp whitespace-nowrap text-white">{temperature}</p>
        <p className="kiosk-text-weather-cond">{condition}</p>
      </div>
    </section>
  )
}

function BottomRow({
  meetingTimeLabel,
  meetingTitle,
  moreLabel,
}: {
  meetingTimeLabel: string
  meetingTitle: string
  moreLabel: string
}) {
  return (
    <div className="flex w-full shrink-0 flex-row flex-wrap items-end justify-between gap-x-10 gap-y-6">
      <div
        className="flex min-w-0 flex-col"
        style={{
          width: 'calc(282 * var(--kiosk-s, 1) * 1px)',
          rowGap: 'calc(17 * var(--kiosk-s, 1) * 1px)',
        }}
      >
        <p className="kiosk-text-next-label">Next up</p>
        <div
          className="flex flex-row flex-nowrap items-center"
          style={{ gap: 'calc(15 * var(--kiosk-s, 1) * 1px)' }}
        >
          <img
            src={KIOSK_IDLE_ASSET.calendarIcon}
            alt=""
            className="shrink-0 object-contain"
            style={{
              width: 'calc(34 * var(--kiosk-s, 1) * 1px)',
              height: 'calc(34 * var(--kiosk-s, 1) * 1px)',
            }}
          />
          <span className="kiosk-text-next-label whitespace-nowrap">{meetingTimeLabel}</span>
        </div>
        <p
          className="kiosk-text-meeting-title max-w-full truncate leading-tight"
          title={meetingTitle}
        >
          Now : {meetingTitle}
        </p>
        {moreLabel ? <p className="kiosk-text-more">{moreLabel}</p> : null}
      </div>

      <RecordingCard />
    </div>
  )
}

function RecordingCard() {
  const sv = 'var(--kiosk-s,1)'
  return (
    <div
      role="presentation"
      className="relative shrink-0"
      style={{
        width: `calc(414 * ${sv} * 1px)`,
        borderRadius: `calc(30 * ${sv} * 1px)`,
        padding: `calc(3 * ${sv} * 1px)`,
        boxSizing: 'border-box',
        backgroundImage:
          'linear-gradient(180deg,#034EE2 0%,#0139B3 62%,#0139B3 100%)',
        boxShadow: '0 4px 11px rgba(0,128,255,0.34)',
      }}
    >
      <div
        className="flex h-full min-h-0 w-full flex-row flex-nowrap items-center overflow-hidden"
        style={{
          borderRadius: `calc(27 * ${sv} * 1px)`,
          minHeight: `calc(${161} * ${sv} * 1px)`,
          backgroundImage: 'linear-gradient(180deg,#0038b6,#002376)',
        }}
      >
        <img
          src={KIOSK_IDLE_ASSET.micOrb}
          alt=""
          className="pointer-events-none object-contain"
          style={{
            marginLeft: 'calc(27 * var(--kiosk-s, 1) * 1px)',
            marginRight: 'calc(20 * var(--kiosk-s, 1) * 1px)',
            width: 'calc(101 * var(--kiosk-s, 1) * 1px)',
            height: 'calc(101 * var(--kiosk-s, 1) * 1px)',
            filter:
              'drop-shadow(0 0 calc(8 * var(--kiosk-s, 1) * 1px) rgba(0, 89, 255, 0.55))',
          }}
        />

        <div
          className="flex min-w-0 flex-1 flex-col justify-center"
          style={{
            paddingRight: 'calc(16 * var(--kiosk-s, 1) * 1px)',
          }}
        >
          <p className="kiosk-record-title truncate text-white">Start Recording</p>
          <p className="kiosk-record-sub truncate text-white opacity-95">
            Tap or say &quot;start recording&quot;
          </p>
        </div>
      </div>
    </div>
  )
}

export function useKioskIdleClock(intervalMs = 60000): Date {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), intervalMs)
    setNow(new Date())
    return () => window.clearInterval(id)
  }, [intervalMs])

  return now
}
