import { useEffect, useRef, useState } from 'react'
import { fetchWeather, type WeatherData } from '../api/weather'

const POLL_INTERVAL_MS = 10 * 60 * 1000 // 10 minutes

export function useWeather(): { data: WeatherData | null; loading: boolean } {
  const [data, setData] = useState<WeatherData | null>(null)
  const [loading, setLoading] = useState(true)
  const cancelled = useRef(false)

  useEffect(() => {
    cancelled.current = false

    const load = async () => {
      try {
        const result = await fetchWeather()
        if (!cancelled.current) {
          setData(result)
        }
      } catch {
        // Silently fail — dashboard will show placeholder
      } finally {
        if (!cancelled.current) setLoading(false)
      }
    }

    load()
    const interval = setInterval(load, POLL_INTERVAL_MS)

    return () => {
      cancelled.current = true
      clearInterval(interval)
    }
  }, [])

  return { data, loading }
}
