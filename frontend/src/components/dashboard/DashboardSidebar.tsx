import clsx from 'clsx'
import { Link, useLocation } from 'react-router-dom'

/** Matches sidebar label: active `text-[#006bf9]`, inactive `text-[#afb6ce]`. */
const NAV_ICON_ACTIVE = '#006bf9'
const NAV_ICON_INACTIVE = '#afb6ce'

function scaledIconSize(px: number): number {
  return Math.round(px * 1.15)
}

// Fixed-size icon container — prevents flex stretching and preserves aspect ratio.
function Ico({ src, size, alt = '' }: { src: string; size: number; alt?: string }) {
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center"
      style={{ width: size, height: size, minWidth: size }}
    >
      <img src={src} alt={alt} className="block max-h-full max-w-full object-contain" />
    </span>
  )
}

/** Monochrome nav SVG as img — tint via mask so active state matches label blue. */
function NavIcon({ src, size, active }: { src: string; size: number; active: boolean }) {
  const bg = active ? NAV_ICON_ACTIVE : NAV_ICON_INACTIVE
  return (
    <span
      aria-hidden
      className="inline-block shrink-0"
      style={{
        width: size,
        height: size,
        minWidth: size,
        backgroundColor: bg,
        WebkitMaskImage: `url(${src})`,
        maskImage: `url(${src})`,
        WebkitMaskSize: 'contain',
        maskSize: 'contain',
        WebkitMaskRepeat: 'no-repeat',
        maskRepeat: 'no-repeat',
        WebkitMaskPosition: 'center',
        maskPosition: 'center',
      }}
    />
  )
}

// ── Local icon paths (SVGs from Figma, served from /public/icons/) ───────────
const icoHome     = '/icons/ic-home.svg'
const icoCalendar = '/icons/ic-calendar.svg'
const icoMeetings = '/icons/ic-meetings.svg'
const icoTasks    = '/icons/ic-task-nav.svg'
const icoEmails   = '/icons/ic-mail.svg'
const icoRobotic  = '/icons/ic-assistant.svg'
const icoSettings = '/icons/ic-settings.svg'
const icoLogo     = '/icons/ic-logo.svg'

type NavItem = {
  label: string
  to: string
  icon: string
  iconSize?: number
  match?: (p: string) => boolean
}

const nav: NavItem[] = [
  { label: 'Home',      to: '/dashboard', icon: icoHome,     iconSize: scaledIconSize(24), match: (p) => p.replace(/\/$/, '') === '/dashboard' },
  { label: 'Calendar',  to: '/calendar',  icon: icoCalendar, iconSize: scaledIconSize(22) },
  { label: 'Meetings',  to: '/meetings',  icon: icoMeetings, iconSize: scaledIconSize(24) },
  { label: 'Tasks',     to: '/tasks',     icon: icoTasks,    iconSize: scaledIconSize(22) },
  { label: 'Emails',    to: '/emails',    icon: icoEmails,   iconSize: scaledIconSize(22) },
  { label: 'Assistant', to: '/assistant', icon: icoRobotic,  iconSize: scaledIconSize(22) },
]

export default function DashboardSidebar({
  open,
  onClose,
  onOpenSidebar,
}: {
  open: boolean
  onClose: () => void
  onOpenSidebar?: () => void
}) {
  const location = useLocation()

  return (
    <>
      {/* Mobile backdrop */}
      <button
        type="button"
        aria-label="Close sidebar"
        onClick={onClose}
        className={clsx(
          'fixed inset-0 z-40 bg-black/60 backdrop-blur-sm transition-opacity lg:hidden',
          open ? 'opacity-100' : 'pointer-events-none opacity-0'
        )}
      />

      <aside
        className={clsx(
          'fixed inset-y-0 left-0 z-50 w-[272px]',
          'bg-[#01081a]',
          'transition-transform duration-200 lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        {/* Gradient right border matching Figma divider */}
        <div
          className="pointer-events-none absolute inset-y-0 right-0 w-[3px]"
          style={{ background: 'linear-gradient(180deg,rgba(2,23,77,0) 0%,rgba(2,23,77,0.421) 6%,rgb(2,23,77) 47%,rgba(2,23,77,0.371) 93%,rgba(2,23,77,0) 100%)' }}
        />
        <div className="flex h-full flex-col pt-[68px]">
          {/* Mobile toggle */}
          {onOpenSidebar && (
            <button
              type="button"
              onClick={onOpenSidebar}
              className="absolute right-[-2rem] top-4 flex h-8 w-8 items-center justify-center rounded-r-lg bg-[#01081a] text-white/60 lg:hidden"
              aria-label="Open sidebar"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                <path strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
          )}

          {/* Brand */}
          <div className="absolute left-5 top-5 flex items-center gap-3">
            <Ico src={icoLogo} size={28} alt="MeetingBox" />
            <span className="text-[16px] font-bold tracking-[-0.3px] text-[#f1f5f9]">
              MeetingBox AI
            </span>
          </div>

          {/* Nav items */}
          <nav className="flex flex-col gap-0.5 px-3">
            {nav.map((item) => {
              const p = location.pathname.replace(/\/$/, '') || '/'
              const t = item.to.replace(/\/$/, '') || '/'
              const active = item.match
                ? item.match(location.pathname)
                : p === t || p.startsWith(`${t}/`)

              return (
                <Link
                  key={item.label}
                  to={item.to}
                  onClick={onClose}
                  className={clsx(
                    'relative flex items-center gap-3 rounded-[10px] px-3 py-2.5 transition-colors',
                    active
                      ? 'bg-[#011745] border border-[#3f8cff]'
                      : 'border border-transparent hover:bg-white/5'
                  )}
                >
                  <NavIcon src={item.icon} size={item.iconSize ?? scaledIconSize(22)} active={active} />
                  <span
                    className={clsx(
                      'text-[15px] font-semibold',
                      active ? 'text-[#006bf9]' : 'text-[#afb6ce]'
                    )}
                  >
                    {item.label}
                  </span>
                </Link>
              )
            })}
          </nav>

          {/* Settings — pinned at bottom */}
          <div className="absolute bottom-4 left-0 right-0 px-3">
            <Link
              to="/settings"
              onClick={onClose}
              className={clsx(
                'flex items-center gap-3 rounded-[10px] px-3 py-2.5 transition-colors border',
                location.pathname === '/settings'
                  ? 'bg-[#011745] border-[#3f8cff]'
                  : 'border-transparent hover:bg-white/5'
              )}
            >
              <NavIcon src={icoSettings} size={scaledIconSize(22)} active={location.pathname === '/settings'} />
              <span
                className={clsx(
                  'text-[15px] font-semibold',
                  location.pathname === '/settings' ? 'text-[#006bf9]' : 'text-[#afb6ce]'
                )}
              >
                Settings
              </span>
            </Link>
          </div>
        </div>
      </aside>
    </>
  )
}
