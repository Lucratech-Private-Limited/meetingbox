from contextlib import asynccontextmanager
import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path

from typing import Optional

# Load server/web/.env into os.environ before any local imports read env vars.
try:
  from dotenv import load_dotenv
  load_dotenv(Path(__file__).resolve().parent / ".env")  # server/web/.env
except ImportError:
  pass

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.routing import Match, get_route_path
from starlette.types import Scope
import redis

from database import init_database, get_connection
from agent_registry import load_agent_definitions
from routes.meetings import router as meetings_router
from routes.agents import router as agents_router
from routes.assistant import router as assistant_router
from routes.system import router as system_router
from routes.device import router as device_router
from routes.devices import router as devices_router
from routes.auth import router as auth_router
from routes.actions import router as actions_router
from routes.integrations import router as integrations_router
from routes.commitments import router as commitments_router
from routes.briefing import router as briefing_router
from routes.admin_memory import router as admin_memory_router
from routes.voice import router as voice_router
from auth import get_optional_user, resolve_actor_from_access_token
from routes.device import SetupCompleteBody, finalize_first_boot_setup
from rate_limit import limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("meetingbox.web")


def _startup_strict_env() -> None:
  """Optional fail-fast when MEETINGBOX_STRICT_STARTUP=1 or MEETINGBOX_PRODUCTION=1."""
  strict = os.getenv("MEETINGBOX_STRICT_STARTUP", "").strip() == "1"
  production = os.getenv("MEETINGBOX_PRODUCTION", "").strip() == "1"
  if strict or production:
    missing = [k for k in ("JWT_SECRET_KEY",) if not (os.getenv(k) or "").strip()]
    if missing:
      raise RuntimeError(
        f"Production/strict startup: set environment variables: {', '.join(missing)}"
      )

  if os.getenv("MEETINGBOX_REQUIRE_EXPLICIT_CORS", "").strip() == "1":
    raw = (os.getenv("MEETINGBOX_CORS_ORIGINS", "") or "").strip()
    parts = [o.strip() for o in raw.split(",") if o.strip()] if raw else []
    if not parts or any(p == "*" for p in parts):
      raise RuntimeError(
        "MEETINGBOX_REQUIRE_EXPLICIT_CORS=1: set MEETINGBOX_CORS_ORIGINS to a comma-separated "
        "list of real origins (no wildcard *)."
      )

  workers = (os.getenv("WEB_CONCURRENCY") or os.getenv("UVICORN_WORKERS") or "1").strip()
  try:
    n_workers = int(workers)
  except ValueError:
    n_workers = 1
  if n_workers > 1 and os.getenv("MEETINGBOX_I_ACKNOWLEDGE_MULTI_WORKER_RECORDING", "").strip() != "1":
    logger.warning(
      "WEB_CONCURRENCY/UVICORN_WORKERS=%s: recording state in Redis is shared but in-memory "
      "helpers may diverge across processes. Prefer a single worker for appliance demos or set "
      "MEETINGBOX_I_ACKNOWLEDGE_MULTI_WORKER_RECORDING=1.",
      workers,
    )


_startup_strict_env()
init_database()
load_agent_definitions()

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
STATIC_DIR = Path(os.getenv("STATIC_DIR", "/app/static"))


class ConnectionManager:
  def __init__(self) -> None:
    self.active_connections: list[WebSocket] = []

  async def connect(self, websocket: WebSocket) -> None:
    await websocket.accept()
    self.active_connections.append(websocket)
    logger.info("WebSocket client connected (%d total)", len(self.active_connections))

  def disconnect(self, websocket: WebSocket) -> None:
    if websocket in self.active_connections:
      self.active_connections.remove(websocket)
      logger.info("WebSocket client disconnected (%d total)", len(self.active_connections))

  async def broadcast(self, message: dict) -> None:
    dead: list[WebSocket] = []
    for ws in self.active_connections:
      try:
        await ws.send_json(message)
      except Exception:
        dead.append(ws)
    for ws in dead:
      self.disconnect(ws)


manager = ConnectionManager()


