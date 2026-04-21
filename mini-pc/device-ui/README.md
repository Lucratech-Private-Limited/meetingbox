# MeetingBox Device UI

Device UI for MeetingBox hardware.

## Features

- Touch-first interface for kiosk and appliance displays
- Real-time recording status and live captions
- Meeting history browser
- Settings management (WiFi, integrations, system info)
- Connects to MeetingBox backend API (localhost:8000)

## Hardware Requirements

- Linux host capable of running X11
- Display connected via HDMI, DisplayPort, or embedded panel
- Optional touch input if the device UI is used interactively

## Installation

### Quick Install (Automated)

From the main MeetingBox repo (full checkout):
```bash
sudo ./scripts/install_device_ui.sh
```

### Manual Install
```bash
# Navigate to device-ui (standalone: clone mini-pc repo and cd device-ui)
cd mini-pc/device-ui/

# Create and activate a virtual environment (recommended for local dev)
python3 -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip3 install -r requirements.txt

# Install system dependencies
sudo apt-get install -y libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev

# Test run (will use mock backend)
MOCK_BACKEND=1 python3 src/main.py
```

### Run in venv without Docker (fast UI iteration)

Docker rebuilds are slow; use a local venv and run `main.py` directly. System SDL2/Kivy packages still apply on Linux (see above); on Windows, `pip install -r requirements.txt` is usually enough for Kivy’s wheels.

**Linux / mini PC (bash):**

```bash
cd mini-pc/device-ui
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Optional: copy mini-pc/.env.example → mini-pc/.env and edit BACKEND_URL
./run_device_ui.sh
# Or mock-only:
MOCK_BACKEND=1 ./run_device_ui.sh
```

`run_device_ui.sh` loads `../.env` (mini-pc), optional monorepo `../../.env`, and `device-ui/.env`, then runs `python3 src/main.py`.

**Windows (PowerShell):**

```powershell
cd mini-pc\device-ui
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\run_device_ui.ps1
# Or:
$env:MOCK_BACKEND = "1"; .\run_device_ui.ps1
```

Use `FULLSCREEN=0` in `.env` (default for local `config.py`) for a windowed app while developing.

**Bare-metal + real backend:** If Redis is not hostname `redis`, set e.g. `LOCAL_REDIS_HOST=127.0.0.1` in `.env` when you need live audio-level from a local Redis.

## Configuration

**Cloud / remote server** — easiest: copy `mini-pc/.env.example` → `mini-pc/.env` (or `.env` in this directory), edit `BACKEND_URL`, then `./run_device_ui.sh`. Or set environment variables in systemd `Environment=`:

| Variable | Example | Purpose |
|----------|---------|---------|
| `BACKEND_URL` | `http://your-host:8000` | REST API base (no trailing slash) |
| `BACKEND_WS_URL` | `ws://your-host:8000/ws` | Real-time captions (optional; derived from `BACKEND_URL` if unset) |
| `DASHBOARD_URL` | `http://your-host:8000` or `your-host:8000` | Dashboard link/QR; full URL or host:port (trailing `/` ok) |
| `MOCK_BACKEND` | `1` | Local testing without a server |

**Host audio recorder** (separate process): same API host as `UPLOAD_AUDIO_API_URL`, and `REDIS_HOST`/`REDIS_PORT` must reach the **same Redis** the cloud `web` container uses (usually VPN or private network; default server compose binds Redis to localhost only).

You can still edit `src/config.py` for display resolution, touch, and theme.

## Running

### As Systemd Service (Production)
```bash
sudo systemctl start meetingbox-ui
sudo systemctl enable meetingbox-ui  # Auto-start on boot
```

### Manual Run (Development)
```bash
# Run locally without Docker (recommended during UI iteration)
cd mini-pc/device-ui   # or ./device-ui when using mini-pc-only repo
source .venv/bin/activate

# Match Figma canvas scale
export DISPLAY_WIDTH=1024
export DISPLAY_HEIGHT=600

# With real backend
python3 src/main.py

# With mock backend (for testing without hardware)
MOCK_BACKEND=1 python3 src/main.py
```

## Development

### Project Structure
```
mini-pc/device-ui/
├── src/
│   ├── main.py              # Application entry point
│   ├── config.py            # Configuration
│   ├── api_client.py        # Backend API client
│   ├── mock_backend.py      # Mock for testing
│   ├── screens/             # UI screens
│   └── components/          # Reusable widgets
├── assets/                  # Fonts, icons
├── tests/                   # Unit tests
└── requirements.txt
```

### Running Tests
```bash
pytest tests/
```

### Logs
```bash
# View logs (systemd service)
journalctl -u meetingbox-ui -f

# Or check local logs
tail -f /var/log/meetingbox-ui.log
```

## Troubleshooting

**Display not working:**
- Check HDMI/DSI connection
- Verify display is recognized: `DISPLAY=:0 xrandr`
- Check display permissions: user must be in `video` group

**Touch not responding:**
- Check touch device: `ls /dev/input/event*`
- Test touch: `evtest /dev/input/event0`

**Backend connection failed:**
- Verify backend is running: `curl http://localhost:8000/api/health`
- Check logs: `journalctl -u meetingbox-backend -f`

**Performance issues:**
- Lower FPS in config.py (default 30, try 20)
- Reduce display resolution
- Check CPU usage: `htop`

## API Documentation

Device UI communicates with backend via REST API + WebSocket.

See: `../backend/API.md` for complete API documentation.

## License

See main MeetingBox LICENSE file.
