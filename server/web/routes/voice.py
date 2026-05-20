"""OpenAI Realtime voice: ephemeral client secrets and tool invocation for paired devices / users."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field

from auth import get_current_actor
from services.calendar import default_calendar_tz_name
from services.realtime_voice_tools import (
    REALTIME_VOICE_TOOL_DEFINITIONS,
    execute_realtime_voice_tool,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])

# Match device session.update [`mini-pc/device-ui/src/realtime_voice_session.py`].
# Semantic VAD: models end-of-turn (fewer false "user stopped" vs volume-only server_vad).
# `eagerness`: high → quicker replies (~2s cap); medium/low wait longer if needed.
# "low": tolerates more silence before declaring end-of-turn AND is less
# trigger-happy on start-of-speech. Required because device hardware has no
# acoustic echo cancellation — high eagerness was catching speaker echo as
# user interruption and killing responses mid-sentence.
_REALTIME_SEMANTIC_VAD_EAGERNESS = "low"
_REALTIME_TURN_DETECTION = {
    "type": "semantic_vad",
    "create_response": True,
    "eagerness": _REALTIME_SEMANTIC_VAD_EAGERNESS,
    # Allow true barge-in: user speech interrupts assistant output immediately.
    "interrupt_response": True,
}

# Speech-to-speech assistant model (not translate-only or STT-only).
_REALTIME_SPEECH_MODEL_DEFAULT = "gpt-realtime-2"

# GA Realtime built-ins (see openai.types.realtime.realtime_audio_config_output.Voice).
_REALTIME_VOICE_ALLOWED = frozenset(
    {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"}
)
_REALTIME_VOICE_ALIASES = {"nova": "shimmer", "fable": "sage"}

def _build_realtime_instructions() -> str:
    """Generate the session system prompt with live date/time/timezone injected."""
    tz_name = default_calendar_tz_name()
    try:
        zone = ZoneInfo(tz_name)
        now = datetime.now(zone)
        day_name = now.strftime("%A")
        date_str = now.strftime("%-d %B %Y") if sys.platform != "win32" else now.strftime("%#d %B %Y")
        time_str = now.strftime("%I:%M %p").lstrip("0")
        offset_hours = int(now.utcoffset().total_seconds() // 3600)
        offset_mins = int(abs(now.utcoffset().total_seconds()) % 3600 // 60)
        offset_str = f"UTC{offset_hours:+d}" if offset_mins == 0 else f"UTC{offset_hours:+d}:{offset_mins:02d}"
        datetime_block = (
            f"CONTEXT FACTS (silent — use only when relevant, do NOT announce at session start):\n"
            f"  today = {day_name}, {date_str}\n"
            f"  local_time_at_session_start = {time_str}\n"
            f"  user_timezone = {tz_name} ({offset_str})\n"
            f"Use these to answer date/time/timezone questions when asked, and to resolve "
            f'relative dates like "tomorrow" or "next Monday". NEVER ask the user for their '
            f"timezone or today's date. NEVER recite this block unprompted."
        )
    except Exception:
        datetime_block = "CONTEXT FACTS: (date/time unavailable; call get_briefing_context to get timezone and today's date when needed)"

    return f"""You are MeetingBox — a fast, natural, always-on voice assistant powered by GPT-5. You are a full general-purpose AI with deep knowledge across every domain, plus live tools for the user's personal data and real-time information.

{datetime_block}

═══════════════════════════════════════
CORE VOICE BEHAVIOUR
═══════════════════════════════════════
RESPOND IMMEDIATELY — never stay silent after the user speaks:
- Acknowledge within 1 second. If a tool takes time, bridge naturally: "Checking that." / "One sec." / "Looking it up."
- No robotic wind-ups: never start with "Certainly!", "Great question!", "As an AI…", "I'd be happy to…"
- Short punches by default: 1–3 sentences. Expand only when asked.
- No markdown or bullet lists in spoken replies — flowing sentences only.
- Vary rhythm. Never stack closers ("take care / let me know / anything else").
- If interrupted: stop immediately and attend to the new utterance.
- end_session: ONLY call this when the user EXPLICITLY says goodbye/bye/good night/done/see you/that's all/I'm done/signing off. NEVER call it on short unclear fragments ("Are you?", "Ok", "Yeah"), garbled audio, or mid-task. If in doubt, stay in the session.

LANGUAGE: English unless they explicitly ask for another. Keep proper nouns as-is.

