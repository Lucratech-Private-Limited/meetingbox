import { useEffect } from 'react'

import { KioskIdleFrame, useKioskIdleClock } from '../components/kiosk/idle/KioskIdleFrame'

/**
 * Fullscreen kiosk/tablet idle surface — mirrors Figma #338:60 (892×573).
 *
 * Routed at `/kiosk/idle` without the main chrome so it fills the viewport and
 * keeps the original aspect-ratio frame centered (letterboxed on odd viewports).
 */
export default function KioskIdlePage() {
  const now = useKioskIdleClock(1000)

  useEffect(() => {
    const prev = document.body.style.overflow
    document.documentElement.style.overflow = 'hidden'
    document.body.style.overflow = 'hidden'
    return () => {
      document.documentElement.style.overflow = ''
      document.body.style.overflow = prev
    }
  }, [])

  return (
    <div className="fixed inset-0 z-[100] flex min-h-[100dvh] w-[100vw] flex-col overscroll-none bg-black">
      <main className="flex min-h-0 min-w-0 flex-1 items-center justify-center">
        <KioskIdleFrame
          greeting="Good afternoon"
          now={now}
          temperature="35°C"
          condition="Partly cloudy"
          meetingTimeLabel="10:00 AM"
          meetingTitle="One Piece UI Development Discussion"
          moreLabel="+2 more"
        />
      </main>
    </div>
  )
}
