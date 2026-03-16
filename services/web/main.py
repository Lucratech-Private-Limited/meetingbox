from contextlib import asynccontextmanager
import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import redis

from database import init_database, get_connection
from routes.meetings import router as meetings_router
from routes.system import router as system_router
from routes.device import router as device_router
from routes.auth import router as auth_router
from routes.actions import router as actions_router
from routes.integrations import router as integrations_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("meetingbox.web")

init_database()

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
app.include_router(meetings_router, prefix="/api/meetings", tags=["meetings"])
app.include_router(system_router, prefix="/api/system", tags=["system"])
app.include_router(device_router, prefix="/api/device", tags=["device"])
app.include_router(actions_router, prefix="/api", tags=["actions"])
app.include_router(integrations_router, prefix="/api", tags=["integrations"])


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


if STATIC_DIR.exists():
  @app.get("/")
  async def serve_index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


  @app.get("/{full_path:path}")
  async def serve_spa(full_path: str) -> FileResponse:
    requested = STATIC_DIR / full_path
    if requested.is_file():
      return FileResponse(str(requested))
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
  import uvicorn

  uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
