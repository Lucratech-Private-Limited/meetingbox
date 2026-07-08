import { ReactNode } from 'react'
import clsx from 'clsx'

type Glow = 'blue' | 'cyan' | 'purple' | 'green' | 'none'

const glowMap: Record<Glow, string> = {
  none: 'border-[rgba(59,130,246,0.15)]',
  blue: 'border-[rgba(33,150,243,0.28)] shadow-[0_0_0_1px_rgba(33,150,243,0.10),0_18px_55px_rgba(0,0,0,0.55)]',
  cyan: 'border-[rgba(0,191,255,0.22)] shadow-[0_0_0_1px_rgba(0,191,255,0.10),0_18px_55px_rgba(0,0,0,0.55)]',
  purple: 'border-[rgba(139,92,246,0.22)] shadow-[0_0_0_1px_rgba(139,92,246,0.10),0_18px_55px_rgba(0,0,0,0.55)]',
  green: 'border-[rgba(16,185,129,0.22)] shadow-[0_0_0_1px_rgba(16,185,129,0.10),0_18px_55px_rgba(0,0,0,0.55)]',
}

export default function GlassCard({
  title,
  action,
  children,
  className,
  glow = 'none',
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
  glow?: Glow
}) {
  return (
    <section
      className={clsx(
        'group relative overflow-hidden rounded-3xl border bg-[rgba(8,22,48,0.85)] backdrop-blur-xl',
        'transition-transform duration-200 will-change-transform hover:-translate-y-[2px]',
        glowMap[glow],
        className
      )}
    >
      <div className="pointer-events-none absolute -left-24 -top-24 h-56 w-56 rounded-full bg-[rgba(33,150,243,0.14)] blur-3xl opacity-60 transition-opacity duration-300 group-hover:opacity-80" />
      <div className="pointer-events-none absolute -right-24 -bottom-24 h-56 w-56 rounded-full bg-[rgba(139,92,246,0.10)] blur-3xl opacity-40 transition-opacity duration-300 group-hover:opacity-60" />

      {(title || action) && (
        <header className="flex items-center justify-between gap-3 px-5 pt-5">
          {title ? (
            <h2 className="text-sm font-semibold tracking-wide text-white/90">{title}</h2>
          ) : (
            <div />
          )}
          {action ? <div className="shrink-0">{action}</div> : null}
        </header>
      )}

      <div className={clsx('px-5 pb-5', title || action ? 'pt-4' : 'pt-5')}>{children}</div>
    </section>
  )
}

