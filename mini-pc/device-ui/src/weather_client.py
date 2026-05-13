"""
Weather client for the device UI.

Design goals:
- Device-direct (no backend involvement) so privacy mode users can keep this
  feature off entirely without backend changes.
- No API keys: uses Open-Meteo for forecast + geocoding (free, no signup).
- Auto-detect location once via ipapi.co; users can override the city via
  the Settings → Display → Weather Location dialog.
- Persist resolved (city, lat, lon) to disk so we don't re-geocode every boot.
- Resilient: surface a stale cached reading if the network blips.

Usage from a Kivy screen::

    from weather_client import get_weather_client
    wc = get_weather_client()
    wc.start(refresh_seconds=900)   # refresh every 15 minutes
    wc.subscribe(self._on_weather)  # callback on every successful refresh

    def _on_weather(self, snapshot):
        # snapshot = {"city": "...", "temp_c": 28.4, "label": "Sunny", "icon": "sun"}
        ...
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Optional

import httpx

from async_helper import run_async
from config import resolve_device_config_dir

logger = logging.getLogger(__name__)


_WEATHER_FILE_NAME = "weather_location.json"

# Anonymous IP geolocation (HTTPS, ~1k req/day per IP).
_IPAPI_URL = "https://ipapi.co/json/"

_OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
_OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"

# Sensible default if every network call fails on first boot. Matches the
# existing DISPLAY_TIMEZONE default ("Asia/Kolkata") so the experience stays
# coherent rather than landing on lat/lon (0, 0) somewhere in the ocean.
_DEFAULT_CITY = "Bengaluru"
_DEFAULT_LAT = 12.97
_DEFAULT_LON = 77.59

_HTTP_TIMEOUT = 10.0


# --- WMO weather code → human label + small icon key -----------------------
# Open-Meteo returns codes from the WMO weather-code list. We collapse the 30+
# codes into 5 buckets that map to icons we actually ship.
def _decode_weather_code(code: int) -> tuple[str, str]:
    if code == 0:
        return ("Sunny", "sun")
    if code in (1, 2):
        return ("Partly cloudy", "cloud")
    if code == 3:
        return ("Cloudy", "cloud")
    if code in (45, 48):
        return ("Foggy", "cloud")
    if 51 <= code <= 67 or 80 <= code <= 82:
        return ("Rainy", "rain")
    if 71 <= code <= 77 or 85 <= code <= 86:
        return ("Snowy", "snow")
    if 95 <= code <= 99:
        return ("Thunderstorm", "thunder")
    return ("--", "cloud")


@dataclass
class WeatherSnapshot:
    """One forecast tick. Subscribers receive this object on refresh."""

    city: str
    temp_c: float
    label: str
    icon: str  # short key: "sun" | "cloud" | "rain" | "snow" | "thunder"
    fetched_at: float  # epoch seconds

    def is_stale(self, max_age_s: float = 3600.0) -> bool:
        return (time.time() - self.fetched_at) > max_age_s


class WeatherClient:
    """Singleton-ish weather client. Construct via :func:`get_weather_client`.

    - ``snapshot`` holds the latest reading (or None until the first refresh
      finishes). UI consumers should display "—" until then.
    - ``subscribe(cb)`` registers a callback fired once after every successful
      refresh on the asyncio loop thread; consumers should marshal back to the
      Kivy thread via ``Clock.schedule_once``.
    """

    def __init__(self) -> None:
        self.snapshot: Optional[WeatherSnapshot] = None
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[WeatherSnapshot], None]] = []
        self._refresh_task = None
        self._refresh_seconds = 900
        self._location: Optional[dict] = None
        self._location_path = self._resolve_location_path()
        self._load_persisted_location()

    # ------------------------------------------------------------------
    # Persisted location
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_location_path() -> Path:
        try:
            return resolve_device_config_dir() / _WEATHER_FILE_NAME
        except Exception:
            # Last-resort fallback if config resolution itself fails (very
            # unusual): use the user's home dir so the file is at least
            # discoverable instead of disappearing into /tmp.
            return Path.home() / ".meetingbox" / _WEATHER_FILE_NAME

    def _load_persisted_location(self) -> None:
        try:
            if self._location_path.is_file():
                data = json.loads(self._location_path.read_text(encoding="utf-8"))
                if all(k in data for k in ("city", "latitude", "longitude")):
                    self._location = {
                        "city": str(data["city"]),
                        "latitude": float(data["latitude"]),
                        "longitude": float(data["longitude"]),
                    }
                    logger.info(
                        "Weather location loaded from disk: %s (%.3f, %.3f)",
                        self._location["city"],
                        self._location["latitude"],
                        self._location["longitude"],
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load %s: %s", self._location_path, exc)

    def _persist_location(self) -> None:
        if not self._location:
            return
        try:
            self._location_path.parent.mkdir(parents=True, exist_ok=True)
            self._location_path.write_text(
                json.dumps(self._location, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Could not write %s: %s", self._location_path, exc)

    @property
    def location(self) -> Optional[dict]:
        return dict(self._location) if self._location else None

    # ------------------------------------------------------------------
    # Subscribers
    # ------------------------------------------------------------------
    def subscribe(self, callback: Callable[[WeatherSnapshot], None]) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)
        # Replay last reading so a late subscriber gets the current value.
        snap = self.snapshot
        if snap is not None:
            try:
                callback(snap)
            except Exception:  # noqa: BLE001
                logger.debug("weather subscriber raised on replay", exc_info=True)

    def unsubscribe(self, callback: Callable[[WeatherSnapshot], None]) -> None:
        with self._lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    def _publish(self, snap: WeatherSnapshot) -> None:
        with self._lock:
            self.snapshot = snap
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(snap)
            except Exception:  # noqa: BLE001
                logger.debug("weather subscriber raised", exc_info=True)

    # ------------------------------------------------------------------
    # Public refresh / start
    # ------------------------------------------------------------------
    def start(self, refresh_seconds: int = 900) -> None:
        """Kick off the periodic refresh task on the shared asyncio loop.

        Idempotent — repeated calls won't spawn additional refresh loops.
        The first refresh fires immediately (not after the delay) so the UI
        gets a value as soon as possible. The new ``refresh_seconds`` value
        takes effect on the next loop iteration (no cancel-and-relaunch
        thrash if the value didn't actually change).
        """
        self._refresh_seconds = max(60, int(refresh_seconds))
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = run_async(self._refresh_loop())

    def refresh_now(self) -> None:
        """One-shot async refresh — useful after the user changes the city."""
        run_async(self._refresh_once())

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await self._refresh_once()
            except Exception:  # noqa: BLE001
                logger.debug("weather refresh loop iteration failed", exc_info=True)
            await asyncio.sleep(self._refresh_seconds)

    async def _refresh_once(self) -> None:
        loc = self._location or await self._auto_detect_location()
        if not loc:
            return
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(
                    _OPEN_METEO_FORECAST,
                    params={
                        "latitude": loc["latitude"],
                        "longitude": loc["longitude"],
                        "current": "temperature_2m,weather_code",
                        "temperature_unit": "celsius",
                        "timezone": "auto",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Open-Meteo forecast failed: %s", exc)
            return
        cur = (data or {}).get("current") or {}
        try:
            temp_c = float(cur.get("temperature_2m"))
        except (TypeError, ValueError):
            return
        try:
            code = int(cur.get("weather_code") or 0)
        except (TypeError, ValueError):
            code = 0
        label, icon = _decode_weather_code(code)
        snap = WeatherSnapshot(
            city=str(loc["city"]),
            temp_c=temp_c,
            label=label,
            icon=icon,
            fetched_at=time.time(),
        )
        self._publish(snap)

    # ------------------------------------------------------------------
    # Location resolution
    # ------------------------------------------------------------------
    async def _auto_detect_location(self) -> Optional[dict]:
        """Resolve location via ipapi.co, persist, and return a dict.

        Falls back to ``_DEFAULT_CITY`` / ``_DEFAULT_LAT`` / ``_DEFAULT_LON``
        when the IP service errors out so the UI is never permanently blank.
        """
        loc: Optional[dict] = None
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(_IPAPI_URL)
                resp.raise_for_status()
                data = resp.json() or {}
                lat = data.get("latitude")
                lon = data.get("longitude")
                city = (data.get("city") or "").strip()
                if lat is not None and lon is not None:
                    loc = {
                        "city": city or "Unknown",
                        "latitude": float(lat),
                        "longitude": float(lon),
                    }
        except Exception as exc:  # noqa: BLE001
            logger.warning("ipapi.co lookup failed: %s", exc)

        if loc is None:
            loc = {
                "city": _DEFAULT_CITY,
                "latitude": _DEFAULT_LAT,
                "longitude": _DEFAULT_LON,
            }
            logger.info("Falling back to default weather location: %s", _DEFAULT_CITY)

        self._location = loc
        self._persist_location()
        return loc

    async def set_city(self, city_name: str) -> Optional[dict]:
        """Resolve ``city_name`` via Open-Meteo geocoding, persist, refresh.

        Returns the resolved location dict (with city/latitude/longitude) or
        ``None`` if nothing matched. UI callers should treat ``None`` as
        "couldn't find that city — try again".
        """
        name = (city_name or "").strip()
        if not name:
            return None
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(
                    _OPEN_METEO_GEOCODE,
                    params={
                        "name": name,
                        "count": 1,
                        "language": "en",
                        "format": "json",
                    },
                )
                resp.raise_for_status()
                data = resp.json() or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Open-Meteo geocoding failed for %r: %s", name, exc)
            return None
        results = data.get("results") or []
        if not results:
            return None
        first = results[0]
        try:
            self._location = {
                "city": str(first.get("name") or name),
                "latitude": float(first["latitude"]),
                "longitude": float(first["longitude"]),
            }
        except (KeyError, TypeError, ValueError):
            return None
        self._persist_location()
        await self._refresh_once()
        return dict(self._location)


# Module-level singleton. Most callers should use ``get_weather_client()``.
_singleton: Optional[WeatherClient] = None


def get_weather_client() -> WeatherClient:
    global _singleton
    if _singleton is None:
        _singleton = WeatherClient()
    return _singleton


__all__ = [
    "WeatherClient",
    "WeatherSnapshot",
    "get_weather_client",
]
