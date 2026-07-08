// Shared dashboard chrome: left sidebar + content area.

import { useState, type ReactNode } from 'react'
import DashboardSidebar from './DashboardSidebar'

export default function DashboardNavShell({ children }: { children: ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="min-h-screen bg-[#01081a] text-white">
      <div className="pointer-events-none fixed inset-0 -z-10 bg-[radial-gradient(ellipse_80%_55%_at_60%_5%,rgba(0,107,249,0.08),transparent_55%),radial-gradient(ellipse_70%_50%_at_10%_10%,rgba(1,23,69,0.18),transparent_45%),linear-gradient(180deg,#01081a_0%,#000a26_55%,#01081a_100%)]" />

      <DashboardSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} onOpenSidebar={() => setSidebarOpen(true)} />

      <div className="lg:pl-[17rem]">
        {children}
      </div>
    </div>
  )
}
