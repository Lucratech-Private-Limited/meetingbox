import { type RefObject, useLayoutEffect, useState } from 'react'

/** Frame design width matching Figma #338:60 (`892`). */
export const KIOSK_FRAME_W = 892

/**
 * Tracks scale = rendered-width / design width and updates `--kiosk-s` so
 * typography and pixel-accurate spacings can use `calc(n * var(--kiosk-s))`.
 */
export function useKioskIdleScale(ref: RefObject<HTMLElement | null>) {
  const [scale, setScale] = useState(1)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setScale(el.clientWidth / KIOSK_FRAME_W))
    ro.observe(el)
    setScale(el.clientWidth / KIOSK_FRAME_W)
    return () => ro.disconnect()
  }, [ref])

  useLayoutEffect(() => {
    ref.current?.style.setProperty('--kiosk-s', String(scale))
  }, [scale, ref])

  return scale
}

export function kioskPx(designPx: number, scale?: number): string {
  if (typeof scale !== 'number' || Number.isNaN(scale)) {
    return `calc(${designPx} * var(--kiosk-s, 1) * 1px)`
  }
  return `${designPx * scale}px`
}
