import { Tab } from '@headlessui/react'
import { useSearchParams } from 'react-router-dom'
import GeneralSettings from '../components/settings/GeneralSettings'
import IntegrationsSettings from '../components/settings/IntegrationsSettings'
import PrivacySettings from '../components/settings/PrivacySettings'

const tabs = [
  { name: 'General', component: GeneralSettings },
  { name: 'Integrations', component: IntegrationsSettings },
  { name: 'Privacy', component: PrivacySettings },
]

export default function Settings() {
  const [searchParams] = useSearchParams()
  const defaultTab = searchParams.has('integration') ? 1 : 0

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      <h1 className="text-3xl font-bold text-gray-900 mb-8">Settings</h1>

      <Tab.Group defaultIndex={defaultTab}>
        <Tab.List className="flex space-x-1 rounded-lg bg-primary-100 p-1 mb-8">
          {tabs.map((tab) => (
            <Tab
              key={tab.name}
              className={({ selected }) =>
                `w-full rounded-lg py-2.5 text-sm font-medium leading-5 transition-colors ${
                  selected
                    ? 'bg-white text-primary-700 shadow'
                    : 'text-primary-600 hover:bg-white/[0.12] hover:text-primary-800'
                }`
              }
            >
              {tab.name}
            </Tab>
          ))}
        </Tab.List>
        <Tab.Panels>
          {tabs.map((tab, idx) => (
            <Tab.Panel key={idx}>
              <tab.component />
            </Tab.Panel>
          ))}
        </Tab.Panels>
      </Tab.Group>
    </div>
  )
}
