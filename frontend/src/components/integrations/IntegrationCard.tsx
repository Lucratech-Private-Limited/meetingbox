// Generic integration card used in settings and onboarding

interface IntegrationCardProps {
  name: string
  description: string
  icon: React.ReactNode
  connected: boolean
  onConnect: () => void
  onDisconnect: () => void
}

export default function IntegrationCard({
  name,
  description,
  icon,
  connected,
  onConnect,
  onDisconnect,
}: IntegrationCardProps) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 hover:border-primary-500 transition-colors">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-lg bg-gray-100 flex items-center justify-center">
            {icon}
          </div>
          <div>
            <h3 className="font-medium text-gray-900">{name}</h3>
            <p className="text-sm text-gray-500">{description}</p>
          </div>
        </div>
        {connected ? (
          <button
            onClick={onDisconnect}
            className="px-4 py-2 text-sm font-medium text-red-700 bg-red-50 rounded-lg hover:bg-red-100"
          >
            Disconnect
          </button>
        ) : (
          <button
            onClick={onConnect}
            className="px-4 py-2 text-sm font-medium text-primary-700 bg-primary-50 rounded-lg hover:bg-primary-100"
          >
            Connect
          </button>
        )}
      </div>
    </div>
  )
}
