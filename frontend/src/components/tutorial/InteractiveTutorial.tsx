import { useCallback, useEffect, useLayoutEffect, useState, type CSSProperties } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { tutorialSteps } from './tutorialSteps'
import { useTutorialStore } from '../../store/tutorialStore'

type Rect = { top: number; left: number; width: number; height: number }

const PADDING = 8

function queryTarget(selector: string | undefined): HTMLElement | null {
  if (!selector) return null
  return document.querySelector(`[data-tutorial="${selector}"]`) as HTMLElement | null
}

function measure(el: HTMLElement): Rect {
  const r = el.getBoundingClientRect()
  return {
    top: r.top - PADDING,
    left: r.left - PADDING,
    width: r.width + PADDING * 2,
    height: r.height + PADDING * 2,
  }
}

function pathMatchesDashboard(pathname: string): boolean {
  return pathname === '/dashboard' || pathname === '/'
}

function shouldNavigateToStep(pathname: string, stepPath: string): boolean {
  if (stepPath === '/dashboard') {
    return !pathMatchesDashboard(pathname) && !pathname.startsWith('/meeting/')
  }
  return !pathname.startsWith(stepPath)
}

export default function InteractiveTutorial() {
  const navigate = useNavigate()
  const location = useLocation()
  const active = useTutorialStore((s) => s.active)
  const index = useTutorialStore((s) => s.index)
  const next = useTutorialStore((s) => s.next)
  const back = useTutorialStore((s) => s.back)
  const skip = useTutorialStore((s) => s.skip)

  const step = tutorialSteps[index]
  const [rect, setRect] = useState<Rect | null>(null)
  const [centerFallback, setCenterFallback] = useState(false)

  const updateRect = useCallback(() => {
    if (!active || !step) {
      setRect(null)
      setCenterFallback(false)
      return
    }
    if (!step.targetSelector || step.placement === 'center') {
      setRect(null)
      setCenterFallback(true)
      return
    }
    const el = queryTarget(step.targetSelector)
    if (!el) {
      setRect(null)
      setCenterFallback(true)
      return
    }
    setCenterFallback(false)
    el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' })
    setRect(measure(el))
  }, [active, step])

  useEffect(() => {
    if (!active || !step) return
    if (shouldNavigateToStep(location.pathname, step.path)) {
      navigate(step.path)
    }
  }, [active, step, location.pathname, navigate])

  useLayoutEffect(() => {
    if (!active) return
    const t = window.requestAnimationFrame(() => updateRect())
    return () => window.cancelAnimationFrame(t)
  }, [active, index, location.pathname, updateRect])

  useEffect(() => {
    if (!active) return
    const onResize = () => updateRect()
    window.addEventListener('resize', onResize)
    window.addEventListener('scroll', onResize, true)
    return () => {
      window.removeEventListener('resize', onResize)
      window.removeEventListener('scroll', onResize, true)
    }
  }, [active, updateRect])

  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') skip()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, skip])

  if (!active || !step) return null

  const placement = step.placement ?? (step.targetSelector ? 'bottom' : 'center')
  const showSpotlight = !centerFallback && rect && placement !== 'center'
  const isLast = index === tutorialSteps.length - 1

  let tooltipStyle: CSSProperties = {
    position: 'fixed',
    zIndex: 10002,
    maxWidth: 'min(22rem, calc(100vw - 2rem))',
  }

  if (showSpotlight && rect) {
    const gap = 12
    const vw = typeof window !== 'undefined' ? window.innerWidth : 0
    const vh = typeof window !== 'undefined' ? window.innerHeight : 0
    const tw = 352
    const cx = rect.left + rect.width / 2 - tw / 2
    const clampLeft = Math.min(Math.max(16, cx), vw - 16 - tw)

    if (placement === 'bottom') {
      tooltipStyle = {
        ...tooltipStyle,
        top: Math.min(rect.top + rect.height + gap, vh - 120),
        left: clampLeft,
      }
    } else if (placement === 'top') {
      tooltipStyle = {
        ...tooltipStyle,
        bottom: Math.min(Math.max(16, vh - rect.top + gap), vh - 16),
        left: clampLeft,
      }
    } else if (placement === 'left') {
      tooltipStyle = {
        ...tooltipStyle,
        top: Math.min(Math.max(16, rect.top + rect.height / 2 - 80), vh - 200),
        right: Math.min(Math.max(16, vw - rect.left + gap), vw - 16),
      }
    } else {
      tooltipStyle = {
        ...tooltipStyle,
        top: Math.min(Math.max(16, rect.top + rect.height / 2 - 80), vh - 200),
        left: Math.min(rect.left + rect.width + gap, vw - 16 - tw),
      }
    }
  } else {
    tooltipStyle = {
      ...tooltipStyle,
      top: '50%',
      left: '50%',
      transform: 'translate(-50%, -50%)',
    }
  }

  return (
    <div className="fixed inset-0 z-[10000] pointer-events-none" aria-live="polite">
      <div
        className={`absolute inset-0 pointer-events-auto ${showSpotlight ? 'bg-transparent' : 'bg-app-deep/75'}`}
        onClick={(e) => e.stopPropagation()}
        aria-hidden
      />

      {showSpotlight && rect && (
        <div
          className="fixed z-[10001] rounded-xl border-2 border-primary-400 pointer-events-none transition-all duration-200 shadow-[0_0_0_9999px_rgba(15,23,42,0.55)]"
          style={{
            top: rect.top,
            left: rect.left,
            width: rect.width,
            height: rect.height,
          }}
        />
      )}

      <div
        className="pointer-events-auto rounded-xl border border-app-border bg-app-surface p-4 shadow-xl"
        style={tooltipStyle}
        role="dialog"
        aria-labelledby="tutorial-title"
        aria-describedby="tutorial-body"
      >
        <p id="tutorial-title" className="text-sm font-semibold text-app-ink">
          {step.title}
        </p>
        <p id="tutorial-body" className="mt-2 text-sm text-app-ink-muted leading-relaxed">
          {step.body}
        </p>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-app-ink-faint">
            {index + 1} / {tutorialSteps.length}
          </span>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={skip}
              className="px-3 py-1.5 text-xs font-medium text-app-ink-muted hover:text-app-ink"
            >
              Skip tour
            </button>
            <button
              type="button"
              onClick={back}
              disabled={index === 0}
              className="px-3 py-1.5 text-xs font-medium rounded-lg border border-app-border-light text-app-ink-muted hover:bg-app-page disabled:opacity-40"
            >
              Back
            </button>
            <button
              type="button"
              onClick={next}
              className="px-3 py-1.5 text-xs font-medium rounded-lg bg-primary-600 text-white hover:bg-primary-700"
            >
              {isLast ? 'Done' : 'Next'}
            </button>
          </div>
        </div>
        <p className="mt-2 text-[10px] text-app-ink-faint">Press Esc to exit</p>
      </div>
    </div>
  )
}