def _redis_listener_thread(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
  """Blocking Redis pubsub loop with automatic reconnection."""
  backoff = 1
  max_backoff = 30
  while True:
    try:
      client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
      pubsub = client.pubsub()
      pubsub.subscribe("events", "audio_segments")
      logger.info("Redis event listener started")
      backoff = 1
      for message in pubsub.listen():
        if message.get("type") != "message":
          continue
        try:
          event = json.loads(message["data"])
          loop.call_soon_threadsafe(queue.put_nowait, event)
        except json.JSONDecodeError:
          continue
    except redis.ConnectionError:
      logger.warning("Redis connection lost, reconnecting in %ds...", backoff)
      time.sleep(backoff)
      backoff = min(backoff * 2, max_backoff)
    except Exception:
      logger.exception("Unexpected error in Redis listener, reconnecting in %ds...", backoff)
      time.sleep(backoff)
      backoff = min(backoff * 2, max_backoff)


async def _redis_event_relay(queue: asyncio.Queue) -> None:
  """Async task: read from queue and broadcast to WebSocket clients."""
  while True:
    try:
      event = await asyncio.wait_for(queue.get(), timeout=1.0)
      await manager.broadcast(event)
    except asyncio.TimeoutError:
      continue
    except asyncio.CancelledError:
      break


def _auto_delete_thread() -> None:
  """Periodically delete meetings older than the configured auto_delete_days setting."""
  from datetime import datetime, timedelta
  from pathlib import Path
  from routes.device import _load_settings

  INTERVAL = 6 * 3600  # run every 6 hours

  while True:
    try:
      settings = _load_settings()
      ad = settings.get("auto_delete_days", "never")
      if ad and ad != "never":
        try:
          days = int(ad)
        except ValueError:
          days = 0
        if days > 0:
          cutoff = (datetime.now() - timedelta(days=days)).isoformat()
          conn = get_connection()
          try:
            cur = conn.cursor()
            cur.execute(
              "SELECT id, audio_path FROM meetings WHERE created_at < ?",
              (cutoff,),
            )
            rows = cur.fetchall()
            for mid, audio_path in rows:
              cur.execute("DELETE FROM actions WHERE meeting_id = ?", (mid,))
              cur.execute("DELETE FROM segments WHERE meeting_id = ?", (mid,))
              cur.execute("DELETE FROM summaries WHERE meeting_id = ?", (mid,))
              cur.execute("DELETE FROM local_summaries WHERE meeting_id = ?", (mid,))
              cur.execute("DELETE FROM meetings WHERE id = ?", (mid,))
              if audio_path:
                p = Path(audio_path)
                if p.exists():
                  p.unlink(missing_ok=True)
            conn.commit()
            if rows:
              logger.info("Auto-delete: removed %d meetings older than %d days", len(rows), days)
          finally:
            conn.close()

      audit_days = int(os.getenv("MEETINGBOX_AUDIT_RETENTION_DAYS", "0") or 0)
      if audit_days > 0:
        acut = (datetime.now() - timedelta(days=audit_days)).isoformat()
        conn = get_connection()
        try:
          cur = conn.cursor()
          cur.execute(
            """
            DELETE FROM pending_assistant_actions
            WHERE status != 'pending' AND resolved_at IS NOT NULL AND resolved_at < ?
            """,
            (acut,),
          )
          cur.execute(
            """
            DELETE FROM assistant_audits
            WHERE created_at < ?
              AND id NOT IN (
                SELECT audit_id FROM pending_assistant_actions
                WHERE audit_id IS NOT NULL AND status = 'pending'
              )
            """,
            (acut,),
          )
          conn.commit()
        finally:
          conn.close()
    except Exception as e:
      logger.warning("Auto-delete error: %s", e)

    time.sleep(INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[override]
  loop = asyncio.get_running_loop()
  queue: asyncio.Queue = asyncio.Queue()
  thread = threading.Thread(target=_redis_listener_thread, args=(queue, loop), daemon=True)
  thread.start()
  ad_thread = threading.Thread(target=_auto_delete_thread, daemon=True)
  ad_thread.start()
  relay = asyncio.create_task(_redis_event_relay(queue))
  yield
  relay.cancel()
  try:
    await relay
  except asyncio.CancelledError:
    pass


app = FastAPI(title="MeetingBox API", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_cors_raw = (os.getenv("MEETINGBOX_CORS_ORIGINS", "") or "").strip()
if _cors_raw:
  _cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()]
  if not _cors_origins:
    _cors_origins = ["*"]
else:
  _cors_origins = ["*"]

app.add_middleware(
  CORSMiddleware,
  allow_origins=_cors_origins,
  allow_credentials=False,
  allow_methods=["*"],
  allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
app.include_router(assistant_router, prefix="/api/assistant", tags=["assistant"])
app.include_router(meetings_router, prefix="/api/meetings", tags=["meetings"])
app.include_router(system_router, prefix="/api/system", tags=["system"])
app.include_router(device_router, prefix="/api/device", tags=["device"])
app.include_router(devices_router, prefix="/api", tags=["devices"])
app.include_router(actions_router, prefix="/api", tags=["actions"])
app.include_router(integrations_router, prefix="/api", tags=["integrations"])
app.include_router(commitments_router, prefix="/api", tags=["commitments"])
app.include_router(briefing_router, prefix="/api")
app.include_router(admin_memory_router, prefix="/api")
app.include_router(voice_router, prefix="/api/voice")


@app.post("/api/device/setup-complete", tags=["device"])
async def post_setup_complete_root(
    body: SetupCompleteBody,
    _current_user: Optional[dict] = Depends(get_optional_user),
):
    """
    Finish first-boot after Wi‑Fi is on a real LAN. Must stay on the app root
    (before SPA static routes) so POST is never confused with GET /*.
    """
    return finalize_first_boot_setup(body)


def _ws_bearer_from_request(websocket: WebSocket) -> str:
  """Token for WS auth: query access_token, else Authorization: Bearer (non-browser clients)."""
  q = (websocket.query_params.get("access_token") or "").strip()
  if q:
    return q
  auth_hdr = (websocket.headers.get("authorization") or websocket.headers.get("Authorization") or "").strip()
  if auth_hdr.lower().startswith("bearer "):
    return auth_hdr[7:].strip()
  return ""


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
  secret = (os.getenv("MEETINGBOX_WS_SHARED_SECRET", "") or "").strip()
  require_auth = os.getenv("MEETINGBOX_WS_REQUIRE_AUTH", "").strip() == "1"
  secret_ok = bool(secret) and (websocket.query_params.get("token") or "").strip() == secret
  actor_ok = bool(resolve_actor_from_access_token(_ws_bearer_from_request(websocket)))

  if not secret and not require_auth:
    pass  # backward compatible: open connect
  elif not secret and require_auth:
    if not actor_ok:
      await websocket.close(code=1008)
      return
  elif secret and not require_auth:
    if not secret_ok:
      await websocket.close(code=1008)
      return
  else:
    if not (secret_ok or actor_ok):
      await websocket.close(code=1008)
      return

  await manager.connect(websocket)
  try:
    while True:
      msg = await websocket.receive_text()
      await websocket.send_text(f"ack:{msg}")
  except WebSocketDisconnect:
    pass
  except Exception:
    logger.debug("WebSocket connection error", exc_info=True)
  finally:
    manager.disconnect(websocket)


@app.get("/health")
async def health() -> dict:
  return {"status": "healthy", "service": "meetingbox-web"}


class _SpaFallbackAPIRoute(APIRoute):
  """
  The SPA GET catch-all must not partially-match /api/*. Otherwise Starlette
  treats POST /api/... as "wrong method" on that GET route and returns
  405 Allow: GET — masking a missing API handler and breaking setup-complete.
  """

  def matches(self, scope: Scope) -> tuple[Match, Scope]:
    if scope.get("type") == "http":
      path = get_route_path(scope)
      if path == "/api" or path.startswith("/api/"):
        return Match.NONE, {}
    match, child_scope = super().matches(scope)
    if match != Match.NONE:
      child_scope["route"] = self
    return match, child_scope


if STATIC_DIR.exists():
  @app.get("/")
  async def serve_index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))

  async def serve_spa(full_path: str) -> FileResponse:
    requested = STATIC_DIR / full_path
    if requested.is_file():
      return FileResponse(str(requested))
    return FileResponse(str(STATIC_DIR / "index.html"))

  # FastAPI.add_api_route() does not accept route_class_override (0.109+); the
  # router method does — required so GET /{path} does not steal POST /api/*.
  app.router.add_api_route(
      "/{full_path:path}",
      serve_spa,
      methods=["GET"],
      response_class=FileResponse,
      route_class_override=_SpaFallbackAPIRoute,
      include_in_schema=False,
  )


if __name__ == "__main__":
  import uvicorn

  uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
