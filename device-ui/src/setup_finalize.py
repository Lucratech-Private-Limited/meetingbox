"""Write first-boot completion marker and optional API notify (shared by onboarding)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from config import setup_complete_marker_paths_for_write

logger = logging.getLogger(__name__)


def write_local_setup_complete_marker(
    wifi_ssid: str,
    device_name: str,
    onboarding_flow: str = "wifi_on_device_v1",
    extra: Optional[dict] = None,
) -> bool:
    """Write `.setup_complete` JSON everywhere the UI and server look."""
    meta: dict[str, Any] = {
        "version": 1,
        "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "device_name": (device_name or "MeetingBox").strip(),
        "wifi_ssid": (wifi_ssid or "").strip(),
        "onboarding_flow": (onboarding_flow or "wifi_on_device_v1").strip(),
    }
    if extra:
        meta.update(extra)
    text = json.dumps(meta, indent=2)
    any_ok = False
    for path in setup_complete_marker_paths_for_write():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            any_ok = True
        except OSError as e:
            logger.debug("Local setup marker skip %s: %s", path, e)
    return any_ok


async def post_setup_complete_safe(backend, wifi_ssid: str, onboarding_flow: str) -> bool:
    try:
        await backend.post_setup_complete(
            wifi_ssid=wifi_ssid,
            onboarding_flow=onboarding_flow,
        )
        return True
    except Exception as e:
        logger.error("post_setup_complete failed: %s", e)
        return False