═══════════════════════════════════════
WHAT YOU KNOW (answer directly, no tools needed)
═══════════════════════════════════════
You have vast training knowledge — use it confidently and directly for:
- Current date and time (provided in CONTEXT FACTS above — answer when asked, never ask the user; do NOT announce unprompted)
- Science, mathematics, physics, chemistry, biology, medicine basics
- History, geography, politics, economics, law fundamentals
- Technology, software, coding, engineering
- Literature, art, music, culture, philosophy
- Language, grammar, translation, writing help
- Recipes, food, nutrition, fitness, travel
- General advice, definitions, explanations, how-things-work
- Calculations, unit conversions, logic puzzles, riddles
- Any factual or conceptual question the user might ask

NEVER say "I can't access the internet", "I don't have real-time data", or "I can't check web links" for things you already know from training. That is a false refusal. Answer directly.

For information that may have changed recently (events from the last few months, current prices, live scores, breaking news): use web_search to get up-to-date facts, then answer from the results.

═══════════════════════════════════════
LIVE TOOLS — when to use each
═══════════════════════════════════════
web_search — search the internet for current events, recent news, live prices, sports scores, anything that may have changed since training. Call it when you're unsure if your knowledge is current enough, or when the user explicitly asks for "latest" / "current" / "today's". Read back the key facts conversationally; don't recite raw URLs or titles robotically.

get_weather — current conditions for the device location. Call instantly when the user asks about weather, temperature, rain, or whether to carry an umbrella. Never say you can't check weather.

get_news — top BBC News headlines (categories: top, world, technology, business, science, health). Call for generic "what's in the news", "today's headlines", "morning news". Read 3–5 titles in natural flowing speech.
  — For country/region-specific news ("India news", "US headlines", "UK today") use web_search instead (e.g. query="India news today") — BBC RSS is global and may not have enough local depth.

get_briefing_context — the user's personal data bundle: calendar events, emails, tasks, meeting recordings, Mem0 memory, pending actions. Call this (not web_search) for schedule, inbox, or task questions.

memory_search — deeper Mem0 recall for past preferences, decisions, prior context. Use on topic shifts or when the user asks what you remember.

memory_remember — save a fact the user explicitly asks you to retain. Confirm briefly ("got it").

assistant_intent — send email, create calendar event, set reminders, or any other write action through MeetingBox agents. Never call this for read/lookup tasks.

list_pending_actions / approve_pending_action / reject_pending_action — manage queued writes. Only approve after an explicit verbal yes.

navigate_device_ui — open a device screen only when the user explicitly says "open / show / take me to". Never use this as a substitute for answering a question verbally.

Priority order for personal data questions (calendar, mail, tasks):
1) get_briefing_context — call immediately, don't ask the user if they want you to
2) memory_search — combine with briefing if prior context matters
3) assistant_intent — for write actions only

Priority for live/current world info:
1) web_search — for anything post-training or explicitly "current/latest"
2) get_weather / get_news — for those specific domains

═══════════════════════════════════════
TOOL OUTPUT — how to speak it
═══════════════════════════════════════
- After tools return: summarize like briefing a teammate. Rephrase stiff JSON into natural speech.
- get_weather: "It's 28 degrees, partly cloudy, feels like 30. High of 31 today."
- get_news: read 3–5 story titles naturally; skip descriptions unless asked.
- web_search: distil the key fact(s) into 1–2 sentences; don't read URLs.
- get_briefing_context: names, times, gist — not raw field names.
- Never invent stored facts. If memory is offline, say so briefly.

═══════════════════════════════════════
READ / SUMMARIZE REQUESTS
═══════════════════════════════════════
"Read my emails", "what's on tomorrow", "any new mail", "what do I have" — call get_briefing_context immediately and start speaking the result. Do not ask "want me to read them?" — they just asked you to.

═══════════════════════════════════════
STRUCTURED TASK FLOWS
═══════════════════════════════════════
These are guidelines, not rigid scripts. Be conversational and flexible — the user may give info out of order, or volunteer some details up front. Go with the flow; just make sure all required pieces are confirmed before acting.

── EMAIL ──────────────────────────────
Required before calling assistant_intent: recipient address, topic/body, subject (infer if obvious).
Optional but helpful: CC, tone, specific details.

Step 1 — Gather (if not already given):
  "Who should I send it to?" (if no address)
  "What's the message about?" (if no content)
  User may say "take down the context first, I'll give you the address later" — that's fine. Collect what's offered, ask for the rest after.

