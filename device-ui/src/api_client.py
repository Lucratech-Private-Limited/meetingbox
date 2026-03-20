"""
Backend API Client

Handles all communication with the MeetingBox FastAPI backend.
Aligned with the actual backend routes in services/web/.
"""

import asyncio
import json
import logging
from typing import List, Dict, Optional, AsyncIterator
from datetime import datetime

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from config import (
    BACKEND_URL,
    BACKEND_WS_URL,
    API_TIMEOUT,
    WS_RECONNECT_DELAY,
    WS_MAX_RECONNECT_ATTEMPTS,
)

logger = logging.getLogger(__name__)


class BackendClient:
    """
    Client for MeetingBox backend API.

    Route mapping (actual backend):
      Health:          GET  /health
      Start recording: POST /api/meetings/start
      Stop recording:  POST /api/meetings/stop
      Recording state: GET  /api/meetings/recording-status
      Pause:           POST /api/meetings/pause           (device route)
      Resume:          POST /api/meetings/resume           (device route)
      List meetings:   GET  /api/meetings/
      Meeting detail:  GET  /api/meetings/{id}
      Delete meeting:  DELETE /api/meetings/{id}           (device route)
      System status:   GET  /api/system/status
      Device info:     GET  /api/system/device-info        (device route)
      Settings:        GET  /api/device/settings           (device route)
      Settings update: PATCH /api/device/settings          (device route)
      WiFi scan:       GET  /api/device/wifi/scan          (device route)
      WiFi connect:    POST /api/device/wifi/connect       (device route)
      Check updates:   GET  /api/device/check-updates      (device route)
      Install update:  POST /api/device/install-update     (device route)
      WebSocket:       ws://host:port/ws
    """

    def __init__(self, base_url: str = BACKEND_URL):
        self.base_url = base_url.rstrip('/')
        self.ws_url = BACKEND_WS_URL
        self.client = httpx.AsyncClient(timeout=API_TIMEOUT)
        self.ws_connection = None
        self._ws_reconnect_attempts = 0

    async def close(self):
        await self.client.aclose()
        if self.ws_connection:
            await self.ws_connection.close()

    # ==================================================================
    # MEETINGS API
    # ==================================================================

    async def start_recording(self) -> Dict:
        """
        POST /api/meetings/start
        Returns: { session_id, status }
        """
        try:
            resp = await self.client.post(f"{self.base_url}/api/meetings/start")
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Started recording: {data.get('session_id')}")
            return data
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            raise

    async def stop_recording(self, session_id: str = None) -> Dict:
        """
        POST /api/meetings/stop
        Backend reads current session from Redis, session_id param unused.
        Returns: { session_id, status }
        """
        try:
            resp = await self.client.post(f"{self.base_url}/api/meetings/stop")
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Stopped recording: {data.get('session_id')}")
            return data
        except Exception as e:
            logger.error(f"Failed to stop recording: {e}")
            raise

    async def pause_recording(self, session_id: str) -> Dict:
        """
        POST /api/meetings/pause
        Sends pause command to audio service via Redis.
        """
        try:
            resp = await self.client.post(f"{self.base_url}/api/meetings/pause")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to pause recording: {e}")
            raise

    async def resume_recording(self, session_id: str) -> Dict:
        """
        POST /api/meetings/resume
        Sends resume command to audio service via Redis.
        """
        try:
            resp = await self.client.post(f"{self.base_url}/api/meetings/resume")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to resume recording: {e}")
            raise

    async def get_recording_status(self) -> Dict:
        """
        GET /api/meetings/recording-status
        Returns: { state, session_id }
        """
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/meetings/recording-status")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to get recording status: {e}")
            raise

    async def get_meetings(self, limit: int = 20, offset: int = 0) -> List[Dict]:
        """
        GET /api/meetings/?limit=&offset=
        Returns list of meeting dicts.
        Backend shape: { id, title, start_time, end_time, duration, status,
                         audio_path, created_at }
        We normalise to the shape the UI expects.
        """
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/meetings/",
                params={"limit": limit, "offset": offset},
            )
            resp.raise_for_status()
            meetings = resp.json()
            # Normalise: add pending_actions=0 if missing
            for m in meetings:
                m.setdefault('pending_actions', 0)
            return meetings
        except Exception as e:
            logger.error(f"Failed to fetch meetings: {e}")
            raise

    async def get_meeting_detail(self, meeting_id: str) -> Dict:
        """
        GET /api/meetings/{meeting_id}
        Backend returns: { meeting: {...}, segments: [...], summary: {...}|null,
                           local_summary: {...}|null }
        We flatten to the shape the UI expects.
        """
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/meetings/{meeting_id}")
            resp.raise_for_status()
            data = resp.json()

            # Flatten: merge meeting fields + summary into a single dict
            meeting = data.get('meeting', {})
            segments = data.get('segments', [])
            summary = data.get('summary') or data.get('local_summary') or {}

            result = {
                **meeting,
                'segments': segments,
                'summary': summary,
            }
            return result
        except Exception as e:
            logger.error(f"Failed to fetch meeting {meeting_id}: {e}")
            raise

    async def delete_meeting(self, meeting_id: str) -> None:
        """DELETE /api/meetings/{meeting_id}"""
        try:
            resp = await self.client.delete(
                f"{self.base_url}/api/meetings/{meeting_id}")
            resp.raise_for_status()
            logger.info(f"Deleted meeting: {meeting_id}")
        except Exception as e:
            logger.error(f"Failed to delete meeting {meeting_id}: {e}")
            raise

    # ==================================================================
    # SUMMARIZE API
    # ==================================================================

    async def summarize_meeting(self, meeting_id: str) -> Dict:
        """POST /api/meetings/{meeting_id}/summarize (Claude API)"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/meetings/{meeting_id}/summarize",
                timeout=300.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to summarize meeting {meeting_id}: {e}")
            raise

    async def summarize_meeting_local(self, meeting_id: str) -> Dict:
        """POST /api/meetings/{meeting_id}/summarize-local (Ollama)"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/meetings/{meeting_id}/summarize-local",
                timeout=300.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to locally summarize meeting {meeting_id}: {e}")
            raise

    # ==================================================================
    # ACTIONS API
    # ==================================================================

    async def get_actions(self, meeting_id: str) -> List[Dict]:
        """GET /api/meetings/{meeting_id}/actions"""
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/meetings/{meeting_id}/actions")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to get actions for meeting {meeting_id}: {e}")
            raise

    async def execute_action(self, action_id: str) -> Dict:
        """POST /api/actions/{action_id}/execute"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/actions/{action_id}/execute",
                timeout=120.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to execute action {action_id}: {e}")
            raise

    async def dismiss_action(self, action_id: str) -> Dict:
        """POST /api/actions/{action_id}/dismiss"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/actions/{action_id}/dismiss")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to dismiss action {action_id}: {e}")
            raise

    # ==================================================================
    # SETTINGS API (device route)
    # ==================================================================

    async def get_settings(self) -> Dict:
        """GET /api/device/settings"""
        try:
            resp = await self.client.get(f"{self.base_url}/api/device/settings")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch settings: {e}")
            raise

    async def update_settings(self, settings: Dict) -> Dict:
        """PATCH /api/device/settings"""
        try:
            resp = await self.client.patch(
                f"{self.base_url}/api/device/settings", json=settings)
            resp.raise_for_status()
            logger.info(f"Updated settings: {settings}")
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            raise

    # ==================================================================
    # INTEGRATIONS API
    # ==================================================================

    async def get_integrations(self) -> List[Dict]:
        """GET /api/device/integrations"""
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/device/integrations")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch integrations: {e}")
            raise

    async def get_integration_auth_url(self, integration_id: str) -> str:
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/device/integrations/{integration_id}/auth-url")
            resp.raise_for_status()
            data = resp.json()
            return data.get('auth_url') or data.get('url') or ''
        except Exception as e:
            logger.error(f"Failed to get auth URL for {integration_id}: {e}")
            raise

    async def disconnect_integration(self, integration_id: str) -> None:
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/integrations/{integration_id}/disconnect")
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to disconnect {integration_id}: {e}")
            raise

    # ==================================================================
    # SYSTEM API
    # ==================================================================

    async def get_system_info(self) -> Dict:
        """
        GET /api/system/device-info
        Returns device-level info (name, firmware, WiFi, storage, uptime).
        Falls back to /api/system/status if device-info not available.
        """
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/system/device-info")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError:
            # Fallback: use /api/system/status and normalise
            try:
                resp2 = await self.client.get(
                    f"{self.base_url}/api/system/status")
                resp2.raise_for_status()
                raw = resp2.json().get('system', {})
                return {
                    'device_name': 'MeetingBox',
                    'firmware_version': '1.0.0',
                    'ip_address': '',
                    'wifi_ssid': '',
                    'wifi_signal': 0,
                    'storage_used': int(raw.get('disk_used_gb', 0) * (1024**3)),
                    'storage_total': int(raw.get('disk_total_gb', 1) * (1024**3)),
                    'uptime': 0,
                    'meetings_count': 0,
                }
            except Exception:
                raise
        except Exception as e:
            logger.error(f"Failed to fetch system info: {e}")
            raise

    async def check_for_updates(self) -> Dict:
        """GET /api/device/check-updates"""
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/device/check-updates")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to check for updates: {e}")
            raise

    async def install_update(self) -> Dict:
        """POST /api/device/install-update"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/install-update")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to install update: {e}")
            raise

    # ==================================================================
    # WIFI API (device route)
    # ==================================================================

    async def get_wifi_networks(self) -> List[Dict]:
        """GET /api/device/wifi/scan"""
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/device/wifi/scan")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to scan WiFi: {e}")
            raise

    async def connect_wifi(self, ssid: str, password: str = None) -> Dict:
        """POST /api/device/wifi/connect"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/wifi/connect",
                json={"ssid": ssid, "password": password},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to connect to WiFi {ssid}: {e}")
            raise

    async def disconnect_wifi(self) -> None:
        """POST /api/device/wifi/disconnect"""
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/device/wifi/disconnect")
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to disconnect WiFi: {e}")
            raise

    # ==================================================================
    # WEBSOCKET (Real-time events)
    # ==================================================================

    async def subscribe_events(self) -> AsyncIterator[Dict]:
        """
        Subscribe to real-time events from backend via WebSocket at /ws.
        Auto-reconnects on disconnect.
        """
        while True:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    logger.info("WebSocket connected")
                    self._ws_reconnect_attempts = 0
                    self.ws_connection = ws

                    async for message in ws:
                        try:
                            event = json.loads(message)
                            if 'type' in event:
                                yield event
                            elif 'segment_num' in event:
                                yield {'type': 'audio_segment', 'data': event}
                            else:
                                yield {'type': 'unknown', 'data': event}
                        except json.JSONDecodeError:
                            continue

            except ConnectionClosed:
                logger.warning("WebSocket closed, reconnecting…")
                self.ws_connection = None
                await self._handle_reconnect()
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self.ws_connection = None
                await self._handle_reconnect()

    async def _handle_reconnect(self):
        self._ws_reconnect_attempts += 1
        if self._ws_reconnect_attempts > WS_MAX_RECONNECT_ATTEMPTS:
            logger.error("Max WS reconnect attempts reached")
            raise ConnectionError("Failed to reconnect to backend WebSocket")
        delay = min(WS_RECONNECT_DELAY * (2 ** self._ws_reconnect_attempts), 60)
        logger.info(f"Reconnecting WS in {delay}s (attempt {self._ws_reconnect_attempts})")
        await asyncio.sleep(delay)

    # ==================================================================
    # HEALTH CHECK
    # ==================================================================

    async def health_check(self) -> bool:
        """
        GET /health  (note: no /api prefix)
        Returns True if backend is up.
        """
        try:
            resp = await self.client.get(
                f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

