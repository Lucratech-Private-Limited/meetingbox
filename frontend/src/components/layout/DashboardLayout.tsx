// Dashboard shell layout — no top Navbar; chrome is handled by DashboardNavShell per-page.

import { Outlet } from 'react-router-dom'
import InteractiveTutorial from '../tutorial/InteractiveTutorial'

export default function DashboardLayout() {
  return (
    <div className="min-h-screen bg-[#01081a]">
      <Outlet />
      <InteractiveTutorial />
    </div>
  )
}
