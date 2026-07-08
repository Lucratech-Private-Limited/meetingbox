export interface WeatherData {
  city: string
  temperature: number | null
  feels_like: number | null
  condition: string
  high: number | null
  low: number | null
  humidity: number | null
  wind_kph: number | null
  aqi: number | null
  aqi_label: string | null
}

const DEFAULT_WEATHER_CITY = 'Bengaluru'
const DEFAULT_WEATHER_LAT = 12.9716
const DEFAULT_WEATHER_LON = 77.5946
const WEATHER_REQUEST_TIMEOUT_MS = 8_000

const WMO_CODES: Record<number, string> = {
  0: 'Clear sky',
  1: 'Mainly clear',
  2: 'Partly cloudy',
  3: 'Overcast',
  45: 'Foggy',
  48: 'Icy fog',
  51: 'Light drizzle',
  53: 'Drizzle',
  55: 'Heavy drizzle',
  61: 'Light rain',
  63: 'Rain',
  65: 'Heavy rain',
  71: 'Light snow',
  73: 'Snow',
  75: 'Heavy snow',
  80: 'Rain showers',
  81: 'Rain showers',
  82: 'Violent showers',
  95: 'Thunderstorm',
  96: 'Thunderstorm with hail',
  99: 'Thunderstorm with heavy hail',
}

interface OpenMeteoForecast {
  current?: {
    temperature_2m?: number
    apparent_temperature?: number
    weathercode?: number
    relative_humidity_2m?: number
    wind_speed_10m?: number
  }
  daily?: {
    temperature_2m_max?: number[]
    temperature_2m_min?: number[]
  }
}

interface OpenMeteoAirQuality {
  current?: {
    us_aqi?: number
  }
}

interface IpLocation {
  city?: string
  region?: string
  country_name?: string
  latitude?: number | string
  longitude?: number | string
}

interface WeatherLocation {
  city: string
  lat: number
  lon: number
}

let ipLocationPromise: Promise<WeatherLocation> | null = null

function readNumberEnv(value: string | undefined, fallback: number): number {
  if (!value?.trim()) return fallback
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function readOptionalNumberEnv(value: string | undefined): number | null {
  if (!value?.trim()) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function aqiLabel(usAqi: number | null): string | null {
  if (usAqi == null) return null
  if (usAqi <= 50) return 'Good'
  if (usAqi <= 100) return 'Moderate'
  if (usAqi <= 150) return 'Unhealthy (Sensitive)'
  if (usAqi <= 200) return 'Unhealthy'
  if (usAqi <= 300) return 'Very Unhealthy'
  return 'Hazardous'
}

async function fetchJson<T>(url: string, timeoutMs = WEATHER_REQUEST_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(url, { signal: controller.signal })
    if (!response.ok) {
      throw new Error(`Weather request failed: ${response.status}`)
    }
    return response.json() as Promise<T>
  } finally {
    window.clearTimeout(timeout)
  }
}

function fallbackLocation(): WeatherLocation {
  return {
    city: import.meta.env.VITE_WEATHER_CITY?.trim() || DEFAULT_WEATHER_CITY,
    lat: readNumberEnv(import.meta.env.VITE_WEATHER_LAT, DEFAULT_WEATHER_LAT),
    lon: readNumberEnv(import.meta.env.VITE_WEATHER_LON, DEFAULT_WEATHER_LON),
  }
}

async function detectLocationByIp(): Promise<WeatherLocation> {
  const fixedLat = readOptionalNumberEnv(import.meta.env.VITE_WEATHER_LAT)
  const fixedLon = readOptionalNumberEnv(import.meta.env.VITE_WEATHER_LON)
  if (fixedLat != null && fixedLon != null) {
    return fallbackLocation()
  }

  if (!ipLocationPromise) {
    ipLocationPromise = fetchJson<IpLocation>('https://ipapi.co/json/', 5_000)
      .then((data) => {
        const lat = typeof data.latitude === 'string' ? Number(data.latitude) : data.latitude
        const lon = typeof data.longitude === 'string' ? Number(data.longitude) : data.longitude
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
          return fallbackLocation()
        }

        return {
          city: data.city?.trim() || data.region?.trim() || data.country_name?.trim() || fallbackLocation().city,
          lat: lat as number,
          lon: lon as number,
        }
      })
      .catch(() => fallbackLocation())
  }

  return ipLocationPromise
}

export async function fetchWeather(): Promise<WeatherData> {
  const { lat, lon, city } = await detectLocationByIp()

  const forecastParams = new URLSearchParams({
    latitude: String(lat),
    longitude: String(lon),
    current: 'temperature_2m,apparent_temperature,weathercode,relative_humidity_2m,wind_speed_10m',
    daily: 'temperature_2m_max,temperature_2m_min',
    timezone: 'auto',
    forecast_days: '1',
  })
  const aqiParams = new URLSearchParams({
    latitude: String(lat),
    longitude: String(lon),
    current: 'us_aqi',
  })

  const result: WeatherData = {
    city,
    temperature: null,
    feels_like: null,
    condition: 'Unknown',
    high: null,
    low: null,
    humidity: null,
    wind_kph: null,
    aqi: null,
    aqi_label: null,
  }

  const [forecast, airQuality] = await Promise.allSettled([
    fetchJson<OpenMeteoForecast>(`https://api.open-meteo.com/v1/forecast?${forecastParams}`),
    fetchJson<OpenMeteoAirQuality>(`https://air-quality-api.open-meteo.com/v1/air-quality?${aqiParams}`),
  ])

  if (forecast.status === 'fulfilled') {
    const current = forecast.value.current ?? {}
    const daily = forecast.value.daily ?? {}

    result.temperature = current.temperature_2m != null ? Math.round(current.temperature_2m) : null
    result.feels_like = current.apparent_temperature != null ? Math.round(current.apparent_temperature) : null
    result.condition = current.weathercode != null ? WMO_CODES[current.weathercode] ?? 'Unknown' : 'Unknown'
    result.high = daily.temperature_2m_max?.[0] != null ? Math.round(daily.temperature_2m_max[0]) : null
    result.low = daily.temperature_2m_min?.[0] != null ? Math.round(daily.temperature_2m_min[0]) : null
    result.humidity = current.relative_humidity_2m != null ? Math.round(current.relative_humidity_2m) : null
    result.wind_kph = current.wind_speed_10m != null ? Math.round(current.wind_speed_10m * 10) / 10 : null
  }

  if (airQuality.status === 'fulfilled') {
    const usAqi = airQuality.value.current?.us_aqi
    result.aqi = usAqi != null ? Math.round(usAqi) : null
    result.aqi_label = aqiLabel(result.aqi)
  }

  return result
}
