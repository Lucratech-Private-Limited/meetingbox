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
from auth import get_optional_user
from routes.device import SetupCompleteBody, finalize_first_boot_setup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("meetingbox.web")

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

app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
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
