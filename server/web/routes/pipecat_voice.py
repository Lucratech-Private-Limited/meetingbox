"""
Server-side Pipecat AI voice pipeline for MeetingBox.

Architecture (no API key on device):
  Device mic  --raw PCM 16 kHz-->  WS /api/voice/pipecat/ws
                                    ├── OpenAIRealtimeSTTService  (server-side VAD)
                                    ├── OpenAILLMService          (GPT-4o reasoning + tools)
                                    └── OpenAITTSService          (gpt-4o-mini-tts)
  Device spkr <--raw PCM 24 kHz--

The device sends raw 16-kHz int16 mono PCM as binary WebSocket frames.
The server runs the full Pipecat pipeline; audio and JSON control events
stream back to the device.

Requires: pipecat-ai[openai]==1.2.1, websockets>=13.1
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import string
import sys
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from auth import resolve_actor_from_access_token
from services.realtime_voice_tools import execute_realtime_voice_tool

logger = logging.getLogger(__name__)
router = APIRouter(tags=["voice"])

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

_LLM_MODEL  = os.getenv("PIPECAT_LLM_MODEL",  "gpt-4o")
_TTS_MODEL  = os.getenv("PIPECAT_TTS_MODEL",  "gpt-4o-mini-tts")
_TTS_VOICE  = os.getenv("PIPECAT_TTS_VOICE",
                         os.getenv("OPENAI_REALTIME_VOICE", "shimmer"))
_STT_MODEL  = "gpt-realtime-whisper"   # low-latency streaming STT via Realtime API

_IN_RATE    = 16000    # device mic sample rate
_OUT_RATE   = 24000    # OpenAI TTS output rate
_SESSION_IDLE_S = 45.0

# ---------------------------------------------------------------------------
# Farewell detection
# ---------------------------------------------------------------------------

_PUNCT_TO_SPACE = str.maketrans({c: " " for c in string.punctuation})


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().translate(_PUNCT_TO_SPACE).split())


_FAREWELL_EXACT = frozenset({
    "bye", "bye bye", "goodbye", "good bye",
    "okay bye", "ok bye", "alright bye",
    "thanks", "thank you", "thanks bye", "thank you bye",
    "im done", "i am done", "i'm done", "all done",
    "thats all", "that's all", "thats all for now", "that's all for now",
    "thats it", "that's it", "done for now",
    "stop", "stop now", "shut up", "stop talking",
    "be quiet", "quiet", "enough", "enough already",
    "thats enough", "that's enough",
    "we're done", "we are done", "were done",
    "end session", "end the session", "session over",
    "nothing else", "nothing more", "nothing else for now",
    "exit", "close", "close session",
})

_FAREWELL_END = (
    "bye", "goodbye", "okay bye", "ok bye", "alright bye",
    "thanks bye", "thank you bye",
    "thats all", "that's all", "thats it", "that's it",
    "im done", "i'm done", "i am done", "all done",
    "we're done", "we are done", "were done",
    "end session", "end the session", "session over",
    "nothing else", "nothing more",
)


def _is_farewell(text: str) -> bool:
    n = _normalize(text)
    if not n:
        return False
    if n in _FAREWELL_EXACT:
        return True
    return any(n.endswith(m) for m in _FAREWELL_END)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are MeetingBox—a real-time voice assistant designed for fast, natural, "
    "interruption-friendly conversations. Your behavior must feel smooth, intelligent, "
    "concise, and human-like.\n\n"
    "RESPOND IMMEDIATELY:\n"
    "- Never stay silent after the user speaks. Acknowledge within 1 second.\n"
    "- If tools take time, briefly inform the user naturally: 'Checking that now.' "
    "/ 'Looking into it.' / 'One moment.' / 'Going through your schedule.'\n"
    "- Never say 'I can help you with emails, scheduling, reminders...' or "
    "'As your AI assistant...'\n\n"
    "HOW YOU TALK (speech, not typing):\n"
    "- Sound fluent and human: contractions, fragments when natural. No wind-ups "
    "like 'Certainly!', 'Great question!', 'I'd be happy to...'.\n"
    "- Keep replies 1-3 short sentences by default; expand only when asked.\n"
    "- No markdown, emojis, or bullet lists. Use flowing sentences.\n"
    "- After tools return: summarize like briefing a teammate — names, times, gist.\n"
    "- Never say 'take care', 'see you later', or similar sign-offs.\n"
    "- If the user says bye / goodbye / thanks that's all / done: give one short "
    "acknowledgment then stop.\n"
    "- If user interrupts: immediately stop and attend to the newest utterance.\n\n"
    "HANDLE AMBIGUITY:\n"
    "- READ/SUMMARIZE requests (emails, calendar, tasks): use tools immediately.\n"
    "- WRITE actions (send email, create event): ask one focused question only if "
    "a required detail is truly missing.\n"
    "- Ask one question at a time.\n\n"
    "LANGUAGE: English unless explicitly requested otherwise.\n\n"
    "Memory and context (not optional):\n"
    "- Call memory_search on topic shifts or when user asks what you remember.\n"
    "- Call memory_remember when user says remember / note that / don't forget.\n"
    "- Never invent stored facts.\n\n"
    "Tool priority:\n"
    "1) get_briefing_context — overview, schedule, inbox, tasks.\n"
    "2) memory_search — personal history, prior decisions.\n"
    "3) memory_remember — storable preferences / facts.\n"
    "4) assistant_intent — send email, schedule, reminder, complex asks.\n"
    "5) list_pending_actions / approve_pending_action / reject_pending_action.\n"
    "6) navigate_device_ui — only when user says open / show / go to a screen.\n\n"
    "For writes: state plainly what you've prepared and ask once if user wants to "
    "proceed. Say it's done only after approve_pending_action confirms it.\n\n"
    "Stay concise. One coherent reply per beat."
)

# ---------------------------------------------------------------------------
# Tool definitions in pipecat-ai 1.x FunctionSchema format
# ---------------------------------------------------------------------------

_TOOL_SPECS = [
    {
        "name": "memory_search",
        "description": (
            "Search the user's long-term Mem0 memory for facts, notes, reminders, "
            "and past context."
        ),
        "properties": {
            "query": {"type": "string", "description": "Natural language search query."},
        },
        "required": ["query"],
    },
    {
        "name": "memory_remember",
        "description": (
            "Save a stable fact the user wants remembered across sessions. "
            "Call when they say remember / note that / don't forget."
        ),
        "properties": {
            "fact": {"type": "string", "description": "One clear sentence to store."},
            "context_note": {"type": "string", "description": "Optional 1-line reason."},
        },
        "required": ["fact"],
    },
    {
        "name": "get_briefing_context",
        "description": (
            "Primary data bundle: calendar events, recent meetings, gmail_preview, mem0. "
            "Call IMMEDIATELY when user asks about schedule, email, tasks, or meetings."
        ),
        "properties": {
            "days_ahead": {"type": "integer",
                           "description": "1=today, 2=today+tomorrow, up to 14. Default 2."},
        },
        "required": [],
    },
    {
        "name": "assistant_intent",
        "description": (
            "Run the user's request through MeetingBox assistants (calendar, Gmail, tasks). "
            "Pass their exact spoken intent."
        ),
        "properties": {
            "message": {"type": "string", "description": "The user's request in natural language."},
            "meeting_id": {"type": "string",
                           "description": "Optional meeting/recording ID if explicitly relevant."},
        },
        "required": ["message"],
    },
    {
        "name": "list_pending_actions",
        "description": "List assistant actions queued for approval.",
        "properties": {},
        "required": [],
    },
    {
        "name": "approve_pending_action",
        "description": "Execute ONE queued write only after the user has clearly confirmed aloud.",
        "properties": {
            "pending_id": {"type": "string"},
            "confirmed_by_user": {"type": "boolean"},
            "confirmation_phrase": {"type": "string"},
        },
        "required": ["pending_id", "confirmed_by_user", "confirmation_phrase"],
    },
    {
        "name": "reject_pending_action",
        "description": "Cancel a queued action after the user declines.",
        "properties": {
            "pending_id": {"type": "string"},
        },
        "required": ["pending_id"],
    },
    {
        "name": "navigate_device_ui",
        "description": (
            "Open a main screen on the device. "
            "Only call when user explicitly says 'open / show / go to'."
        ),
        "properties": {
            "screen": {
                "type": "string",
                "enum": ["calendar", "emails", "home", "meetings",
                         "morning_brief", "settings", "mic_test"],
            },
        },
        "required": ["screen"],
    },
]

# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/pipecat/ws")
async def pipecat_voice_ws(websocket: WebSocket):
    """
    Device connects here to stream audio and receive TTS audio + events.
    Auth: Bearer token in the Authorization header OR ?token= query param.
    """
    token = websocket.query_params.get("token", "")
    if not token:
        auth_header = websocket.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    actor = None
    if token:
        try:
            actor = resolve_actor_from_access_token(token)
        except Exception:
            pass

    if not actor:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        await websocket.accept()
        await websocket.send_text(json.dumps({
            "type": "error", "message": "OPENAI_API_KEY not configured on server",
        }))
        await websocket.close()
        return

    await websocket.accept()
    uid = actor["user"]["id"]
    print(f"PIPECAT_WS connected user={uid}", file=sys.stderr, flush=True)

    try:
        await _run_pipecat_session(websocket, actor, api_key)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("pipecat_voice_ws unhandled error user=%s", uid)
    finally:
        print(f"PIPECAT_WS disconnected user={uid}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Pipecat pipeline session
# ---------------------------------------------------------------------------

async def _run_pipecat_session(
    device_ws: WebSocket,
    actor: dict,
    api_key: str,
) -> None:
    """Build and run one Pipecat AI pipeline session."""

    try:
        from pipecat.adapters.schemas.function_schema import FunctionSchema
        from pipecat.adapters.schemas.tools_schema import ToolsSchema
        from pipecat.frames.frames import (
            InputAudioRawFrame,
            TranscriptionFrame,
            TTSAudioRawFrame,
            TTSStartedFrame,
            TTSStoppedFrame,
            UserStartedSpeakingFrame,
            UserStoppedSpeakingFrame,
        )
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
        from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
        from pipecat.services.llm_service import FunctionCallParams
        from pipecat.services.openai.llm import OpenAILLMService
        from pipecat.services.openai.stt import OpenAIRealtimeSTTService
        from pipecat.services.openai.tts import OpenAITTSService
    except ImportError as exc:
        logger.error("pipecat-ai not installed: %s", exc)
        await _dev_json(device_ws, {
            "type": "error",
            "message": f"pipecat-ai not installed on server: {exc}",
        })
        return

    uid  = actor["user"]["id"]
    loop = asyncio.get_running_loop()
    task_ref: list[Any] = [None]   # filled in after task creation

    # ── helpers ───────────────────────────────────────────────────────────

    async def _state(state: str) -> None:
        await _dev_json(device_ws, {"type": "state", "state": state})

    async def _navigate(screen: str) -> None:
        await _dev_json(device_ws, {"type": "navigate", "screen": screen})

    async def _interrupt() -> None:
        await _dev_json(device_ws, {"type": "interrupt"})

    # ── custom output processor ───────────────────────────────────────────

    class _DeviceSink(FrameProcessor):
        """Intercepts frames of interest and forwards to device WebSocket."""

        async def process_frame(self, frame: Any, direction: FrameDirection) -> None:
            if isinstance(frame, TTSAudioRawFrame):
                try:
                    await device_ws.send_bytes(frame.audio)
                except Exception:
                    pass

            elif isinstance(frame, UserStartedSpeakingFrame):
                await _interrupt()
                await _state("listening")

            elif isinstance(frame, UserStoppedSpeakingFrame):
                await _state("thinking")

            elif isinstance(frame, TTSStartedFrame):
                await _state("speaking")

            elif isinstance(frame, TTSStoppedFrame):
                await _state("listening")

            elif isinstance(frame, TranscriptionFrame):
                text = (getattr(frame, "text", None) or "").strip()
                if text:
                    print(
                        f"PIPECAT_TRANSCRIPT user={uid} text={text[:120]}",
                        file=sys.stderr, flush=True,
                    )
                    if _is_farewell(text) and task_ref[0] is not None:
                        logger.info("PIPECAT_WS farewell %r user=%s", text, uid)
                        await task_ref[0].cancel()

            await self.push_frame(frame, direction)

    device_sink = _DeviceSink()

    # ── tool schema ───────────────────────────────────────────────────────

    schemas = [
        FunctionSchema(
            name=spec["name"],
            description=spec["description"],
            properties=spec["properties"],
            required=spec["required"],
        )
        for spec in _TOOL_SPECS
    ]
    tools = ToolsSchema(standard_tools=schemas)

    # ── LLM context ───────────────────────────────────────────────────────

    context = LLMContext(tools=tools)

    # ── services ──────────────────────────────────────────────────────────

    llm = OpenAILLMService(
        api_key=api_key,
        settings=OpenAILLMService.Settings(
            model=_LLM_MODEL,
            system_instruction=_SYSTEM_PROMPT,
        ),
    )

    tts = OpenAITTSService(
        api_key=api_key,
        settings=OpenAITTSService.Settings(
            model=_TTS_MODEL,
            voice=_TTS_VOICE,
            instructions=(
                "Warm, focused, professional tone. Moderate pace. "
                "Crisp consonants. Natural pauses between sentences."
            ),
        ),
    )

    # Server-side VAD: OpenAI Realtime API handles speech boundaries.
    # turn_detection=None  → use server's default server_vad (not False / local VAD)
    # should_interrupt=True → STT sends InterruptionFrame when user starts speaking
    stt = OpenAIRealtimeSTTService(
        api_key=api_key,
        turn_detection=None,
        should_interrupt=True,
        settings=OpenAIRealtimeSTTService.Settings(
            model=_STT_MODEL,
            noise_reduction="far_field",
        ),
    )

    # ── function call handlers ────────────────────────────────────────────

    def _make_handler(tool_name: str):
        async def _handler(params: FunctionCallParams) -> None:
            args      = params.arguments or {}
            args_json = json.dumps(args)

            print(
                f"PIPECAT_TOOL user={uid} name={tool_name} args={args_json[:200]}",
                file=sys.stderr, flush=True,
            )

            if tool_name == "navigate_device_ui":
                screen = args.get("screen", "")
                if screen:
                    await _navigate(screen)
                result_str = json.dumps({"ok": True, "device_navigate": screen})
            else:
                try:
                    result_str = await loop.run_in_executor(
                        None,
                        functools.partial(
                            execute_realtime_voice_tool,
                            user_id=uid,
                            actor=actor,
                            name=tool_name,
                            arguments_json=args_json,
                        ),
                    )
                except Exception as exc:
                    logger.exception("Tool %r failed", tool_name)
                    result_str = json.dumps({"error": str(exc)})

            print(
                f"PIPECAT_TOOL_RESULT user={uid} name={tool_name} "
                f"out={str(result_str)[:200]}",
                file=sys.stderr, flush=True,
            )
            await params.result_callback(result_str)

        return _handler

    for spec in _TOOL_SPECS:
        llm.register_function(spec["name"], _make_handler(spec["name"]))

    # Announce tool calls in progress so user isn't left in silence
    @llm.event_handler("on_function_calls_started")
    async def _on_tool_start(_service, _calls):
        await _state("thinking")

    # ── context aggregators ───────────────────────────────────────────────

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    # ── pipeline ──────────────────────────────────────────────────────────

    pipeline = Pipeline([
        stt,                  # Streaming STT with server-side VAD
        user_aggregator,      # Accumulates transcription into LLM context
        llm,                  # GPT-4o reasoning + function calling
        tts,                  # Text-to-speech
        device_sink,          # Forwards audio & events to device WebSocket
        assistant_aggregator, # Accumulates assistant reply into LLM context
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=_IN_RATE,
            audio_out_sample_rate=_OUT_RATE,
            allow_interruptions=True,
            enable_metrics=False,
            idle_timeout_secs=_SESSION_IDLE_S,
        ),
    )
    task_ref[0] = task

    # ── receive audio from device and inject into pipeline ────────────────

    async def _recv_device() -> None:
        try:
            while True:
                msg = await device_ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                pcm = msg.get("bytes")
                if pcm:
                    await task.queue_frame(
                        InputAudioRawFrame(
                            audio=pcm,
                            sample_rate=_IN_RATE,
                            num_channels=1,
                        )
                    )
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("PIPECAT_WS recv_device error user=%s", uid)
        finally:
            try:
                await task.cancel()
            except Exception:
                pass

    # ── fire off "connected" to device ────────────────────────────────────

    await _state("listening")

    # ── run pipeline ──────────────────────────────────────────────────────

    runner = PipelineRunner(handle_sigint=False)
    try:
        await asyncio.gather(
            runner.run(task),
            _recv_device(),
        )
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("PIPECAT_WS pipeline session error user=%s", uid)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _dev_json(ws: WebSocket, payload: dict) -> None:
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass
