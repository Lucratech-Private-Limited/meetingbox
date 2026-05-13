"""OpenAI Realtime voice: ephemeral client secrets and tool invocation for paired devices / users."""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

from auth import get_current_actor
from services.realtime_voice_tools import (
    REALTIME_VOICE_TOOL_DEFINITIONS,
    execute_realtime_voice_tool,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])

_REALTIME_INSTRUCTIONS = """You are MeetingBox, a concise helpful voice assistant on a tabletop device.
You have tools to search the user's saved long-term memory (Mem0) and to load their morning briefing bundle
(calendar, tasks, recent meetings, email preview, pending actions). Use tools when needed; speak clearly and briefly.
For confirmations, repeat the key fact or ask a short yes/no question."""


def _realtime_enabled() -> bool:
    return os.getenv("MEETINGBOX_REALTIME_VOICE_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _openai_api_key() -> str:
    return (os.getenv("OPENAI_API_KEY") or "").strip()


def _realtime_model() -> str:
    return (os.getenv("OPENAI_REALTIME_MODEL") or "gpt-realtime").strip()


class RealtimeSessionResponse(BaseModel):
    client_secret: str = Field(..., description="Short-lived ek_ secret for WebSocket auth")
    expires_at: int
    model: str
    session: dict


@router.post("/realtime/session", response_model=RealtimeSessionResponse)
async def create_realtime_voice_session(actor: dict = Depends(get_current_actor)):
    """
    Mint an OpenAI Realtime client secret with tools for Mem0 search and briefing context.
    Requires dashboard JWT or paired device Bearer token.
    """
    if not _realtime_enabled():
        raise HTTPException(status_code=503, detail="Realtime voice is disabled on this server.")
    api_key = _openai_api_key()
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured.")

    model = _realtime_model()
    client = OpenAI(api_key=api_key)
    try:
        created = client.realtime.client_secrets.create(
            expires_after={"anchor": "created_at", "seconds": 600},
            session={
                "type": "realtime",
                "model": model,
                "instructions": _REALTIME_INSTRUCTIONS,
                "tools": REALTIME_VOICE_TOOL_DEFINITIONS,
                "output_modalities": ["audio"],
            },
        )
    except Exception as e:
        logger.exception("OpenAI realtime client_secrets.create failed")
        raise HTTPException(
            status_code=502,
            detail=f"Could not create Realtime session: {e!s}",
        ) from e

    sess = created.session
    if hasattr(sess, "model_dump"):
        sess_dict = sess.model_dump(mode="json")
    elif hasattr(sess, "dict"):
        sess_dict = sess.dict()
    else:
        sess_dict = json.loads(sess.json()) if hasattr(sess, "json") else {}

    return RealtimeSessionResponse(
        client_secret=created.value,
        expires_at=created.expires_at,
        model=str(sess_dict.get("model") or model),
        session=sess_dict,
    )


class ToolInvokeBody(BaseModel):
    call_id: str = Field(..., min_length=1, max_length=256)
    name: str = Field(..., min_length=1, max_length=128)
    arguments: str = Field(default="{}", description="JSON object string from the model")


class ToolInvokeResponse(BaseModel):
    output: str


@router.post("/realtime/tools/invoke", response_model=ToolInvokeResponse)
async def invoke_realtime_tool(body: ToolInvokeBody, actor: dict = Depends(get_current_actor)):
    """
    Execute a server-side Realtime tool (Mem0 search or briefing bundle). Called by the device
    after `response.function_call_arguments.done` over the Realtime WebSocket.
    """
    if not _realtime_enabled():
        raise HTTPException(status_code=503, detail="Realtime voice is disabled on this server.")

    user_id = actor["user"]["id"]
    out = execute_realtime_voice_tool(
        user_id=user_id,
        actor=actor,
        name=body.name.strip(),
        arguments_json=body.arguments or "{}",
    )
    return ToolInvokeResponse(output=out)
