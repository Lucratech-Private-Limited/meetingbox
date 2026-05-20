"""LiveKit Agents worker for MeetingBox voice.

Architecture
------------
The mini-pc device joins a LiveKit room (room name format ``voice-{user_id}-{nonce}``).
LiveKit dispatches this worker into the same room; the worker runs an
``AgentSession`` backed by ``openai.realtime.RealtimeModel`` (speech-to-speech),
exposes the same 8 MeetingBox tools that the old Pipecat pipeline used, and
emits state / navigation events over a LiveKit data channel so the device UI
can react (listening / thinking / speaking, navigate, interrupt, etc).

Run from the project root::

    python livekit_agent_worker.py start

This file is intentionally importable so unit tests can poke at the helpers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import string
import sys
from typing import Any

# --- Make sibling modules (auth, services, etc.) importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth import get_user_by_id  # noqa: E402
from services.realtime_voice_tools import execute_realtime_voice_tool  # noqa: E402

logger = logging.getLogger("meetingbox.livekit_agent")


# ---------------------------------------------------------------------------
# Tunables  (env-overridable, defaults match the previous Pipecat / Realtime
# configuration so behaviour stays close to the original).
# ---------------------------------------------------------------------------

_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")
_REALTIME_VOICE_DEFAULT = "marin"
_REALTIME_VOICE_ALLOWED = frozenset(
    {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"}
)
_REALTIME_VOICE_ALIASES = {"nova": "shimmer", "fable": "sage"}
_SESSION_IDLE_SECONDS = 45.0


def _realtime_voice() -> str:
    raw = (
        os.getenv("OPENAI_REALTIME_VOICE")
        or os.getenv("OPENAI_TTS_VOICE")
        or _REALTIME_VOICE_DEFAULT
    )
    key = raw.strip().lower()
    key = _REALTIME_VOICE_ALIASES.get(key, key)
    return key if key in _REALTIME_VOICE_ALLOWED else _REALTIME_VOICE_DEFAULT


# ---------------------------------------------------------------------------
# Farewell detection — ported verbatim from routes/pipecat_voice.py so users
# get the same auto-close behaviour after they say "bye" / "that's all" / etc.
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
# System prompt — same instructions as routes/voice.py so the assistant voice
# behaves identically on the LiveKit + Realtime path.
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
# Per-room user resolution.
# Room names look like "voice-{user_id}-{nonce}" — see routes/livekit_voice.py.
# ---------------------------------------------------------------------------


def _user_id_from_room(room_name: str) -> str | None:
    if not room_name:
        return None
    parts = room_name.split("-")
    if len(parts) < 3 or parts[0] != "voice":
        return None
    # user_id is everything between the "voice-" prefix and the trailing
    # nonce hex token. uuids contain dashes themselves, so rebuild it.
    return "-".join(parts[1:-1]) or None


def _actor_for_user_id(user_id: str) -> dict | None:
    user = get_user_by_id(user_id)
    if user is None:
        return None
    return {"type": "user", "user": user}


# ---------------------------------------------------------------------------
# Data channel helpers (state, navigate, interrupt — for device UI parity
# with the prior Pipecat WS path).
# ---------------------------------------------------------------------------


async def _publish_data(room: Any, payload: dict) -> None:
    try:
        data = json.dumps(payload).encode("utf-8")
        await room.local_participant.publish_data(data, reliable=True)
    except Exception:
        logger.debug("publish_data failed for %s", payload, exc_info=True)


# ---------------------------------------------------------------------------
# Agent worker entrypoint
# ---------------------------------------------------------------------------


async def entrypoint(ctx: Any) -> None:  # type: ignore[no-untyped-def]
    """LiveKit Agents JobContext entry. Imports the SDK lazily so unit tests
    that import this module don't require the heavy livekit-agents stack."""
    from livekit import agents, rtc  # type: ignore
    from livekit.agents import (  # type: ignore
        Agent,
        AgentSession,
        RoomInputOptions,
        RoomOutputOptions,
    )
    from livekit.agents.llm import function_tool  # type: ignore
    from livekit.plugins import openai as lk_openai  # type: ignore

    await ctx.connect()
    room: rtc.Room = ctx.room

    user_id = _user_id_from_room(room.name)
    actor = _actor_for_user_id(user_id) if user_id else None
    if not user_id or actor is None:
        logger.error(
            "Unknown user for room %r (parsed user_id=%r); leaving.",
            room.name, user_id,
        )
        await _publish_data(room, {
            "type": "error",
            "message": "Unknown user for this room.",
        })
        return

    logger.info("LiveKit agent joining room=%s user=%s", room.name, user_id)

    # ---- Tool bridge: each tool just forwards into execute_realtime_voice_tool.

    def _run_tool(name: str, **kwargs: Any) -> str:
        args_json = json.dumps(kwargs or {})
        return execute_realtime_voice_tool(
            user_id=user_id,
            actor=actor,
            name=name,
            arguments_json=args_json,
        )

    @function_tool(
        description=(
            "Search the user's long-term Mem0 memory for facts, notes, "
            "reminders, and past context."
        ),
    )
    async def memory_search(query: str) -> str:  # noqa: D401
        return await asyncio.to_thread(_run_tool, "memory_search", query=query)

    @function_tool(
        description=(
            "Save a stable fact the user wants remembered across sessions. "
            "Call when they say remember / note that / don't forget."
        ),
    )
    async def memory_remember(fact: str, context_note: str = "") -> str:
        kwargs = {"fact": fact}
        if context_note:
            kwargs["context_note"] = context_note
        return await asyncio.to_thread(_run_tool, "memory_remember", **kwargs)

    @function_tool(
        description=(
            "Primary data bundle: calendar events, recent meetings, "
            "gmail_preview, mem0. Call IMMEDIATELY when user asks about "
            "schedule, email, tasks, or meetings. days_ahead default 2."
        ),
    )
    async def get_briefing_context(days_ahead: int = 2) -> str:
        return await asyncio.to_thread(
            _run_tool, "get_briefing_context", days_ahead=days_ahead,
        )

    @function_tool(
        description=(
            "Run the user's request through MeetingBox assistants (calendar, "
            "Gmail, tasks). Pass their exact spoken intent."
        ),
    )
    async def assistant_intent(message: str, meeting_id: str = "") -> str:
        kwargs: dict[str, Any] = {"message": message}
        if meeting_id:
            kwargs["meeting_id"] = meeting_id
        return await asyncio.to_thread(_run_tool, "assistant_intent", **kwargs)

    @function_tool(description="List assistant actions queued for approval.")
    async def list_pending_actions() -> str:
        return await asyncio.to_thread(_run_tool, "list_pending_actions")

    @function_tool(
        description=(
            "Execute ONE queued write only after the user has clearly "
            "confirmed aloud."
        ),
    )
    async def approve_pending_action(
        pending_id: str,
        confirmed_by_user: bool,
        confirmation_phrase: str,
    ) -> str:
        return await asyncio.to_thread(
            _run_tool,
            "approve_pending_action",
            pending_id=pending_id,
            confirmed_by_user=confirmed_by_user,
            confirmation_phrase=confirmation_phrase,
        )

    @function_tool(description="Cancel a queued action after the user declines.")
    async def reject_pending_action(pending_id: str) -> str:
        return await asyncio.to_thread(
            _run_tool, "reject_pending_action", pending_id=pending_id,
        )

    @function_tool(
        description=(
            "Open a main screen on the device. Only call when user explicitly "
            "says 'open / show / go to'. Allowed screens: calendar, emails, "
            "home, meetings, morning_brief, settings, mic_test."
        ),
    )
    async def navigate_device_ui(screen: str) -> str:
        sc = (screen or "").strip().lower()
        if not sc:
            return json.dumps({"ok": False, "error": "screen_required"})
        await _publish_data(room, {"type": "navigate", "screen": sc})
        return json.dumps({"ok": True, "device_navigate": sc})

    tools = [
        memory_search,
        memory_remember,
        get_briefing_context,
        assistant_intent,
        list_pending_actions,
        approve_pending_action,
        reject_pending_action,
        navigate_device_ui,
    ]

    # ---- Realtime speech-to-speech model -----------------------------------

    voice = _realtime_voice()
    realtime_model = lk_openai.realtime.RealtimeModel(
        model=_REALTIME_MODEL,
        voice=voice,
    )

    # In livekit-agents 1.5.x the RealtimeModel belongs on AgentSession, not
    # Agent. AgentSession(llm=RealtimeModel) activates the voice-to-voice
    # pipeline that pipes room audio → OpenAI Realtime → room audio.
    # Tools and instructions stay on the Agent as normal.
    agent = Agent(
        instructions=_SYSTEM_PROMPT,
        tools=tools,
    )

    # user_away_timeout: use a large value (30 min) rather than None, because
    # in some livekit-agents builds None means "use default 15 s" rather than
    # "disabled". 1800 s keeps the session alive while the user is thinking.
    session = AgentSession(
        llm=realtime_model,
        user_away_timeout=1800.0,
    )

    # ---- Event wiring: state, interrupt, farewell --------------------------

    loop = asyncio.get_running_loop()

    def _dispatch(coro: Any) -> None:
        try:
            loop.create_task(coro)
        except Exception:
            logger.debug("event dispatch failed", exc_info=True)

    @session.on("user_started_speaking")
    def _on_user_started(_ev: Any) -> None:
        _dispatch(_publish_data(room, {"type": "interrupt"}))
        _dispatch(_publish_data(room, {"type": "state", "state": "listening"}))

    @session.on("user_stopped_speaking")
    def _on_user_stopped(_ev: Any) -> None:
        _dispatch(_publish_data(room, {"type": "state", "state": "thinking"}))

    @session.on("agent_started_speaking")
    def _on_agent_started(_ev: Any) -> None:
        _dispatch(_publish_data(room, {"type": "state", "state": "speaking"}))

    @session.on("agent_stopped_speaking")
    def _on_agent_stopped(_ev: Any) -> None:
        _dispatch(_publish_data(room, {"type": "state", "state": "listening"}))

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev: Any) -> None:
        try:
            text = (getattr(ev, "transcript", "") or "").strip()
            is_final = bool(getattr(ev, "is_final", False))
        except Exception:
            return
        if not text:
            return
        logger.info("LK transcript user=%s final=%s text=%r", user_id, is_final, text[:120])
        if is_final and _is_farewell(text):
            logger.info("LK farewell detected; closing session for user=%s", user_id)
            _dispatch(session.aclose())

    # ---- Run ---------------------------------------------------------------

    await _publish_data(room, {"type": "connected"})
    await _publish_data(room, {"type": "state", "state": "listening"})

    logger.info("LK session.start() about to be called for user=%s", user_id)
    logger.info("LK model=%s voice=%s", realtime_model.model if hasattr(realtime_model, 'model') else _REALTIME_MODEL, voice)
    try:
        await session.start(
            agent,
            room=room,
            room_input_options=RoomInputOptions(),
            room_output_options=RoomOutputOptions(),
        )
    except Exception:
        logger.exception("LK session.start() FAILED for user=%s", user_id)
        raise
    logger.info("LK session.start() completed for user=%s started=%s agent_state=%s",
                user_id,
                getattr(session, '_started', '??'),
                getattr(session, '_agent_state', '??'))
    logger.info("LK input.audio=%s output.audio=%s",
                getattr(getattr(session, 'input', None), 'audio', '??'),
                getattr(getattr(session, 'output', None), 'audio', '??'))

    # Force an initial greeting to test the full voice pipeline. generate_reply()
    # returns a SpeechHandle (sync), so we fire it and wait for it to finish.
    try:
        logger.info("LK dispatching initial greeting for user=%s", user_id)
        await asyncio.sleep(0.5)  # brief pause for RoomIO to subscribe to device audio
        handle = session.generate_reply(
            user_input="Hello! Greet the user briefly and ask how you can help.",
        )
        logger.info("LK initial greeting handle=%s for user=%s", handle, user_id)
        # Wait for speech to complete (SpeechHandle is awaitable in livekit-agents)
        if hasattr(handle, '__await__'):
            await handle
            logger.info("LK initial greeting complete for user=%s", user_id)
    except Exception:
        logger.exception("LK initial greeting failed for user=%s", user_id)

    # Hold the entrypoint until the room is disconnected so the worker is
    # available for the entire conversation.
    disconnected = asyncio.Event()

    @room.on("disconnected")
    def _on_disconnect(_reason: Any = None) -> None:
        disconnected.set()

    try:
        await asyncio.wait_for(disconnected.wait(), timeout=_SESSION_IDLE_SECONDS * 60)
    except asyncio.TimeoutError:
        logger.info("LK session idle hold expired for user=%s; releasing.", user_id)
    finally:
        try:
            await session.aclose()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI entry — `python livekit_agent_worker.py start` (production) or `dev`.
# ---------------------------------------------------------------------------


def _main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    from livekit import agents  # type: ignore

    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
        ),
    )


if __name__ == "__main__":
    _main()
