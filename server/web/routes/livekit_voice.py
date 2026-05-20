"""LiveKit voice connect endpoint.

The device calls POST /api/voice/livekit/connect with its paired-device Bearer
token (or a dashboard JWT). The server mints a short-lived LiveKit room token
and returns the LiveKit signaling URL + room name. The device then joins that
room via the LiveKit Python SDK.

A separate LiveKit Agents worker process (the `livekit-agent` service) is
running with automatic dispatch enabled — as soon as the device joins, the
worker spawns an agent into the same room and runs the OpenAI Realtime model.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_actor

logger = logging.getLogger(__name__)
router = APIRouter(tags=["voice"])


_LIVEKIT_URL_ENV = "LIVEKIT_URL"
_LIVEKIT_KEY_ENV = "LIVEKIT_API_KEY"
_LIVEKIT_SECRET_ENV = "LIVEKIT_API_SECRET"
_ROOM_TTL_SECONDS = 30 * 60          # device token validity
_PARTICIPANT_TTL_SECONDS = 8 * 60 * 60


def _livekit_enabled() -> bool:
    return bool(
        os.getenv(_LIVEKIT_URL_ENV, "").strip()
        and os.getenv(_LIVEKIT_KEY_ENV, "").strip()
        and os.getenv(_LIVEKIT_SECRET_ENV, "").strip()
    )


def _mint_room_token(identity: str, room: str) -> str:
    """Build a short-lived LiveKit join token for a participant."""
    # Imported lazily so the route module imports cleanly even if the
    # livekit-api package isn't installed in CI / test environments.
    from livekit import api as lkapi  # type: ignore

    grants = lkapi.VideoGrants(
        room=room,
        room_join=True,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = (
        lkapi.AccessToken(
            os.environ[_LIVEKIT_KEY_ENV].strip(),
            os.environ[_LIVEKIT_SECRET_ENV].strip(),
        )
        .with_identity(identity)
        .with_name(identity)
        .with_ttl(timedelta(seconds=_PARTICIPANT_TTL_SECONDS))
        .with_grants(grants)
    )
    return token.to_jwt()


@router.post("/livekit/connect")
async def livekit_connect(actor: dict = Depends(get_current_actor)) -> dict:
    """
    Mint a LiveKit join token for the calling device/user.

    Response:
      url:    LiveKit signaling URL (ws[s]://...).
      token:  Signed participant JWT.
      room:   Unique room name (per-user + nonce).
      identity: The participant identity that the agent worker will look up.
    """
    if not _livekit_enabled():
        raise HTTPException(
            status_code=503,
            detail="LiveKit voice is not configured on this server.",
        )

    uid = str(actor["user"]["id"])
    # Per-user nonce keeps each session in its own room so two devices for the
    # same user never collide and the agent worker sees one device per room.
    nonce = secrets.token_hex(4)
    room = f"voice-{uid}-{nonce}"
    identity = f"device-{uid}-{nonce}"

    try:
        token = _mint_room_token(identity, room)
    except Exception as exc:
        logger.exception("Failed to mint LiveKit token for user=%s", uid)
        raise HTTPException(
            status_code=500,
            detail=f"Could not mint LiveKit token: {exc!s}",
        ) from exc

    return {
        "url": os.environ[_LIVEKIT_URL_ENV].strip(),
        "token": token,
        "room": room,
        "identity": identity,
        "ttl_seconds": _ROOM_TTL_SECONDS,
    }
