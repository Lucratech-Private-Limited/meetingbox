"""
Weather route — fetches current conditions from the Open-Meteo API (no API key required).

Location is configured via env vars:
  WEATHER_LAT  (float, default 12.9716 = Bengaluru)
  WEATHER_LON  (float, default 77.5946 = Bengaluru)
  WEATHER_CITY (string, default "Bengaluru")

Returns:
  { temperature, feels_like, condition, high, low, humidity, wind_kph, aqi, city }

AQI is fetched from the Open-Meteo Air Quality API (no key required).
"""

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends

from auth import get_optional_user

logger = logging.getLogger("meetingbox.weather")

router = APIRouter()

_WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Icy fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Violent showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def _aqi_label(us_aqi: Optional[float]) -> Optional[str]:
    if us_aqi is None:
        return None
    v = int(us_aqi)
    if v <= 50:
        return "Good"
    if v <= 100:
        return "Moderate"
    if v <= 150:
        return "Unhealthy (Sensitive)"
    if v <= 200:
        return "Unhealthy"
    if v <= 300:
        return "Very Unhealthy"
    return "Hazardous"


@router.get("/weather")
async def get_weather(
    _current_user: Optional[dict] = Depends(get_optional_user),
) -> dict:
    lat = float(os.getenv("WEATHER_LAT", "12.9716"))
    lon = float(os.getenv("WEATHER_LON", "77.5946"))
    city = os.getenv("WEATHER_CITY", "Bengaluru")

    weather_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,apparent_temperature,weathercode,relative_humidity_2m,wind_speed_10m"
        "&daily=temperature_2m_max,temperature_2m_min"
        "&timezone=auto&forecast_days=1"
    )
    aqi_url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={lat}&longitude={lon}"
        "&current=us_aqi"
    )

    result: dict = {
        "city": city,
        "temperature": None,
        "feels_like": None,
        "condition": "Unknown",
        "high": None,
        "low": None,
        "humidity": None,
        "wind_kph": None,
        "aqi": None,
        "aqi_label": None,
    }

    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            resp = await client.get(weather_url)
            resp.raise_for_status()
            data = resp.json()
            curr = data.get("current", {})
            daily = data.get("daily", {})

            temp = curr.get("temperature_2m")
            result["temperature"] = round(temp) if temp is not None else None

            feels = curr.get("apparent_temperature")
            result["feels_like"] = round(feels) if feels is not None else None

            code = curr.get("weathercode")
            result["condition"] = _WMO_CODES.get(int(code), "Unknown") if code is not None else "Unknown"

            highs = daily.get("temperature_2m_max", [])
            lows = daily.get("temperature_2m_min", [])
            result["high"] = round(highs[0]) if highs else None
            result["low"] = round(lows[0]) if lows else None

            hum = curr.get("relative_humidity_2m")
            result["humidity"] = int(hum) if hum is not None else None

            wind = curr.get("wind_speed_10m")
            result["wind_kph"] = round(wind, 1) if wind is not None else None
        except Exception as exc:
            logger.warning("Weather fetch failed: %s", exc)

        try:
            aqi_resp = await client.get(aqi_url)
            aqi_resp.raise_for_status()
            aqi_data = aqi_resp.json()
            us_aqi = aqi_data.get("current", {}).get("us_aqi")
            result["aqi"] = int(us_aqi) if us_aqi is not None else None
            result["aqi_label"] = _aqi_label(us_aqi)
        except Exception as exc:
            logger.warning("AQI fetch failed: %s", exc)

    return result
