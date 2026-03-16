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

From the main MeetingBox directory:
```bash
sudo ./scripts/install_device_ui.sh
```

### Manual Install
```bash
# Navigate to device-ui directory
cd device-ui/

# Install Python dependencies
pip3 install -r requirements.txt

# Install system dependencies
sudo apt-get install -y libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev

# Test run (will use mock backend)
MOCK_BACKEND=1 python3 src/main.py
```

## Configuration

Edit `src/config.py` to configure:

- Backend API URL (default: http://localhost:8000)
- Display resolution
- Touch calibration
- UI theme colors

## Running

### As Systemd Service (Production)
```bash
sudo systemctl start meetingbox-ui
sudo systemctl enable meetingbox-ui  # Auto-start on boot
```

### Manual Run (Development)
```bash
# With real backend
python3 src/main.py

# With mock backend (for testing without hardware)
MOCK_BACKEND=1 python3 src/main.py
```

## Development

### Project Structure
```
device-ui/
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
