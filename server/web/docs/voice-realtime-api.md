# OpenAI Realtime voice (MeetingBox server contract)

The device UI (mini-pc) can run OpenAI’s **Realtime** speech-to-speech session using a short-lived client secret minted by this API. The server does **not** proxy audio: the appliance connects directly to OpenAI’s WebSocket (`wss://api.openai.com/v1/realtime`) with the secret, while **tools** (Mem0 search, morning-briefing bundle) execute on MeetingBox via HTTP.

## Enable

In `server/web/.env` (see [`../.env.example`](../.env.example)):

| Variable | Purpose |
| -------- | ------- |
| `MEETINGBOX_REALTIME_VOICE_ENABLED=1` | Turns on `POST /api/voice/realtime/session` and `POST /api/voice/realtime/tools/invoke`. When unset/off, both return **503**. |
| `OPENAI_API_KEY` | Required to call OpenAI `realtime.client_secrets.create`. |
| `OPENAI_REALTIME_MODEL` | Optional. Voice assistant default in code is **`gpt-realtime-2`** (speech-to-speech with reasoning-class behaviour per [Advancing voice intelligence](https://openai.com/index/advancing-voice-intelligence-with-new-models-in-the-api/)). Use **`gpt-realtime-translate`** only for live translation apps; **`gpt-realtime-whisper`** for streaming STT without spoken replies — neither replaces the general assistant pattern used here. See [Realtime API models](https://platform.openai.com/docs/guides/realtime). |

## Endpoints

All routes are prefixed with `/api/voice` (mounted in the FastAPI app).

Minted sessions include **server-side VAD** (`audio.input.turn_detection` with ~550 ms trailing silence — tuned for responsiveness) and 24 kHz `audio/pcm` so utterances are not cut off at very short silence defaults.

Spoken voice is **`audio.output.voice`** on the minted session (default **shimmer**; set **`OPENAI_REALTIME_VOICE`** or fall back through **`OPENAI_TTS_VOICE`**, mapping e.g. TTS **`nova`** to Realtime **`shimmer`** — Realtime and TTS expose different voice name sets).

### `POST /api/voice/realtime/session`

- **Auth:** Dashboard JWT **or** paired device Bearer token (`mbd_…`).
- **Response:** JSON with at least:
  - `client_secret` — ephemeral `ek_…` value used as `Authorization: Bearer` on the OpenAI Realtime WebSocket.
  - `model` — model id to pass on the WebSocket URL query string (`?model=…`).
  - `expires_at`, `session` — session metadata from OpenAI (tools and instructions are configured server-side when the secret is created).

### `POST /api/voice/realtime/tools/invoke`

- **Auth:** Same as session (user-scoped).
- **Body:** JSON `{ "call_id": string, "name": string, "arguments": string }`  
  `arguments` is a **JSON object string** as emitted by the Realtime model for function calls.
- **Response:** `{ "output": string }` — string passed back to OpenAI as `function_call_output` (typically JSON text from Mem0 or the briefing bundle).

Implemented tool names (see `server/web/services/realtime_voice_tools.py`):

| Name | Role |
| --- | --- |
| `memory_search` | Mem0 long-term recall. |
| `get_briefing_context` | Same structured bundle as the device morning brief. |
| `assistant_intent` | Runs the orchestrator/agents pipeline (same as typed `POST /api/assistant/intent`). Large responses may be truncated. |
| `navigate_device_ui` | Returns `device_navigate` screen id; **device parses this** and runs Kivy `goto_screen` locally (calendar, emails, meetings, …). |
| `list_pending_actions` | Queued writes (calendar create, Gmail send, device tools) awaiting approval. |
| `approve_pending_action` | Execute a queued action after explicit user confirmation (`pending_id`). |
| `reject_pending_action` | Cancel a queued action. |

## Device behaviour (reference)

After a successful `realtime/session` call, the device opens the OpenAI WebSocket, sends a **`session.update`** with matching audio format and **`turn_detection`**, streams PCM16 input at **24 kHz**, plays streamed output (e.g. via `aplay` on Linux), and on each completed function call POSTs to `realtime/tools/invoke`, then sends the tool result and `response.create` on the Realtime socket per OpenAI’s protocol.

If session mint fails (e.g. 503 disabled, pairing), the device shows a brief status message before falling back to the local wake assistant.
