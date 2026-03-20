"""
Mock Backend Client

For testing device UI without real backend running.
Set environment variable: MOCK_BACKEND=1
"""

import asyncio
import logging
from typing import List, Dict, Optional, AsyncIterator
from datetime import datetime, timedelta
import random

logger = logging.getLogger(__name__)


class MockBackendClient:
    """
    Mock implementation of BackendClient for testing.
    Simulates backend behavior without actual API calls.
    """

    def __init__(self, base_url: str = None):
        self.current_recording = None
        self.meetings = self._generate_mock_meetings()
        self._settings = {
            'device_name': 'Conference Room A',
            'timezone': 'America/New_York',
            'auto_delete_days': 'never',
            'brightness': 'high',
            'screen_timeout': 'never',
            'privacy_mode': False,
            'auto_record': False,
        }
        logger.info("Using MOCK backend client")

    async def close(self):
        pass

    # ==================================================================
    # MOCK DATA
    # ==================================================================

    def _generate_mock_meetings(self) -> List[Dict]:
        now = datetime.now()
        return [
            {
                "id": "1",
                "title": "Q4 Planning Session",
                "start_time": (now - timedelta(hours=2)).isoformat(),
                "end_time": (now - timedelta(hours=1, minutes=15)).isoformat(),
                "duration": 2700,
                "status": "completed",
                "pending_actions": 3,
                "summary": {
                    "summary": "Team discussed Q4 priorities and resource allocation.",
                    "action_items": [
                        {"task": "Sarah: Update budget proposal by Friday",
                         "assignee": "Sarah Chen", "due_date": "2026-02-14",
                         "completed": False},
                        {"task": "Mike: Schedule engineering review",
                         "assignee": "Mike Johnson", "due_date": "2026-02-18",
                         "completed": False},
                        {"task": "Lisa: Send revised proposal to client",
                         "assignee": "Lisa Park", "due_date": "2026-02-20",
                         "completed": True},
                    ],
                    "decisions": [
                        "Delay Feature X to Q1 2027",
                        "Hire 2 additional senior engineers",
                    ],
                    "topics": ["Q4 Planning", "Resource Allocation"],
                    "sentiment": "Productive and collaborative",
                },
            },
            {
                "id": "2",
                "title": "Client Proposal Review - Acme Corp",
                "start_time": (now - timedelta(days=1)).isoformat(),
                "end_time": (now - timedelta(days=1) + timedelta(hours=1)).isoformat(),
                "duration": 3600,
                "status": "completed",
                "pending_actions": 0,
                "summary": {
                    "summary": "Reviewed pricing and timeline for Acme Corp proposal.",
                    "action_items": [
                        {"task": "John: Finalize proposal by Wednesday",
                         "assignee": "John Martinez", "due_date": "2026-02-12",
                         "completed": True},
                    ],
                    "decisions": ["Pricing set at $52,000", "Timeline: 10 weeks"],
                    "topics": ["Proposal", "Pricing"],
                    "sentiment": "Focused and decisive",
                },
            },
            {
                "id": "3",
                "title": "Team Standup",
                "start_time": (now - timedelta(days=2)).isoformat(),
                "end_time": (now - timedelta(days=2) + timedelta(minutes=15)).isoformat(),
                "duration": 900,
                "status": "completed",
                "pending_actions": 0,
                "summary": {
                    "summary": "Daily standup covering progress and blockers.",
                    "action_items": [],
                    "decisions": [],
                    "topics": ["Standup"],
                    "sentiment": "Brief and informative",
                },
            },
        ]

    # ==================================================================
    # MEETINGS
    # ==================================================================

    async def start_recording(self) -> Dict:
        await asyncio.sleep(0.5)
        self.current_recording = {
            "session_id": f"mock_{datetime.now().timestamp()}",
            "status": "recording",
            "start_time": datetime.now().isoformat(),
        }
        logger.info(f"[MOCK] Started recording: {self.current_recording['session_id']}")
        return self.current_recording

    async def stop_recording(self, session_id: str) -> Dict:
        await asyncio.sleep(0.5)
        result = {
            "status": "processing",
            "session_id": session_id,
            "duration": random.randint(300, 3600),
        }
        self.current_recording = None
        return result

    async def pause_recording(self, session_id: str) -> Dict:
        await asyncio.sleep(0.3)
        return {"status": "paused", "session_id": session_id}

    async def resume_recording(self, session_id: str) -> Dict:
        await asyncio.sleep(0.3)
        return {"status": "recording", "session_id": session_id}

    async def get_recording_status(self) -> Dict:
        await asyncio.sleep(0.1)
        if self.current_recording:
            return {"state": "recording", "session_id": self.current_recording['session_id']}
        return {"state": "idle", "session_id": None}

    async def get_meetings(self, limit: int = 20, offset: int = 0) -> List[Dict]:
        await asyncio.sleep(0.3)
        return self.meetings[offset:offset + limit]

    async def get_meeting_detail(self, meeting_id: str) -> Dict:
        await asyncio.sleep(0.3)
        meeting = next((m for m in self.meetings if m['id'] == meeting_id), None)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found")
        meeting['segments'] = [
            {"segment_num": 1, "start_time": 0, "end_time": 5,
             "text": "So let's talk about Q4 roadmap.", "speaker_id": "1"},
        ]
        return meeting

    async def delete_meeting(self, meeting_id: str) -> None:
        await asyncio.sleep(0.3)
        self.meetings = [m for m in self.meetings if m['id'] != meeting_id]

    # ==================================================================
    # SETTINGS
    # ==================================================================

    async def get_settings(self) -> Dict:
        await asyncio.sleep(0.2)
        return dict(self._settings)

    async def update_settings(self, settings: Dict) -> Dict:
        await asyncio.sleep(0.3)
        self._settings.update(settings)
        logger.info(f"[MOCK] Updated settings: {settings}")
        return self._settings

    # ==================================================================
    # INTEGRATIONS
    # ==================================================================

    async def get_integrations(self) -> List[Dict]:
        await asyncio.sleep(0.2)
        return [
            {"id": "gmail", "name": "Gmail", "connected": False},
            {"id": "calendar", "name": "Google Calendar", "connected": True},
        ]

    async def get_integration_auth_url(self, integration_id: str) -> str:
        await asyncio.sleep(0.2)
        return f"https://mock-oauth.example.com/auth?integration={integration_id}"

    async def disconnect_integration(self, integration_id: str) -> None:
        await asyncio.sleep(0.3)

    # ==================================================================
    # SYSTEM
    # ==================================================================

    async def get_system_info(self) -> Dict:
        await asyncio.sleep(0.2)
        return {
            "device_name": self._settings.get('device_name', 'Conference Room A'),
            "serial_number": "MB-2026-00001234",
            "firmware_version": "1.2.5",
            "ip_address": "192.168.1.145",
            "wifi_ssid": "Office-Network",
            "wifi_signal": 85,
            "storage_used": 58_000_000_000,
            "storage_total": 512_000_000_000,
            "uptime": 172800,
            "meetings_count": len(self.meetings),
        }

    async def check_for_updates(self) -> Dict:
        await asyncio.sleep(1.5)
        return {
            "update_available": False,
            "current_version": "1.2.5",
            "latest_version": None,
            "release_notes": None,
        }

    async def install_update(self) -> Dict:
        await asyncio.sleep(2.0)
        return {"status": "success"}

    # ==================================================================
    # WIFI
    # ==================================================================

    async def get_wifi_networks(self) -> List[Dict]:
        await asyncio.sleep(1.0)
        return [
            {"ssid": "Office-Network", "signal_strength": 85, "security": "wpa2", "connected": True},
            {"ssid": "Office-Guest", "signal_strength": 70, "security": "wpa2", "connected": False},
            {"ssid": "Conference-5G", "signal_strength": 60, "security": "wpa2", "connected": False},
        ]

    async def connect_wifi(self, ssid: str, password: str = None) -> Dict:
        await asyncio.sleep(2.0)
        return {"status": "connected", "message": f"Connected to {ssid}"}

    async def disconnect_wifi(self) -> None:
        await asyncio.sleep(0.5)

    # ==================================================================
    # WEBSOCKET (MOCK)
    # ==================================================================

    async def subscribe_events(self) -> AsyncIterator[Dict]:
        logger.info("[MOCK] WebSocket connected")
        while True:
            await asyncio.sleep(5)
            etype = random.choice(["heartbeat", "transcription_update", "status_update"])
            if etype == "heartbeat":
                yield {"type": "heartbeat", "data": {"timestamp": datetime.now().isoformat()}}
            elif etype == "transcription_update" and self.current_recording:
                yield {
                    "type": "transcription_update",
                    "data": {
                        "session_id": self.current_recording['session_id'],
                        "text": random.choice([
                            "So I think we should focus on Q4 priorities first…",
                            "The engineering team has capacity concerns…",
                            "Let's schedule a follow-up meeting next week…",
                        ]),
                        "speaker_id": str(random.randint(1, 3)),
                    },
                }

    # ==================================================================
    # HEALTH
    # ==================================================================

    async def health_check(self) -> bool:
        await asyncio.sleep(0.1)
        return True
