"""
Text-to-Speech route — proxy to OpenAI TTS so the device UI never needs
to hold the OpenAI API key locally.

POST /api/tts/speak
  Body : {"text": "...", "voice": "shimmer"}
  Auth : valid device token or user JWT (Bearer)
  Return: audio/pcm  — raw 16-bit signed LE PCM, 24 000 Hz, 1 channel
          (aplay -r 24000 -f S16_LE -c 1 <file>)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth import get_optional_actor

router = APIRouter()
logger = logging.getLogger("meetingbox.tts")

_ALLOWED_VOICES = frozenset({
    "alloy", "ash", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer",
})


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    voice: str = Field(default="shimmer")


def _default_voice() -> str:
    v = (os.getenv("OPENAI_TTS_VOICE") or "shimmer").strip().lower()
    return v if v in _ALLOWED_VOICES else "shimmer"


@router.post("/speak")
async def tts_speak(
    body: TTSRequest,
    current_actor: Optional[dict] = Depends(get_optional_actor),
):
    """
    Synthesise speech via OpenAI TTS and return raw PCM audio.
    Requires a paired device token or a logged-in user JWT.
    Falls back gracefully: returns 503 when OPENAI_API_KEY is absent.
    """
    if current_actor is None:
        raise HTTPException(status_code=401, detail="Authentication required.")

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OpenAI API key not configured on the server.",
        )

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty.")

    voice = (body.voice or "").strip().lower()
    if voice not in _ALLOWED_VOICES:
        voice = _default_voice()

    model = (os.getenv("OPENAI_TTS_MODEL") or "tts-1").strip()

    def _generate() -> bytes:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.audio.speech.create(
            model=model,
            voice=voice,           # type: ignore[arg-type]
            input=text[:4000],
            response_format="pcm", # 24 000 Hz, S16_LE, mono — plays directly with aplay
        )
        return response.content

    try:
        audio_bytes: bytes = await asyncio.to_thread(_generate)
    except Exception as exc:
        logger.error("OpenAI TTS failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"TTS generation failed: {exc}")

    return Response(content=audio_bytes, media_type="audio/pcm")
