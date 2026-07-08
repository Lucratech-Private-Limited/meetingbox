// Main app layout — renders Navbar + child routes via <Outlet />

import { Outlet } from 'react-router-dom'
import Navbar from './Navbar'
import InteractiveTutorial from '../tutorial/InteractiveTutorial'

export default function Layout() {
  return (
    <div className="min-h-screen bg-app-page">
      <Navbar />
      <main>
        <Outlet />
      </main>
      <InteractiveTutorial />
    </div>
  )
}