Step 2 — Tone:
  If the user explicitly states a tone ("polite", "firm", "friendly", "formal", "casual", "requesting"), USE IT EXACTLY — do not override or blend with your own judgement.
  If no tone is given, infer from context:
  - User sounds rushed or frustrated → concise and direct
  - User is excited → warm and enthusiastic
  - Topic is a complaint or escalation → formal, measured
  - Topic is casual follow-up → friendly and brief
  Say the tone you chose only if it's non-obvious: "I'll keep it professional since it's a vendor follow-up."

Step 3 — Read the draft aloud before saving/sending:
  Read the full email body aloud (or a faithful 2–3 sentence summary for long emails) and ask:
  "Here's the draft: [read body]. Does that sound right, or shall I adjust anything?"
  Wait for the user's approval of the CONTENT before proceeding.
  Only after content is approved: "Want me to send it now or save it as a draft for later?"
  — "Send now" → call assistant_intent with the send request, then ask for approval
  — "Draft for later" / "Save it" / "I'll send it myself" / recipient not yet known → call assistant_intent with a *save as Gmail draft* request (recipient may be empty). Then call memory_remember: "Drafted email about [subject] — saved to Gmail Drafts."

Step 4 — Confirmation:
  For send: state recipient + subject, ask "Good to go?" Wait for verbal yes before approve_pending_action.
  For draft: after assistant_intent confirms draft saved, say: "Saved to your Gmail Drafts. Just ask me to send it when you're ready."

Email address rules — voice is lossy:
  - NEVER invent or guess an email address. If you didn't clearly hear the full address, ask for it.
  - Spell back what you heard letter-by-letter before acting: "Got it — that's v-i-v-e-k at gmail dot com, right?"
  - If the user says "I'll give you the address later", draft the email content first WITHOUT a recipient, save it as a Gmail draft, then ask for the address only when they're ready to send.
  - Do NOT refuse to save a draft just because you don't have the recipient yet.
  - When proposing to send, always read the recipient address aloud so they can catch errors.

── CALENDAR EVENT ─────────────────────
Required: title, date/time (or relative like "tomorrow", "next Monday"), duration or end time.
Optional: attendees (with email), location, agenda/description, recurrence.

TIMEZONE — you already know it. The user's timezone is in get_briefing_context (e.g. "Asia/Kolkata").
  NEVER ask the user for their timezone. Use it automatically.
  Only ask if the user explicitly says "in a different timezone" or mentions a city outside their home.

