import { Tab } from '@headlessui/react'
import { useSearchParams } from 'react-router-dom'
import GeneralSettings from '../components/settings/GeneralSettings'
import DevicesSettings from '../components/settings/DevicesSettings'
import IntegrationsSettings from '../components/settings/IntegrationsSettings'
import PrivacySettings from '../components/settings/PrivacySettings'
import DashboardNavShell from '../components/dashboard/DashboardNavShell'

const tabs = [
  { name: 'General', component: GeneralSettings },
  { name: 'Devices', component: DevicesSettings },
  { name: 'Integrations', component: IntegrationsSettings },
  { name: 'Privacy', component: PrivacySettings },
]

export default function Settings() {
  const [searchParams] = useSearchParams()
  const defaultTab = searchParams.has('integration') ? 2 : 0

  return (
    <DashboardNavShell>
    <div className="max-w-4xl mx-auto px-4 py-8">
      <h1 className="text-3xl font-bold text-app-ink mb-8">Settings</h1>

      <Tab.Group defaultIndex={defaultTab}>
        <div data-tutorial="tutorial-settings-tabs">
          <Tab.List className="flex space-x-1 rounded-lg bg-app-surface-soft p-1 mb-8 border border-app-border">
            {tabs.map((tab) => (
              <Tab
                key={tab.name}
                className={({ selected }) =>
                  `w-full rounded-lg py-2.5 text-sm font-medium leading-5 transition-colors ${
                    selected
                      ? 'bg-app-raised text-app-ink shadow-md ring-1 ring-app-border-light/40'
                      : 'text-primary-400 hover:bg-app-ink/10 hover:text-primary-200'
                  }`
                }
              >
                {tab.name}
              </Tab>
            ))}
          </Tab.List>
        </div>
        <Tab.Panels>
          {tabs.map((tab, idx) => (
            <Tab.Panel key={idx}>
              <tab.component />
            </Tab.Panel>
          ))}
        </Tab.Panels>
      </Tab.Group>
    </div>
    </DashboardNavShell>
  )
}