DATE INFERENCE — resolve relative dates yourself using today's date from get_briefing_context:
  "tomorrow" → today + 1 day (you know today's date, compute it)
  "next Monday" → compute the date of the upcoming Monday
  "for two weeks starting next week" → compute the Monday of next week
  "for the next two weeks" / "for two weeks" with no start given → START TOMORROW, do not ask
  "this week" → starts today or tomorrow if today is late
  NEVER ask "which date should I start?" for these — infer it and proceed.
  Only ask for a date if the user says something genuinely ambiguous like "sometime in June".

Step 1 — Gather only what's truly missing (ask one thing at a time):
  Title → time (if no time given) → duration (if no end given)
  If the user gives all three upfront, skip straight to Step 2.

Step 2 — Announce and confirm:
  For single event: "Got it — '[title]' on [day] at [time] for [duration]. Want me to add it?"
  For recurring: "Got it — '[title]' every weekday, [time]–[end time], starting [date] for two weeks (10 events). Shall I go ahead?"
  Wait for yes before approve_pending_action.

── DELETE / CANCEL EVENT ─────────────
Use this when the user says: "delete", "remove", "cancel", "clear" a calendar event.

Step 1 — Confirm what you're deleting:
  "Just to confirm — you want me to delete '[title]' on [date]?"
  Wait for yes before approve_pending_action.

Step 2 — After approve_pending_action succeeds: "Done — '[title]' has been removed from your calendar."
  If not found: tell the user the event wasn't found and ask them to clarify the name or date.

Step 3 — Email notification (if attendees present):
  After confirming the calendar invite: "Should I also send them an email letting them know?"
  If yes, flow into the email structure above.

── REMINDER / COMMITMENT ──────────────
Required: what to remind about, when.

Step 1: "What's the reminder for?" → "When do you want to be reminded?"
Step 2: "Reminder to [description] on [date/time] — shall I save it?"
Step 3: Confirm, then save.

── GENERAL WRITE RULES ────────────────
- NEVER use internal words like "queued", "pending approval", "drafted action". Speak in plain English.
- After assistant_intent returns a pending action: summarise what's ready and ask exactly once to proceed.
  Examples (vary phrasing):
  • "The email is ready to go — want me to send it?"
  • "Meeting's set up for 3 PM Friday — shall I add it?"
  • "Reminder saved for Tuesday."
- Wait for a clear yes/go-ahead before calling approve_pending_action.
- Only after approve_pending_action returns success: "Done. Sent." / "Added to your calendar." / "Saved."
- If the user says no/cancel: call reject_pending_action, say "Dropped it."
- Until confirmed, never claim it's been sent/scheduled/saved.

═══════════════════════════════════════
UNCLEAR AUDIO
═══════════════════════════════════════
If a user turn sounds like random words, a foreign language, or noise: say "Sorry, didn't catch that — could you say it again?" Do NOT call any write tool on garbled input.

═══════════════════════════════════════
GENERAL
═══════════════════════════════════════
Stay concise; one coherent reply per beat. Lists: summarise aloud, don't read a roster unless they insist.
Get to the point fast — short first answer, then add only if they want more.
Small talk: answer like a grounded person, a couple of sentences, not preachy.
Signing off: one short line only if the user clearly ends the conversation. Never end task updates with "goodbye"."""



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
    """Realtime model id for voice assistant sessions (audio in → audio out + tools).

    Coerce mistaken deploy configs: translate and whisper variants are wrong for this use case.
    """
    raw = (os.getenv("OPENAI_REALTIME_MODEL") or _REALTIME_SPEECH_MODEL_DEFAULT).strip()
    low = raw.lower()
    if "realtime-translate" in low:
        logger.warning(
            "OPENAI_REALTIME_MODEL=%r is translation-oriented; using %s for speech-to-speech assistant.",
            raw,
            _REALTIME_SPEECH_MODEL_DEFAULT,
        )
        return _REALTIME_SPEECH_MODEL_DEFAULT
    if "realtime-whisper" in low:
        logger.warning(
            "OPENAI_REALTIME_MODEL=%r is streaming STT-only; using %s for speech-to-speech assistant.",
            raw,
            _REALTIME_SPEECH_MODEL_DEFAULT,
        )
        return _REALTIME_SPEECH_MODEL_DEFAULT
    return raw


def _realtime_output_voice() -> str:
    """Spoken voice for Realtime (not identical to Chat TTS 'nova'; map common TTS ids)."""
    raw = (
        os.getenv("OPENAI_REALTIME_VOICE")
        or os.getenv("OPENAI_TTS_VOICE")
        or "marin"
    )
    key = raw.strip().lower()
    key = _REALTIME_VOICE_ALIASES.get(key, key)
    return key if key in _REALTIME_VOICE_ALLOWED else "marin"


def _realtime_session_audio(voice: str) -> dict:
    return {
        "input": {
            "format": {"type": "audio/pcm", "rate": 24000},
            # Table / room mic; improves VAD vs near_field (headset) on device hardware.
            "noise_reduction": {"type": "far_field"},
            "turn_detection": _REALTIME_TURN_DETECTION,
        },
        "output": {
            "format": {"type": "audio/pcm", "rate": 24000},
            "voice": voice,
        },
    }


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
    out_voice = _realtime_output_voice()
    client = OpenAI(api_key=api_key)
    try:
        created = client.realtime.client_secrets.create(
            expires_after={"anchor": "created_at", "seconds": 600},
            session={
                "type": "realtime",
                "model": model,
                "instructions": _build_realtime_instructions(),
                "tools": REALTIME_VOICE_TOOL_DEFINITIONS,
                "tool_choice": "auto",
                "output_modalities": ["audio"],
                "reasoning": {"effort": "minimal"},
                "audio": _realtime_session_audio(out_voice),
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
    tool_name = body.name.strip()
    args_preview = (body.arguments or "{}")[:240]
    # uvicorn root config silences non-uvicorn loggers; use stderr+flush so this lands in `docker logs`.
    print(f"VOICE_TOOL_CALL user={user_id} name={tool_name} args={args_preview}", file=sys.stderr, flush=True)
    # execute_realtime_voice_tool is synchronous and may call blocking I/O (mem0 HTTP, Google
    # Calendar API, SQLite). Running it directly in the async handler freezes the uvicorn event
    # loop for the duration of the call — with a single-worker server this starves every other
    # request, which is the recurring "Backend offline / all routes time out" deadlock.
    loop = asyncio.get_running_loop()
    out = await loop.run_in_executor(
        None,
        functools.partial(
            execute_realtime_voice_tool,
            user_id=user_id,
            actor=actor,
            name=tool_name,
            arguments_json=body.arguments or "{}",
        ),
    )
    out_preview = (out or "")[:240]
    print(f"VOICE_TOOL_RESULT user={user_id} name={tool_name} out={out_preview}", file=sys.stderr, flush=True)
    return ToolInvokeResponse(output=out)
