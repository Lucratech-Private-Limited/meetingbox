#!/usr/bin/env python3
"""
MeetingBox Onboarding Server

Lightweight HTTP server that runs on the HOST (not in Docker) during first-time
setup. Serves a WiFi configuration page on port 80 so users can connect via
the MeetingBox hotspot and enter their home WiFi credentials.

Flow:
  1. systemd starts this + hotspot on first boot (no .setup_complete marker)
  2. User connects phone to MeetingBox-XXXX hotspot
  3. Phone opens http://192.168.4.1
  4. User submits WiFi SSID + password
  5. Server responds IMMEDIATELY with success page (before switching WiFi)
  6. After 3s delay, Pi connects to user's WiFi, hotspot goes down
  7. .setup_complete is written — OLED advances to Home

Run: sudo python3 scripts/onboard_server.py
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

LISTEN_PORT = 80
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_MARKER = os.path.join(_PROJECT_ROOT, "data", "config", ".setup_complete")
HOTSPOT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hotspot.sh")
WIFI_SWITCH_DELAY = 3  # seconds — gives phone time to receive the response

# Global connection status: idle | connecting | connected | failed
_wifi_status = {"state": "idle", "message": ""}
_wifi_status_lock = threading.Lock()

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MeetingBox Setup</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #1C1C1E; color: #fff; min-height: 100vh;
    display: flex; justify-content: center; align-items: center;
    padding: 20px;
  }
  .card {
    background: #2C2C2E; border-radius: 16px; padding: 32px;
    max-width: 400px; width: 100%; box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }
  h1 { font-size: 24px; text-align: center; margin-bottom: 8px; }
  .subtitle { color: #8E8E93; text-align: center; margin-bottom: 24px; font-size: 14px; }
  label { display: block; color: #AEAEB2; font-size: 13px; margin-bottom: 6px; margin-top: 16px; }
  input[type=text], input[type=password] {
    width: 100%; padding: 12px 16px; border-radius: 10px;
    border: 1px solid #545458; background: #1C1C1E; color: #fff;
    font-size: 16px; outline: none;
  }
  input:focus { border-color: #3888FA; }
  .btn {
    width: 100%; padding: 14px; border: none; border-radius: 12px;
    background: linear-gradient(135deg, #3888FA, #2273F5);
    color: #fff; font-size: 17px; font-weight: 600;
    cursor: pointer; margin-top: 24px;
  }
  .btn:active { opacity: 0.8; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .status { text-align: center; margin-top: 16px; font-size: 14px; color: #8E8E93; }
  .status.error { color: #FF453A; }
  .status.success { color: #34C759; }
  .scan-list { max-height: 200px; overflow-y: auto; margin-top: 8px; }
  .scan-item {
    padding: 10px 14px; background: #1C1C1E; border-radius: 8px;
    margin-bottom: 6px; cursor: pointer; display: flex;
    justify-content: space-between; align-items: center;
  }
  .scan-item:hover { background: #38383A; }
  .scan-item .signal { color: #8E8E93; font-size: 12px; }
  .loader { display: none; text-align: center; padding: 20px; }
  .loader.active { display: block; }
  .success-card { text-align: center; }
  .success-card .checkmark { font-size: 48px; margin-bottom: 16px; }
  .success-card h1 { color: #34C759; margin-bottom: 12px; }
  .success-card .next-steps {
    background: #1C1C1E; border-radius: 12px; padding: 16px;
    margin-top: 20px; text-align: left;
  }
  .success-card .next-steps li {
    color: #AEAEB2; font-size: 14px; margin-bottom: 8px;
    list-style: none; padding-left: 20px; position: relative;
  }
  .success-card .next-steps li::before {
    content: attr(data-num); position: absolute; left: 0;
    color: #3888FA; font-weight: 600;
  }
  .success-card .url {
    color: #3888FA; font-weight: 600; font-size: 16px;
  }
  .success-card .note {
    color: #6E6E73; font-size: 12px; margin-top: 16px;
  }
</style>
</head>
<body>
<div class="card" id="setup-card">
  <h1>MeetingBox Setup</h1>
  <p class="subtitle">Connect your MeetingBox to WiFi</p>

  <div id="scan-section">
    <label>Available Networks</label>
    <div id="scan-list" class="scan-list"></div>
    <div id="scan-loader" class="loader active">Scanning for networks...</div>
  </div>

  <form id="wifi-form" onsubmit="return submitWifi(event)">
    <label for="ssid">WiFi Network Name</label>
    <input type="text" id="ssid" name="ssid" required placeholder="Select from above or type manually">

    <label for="password">WiFi Password</label>
    <input type="password" id="password" name="password" placeholder="Enter password">

    <button type="submit" class="btn" id="connect-btn">Connect</button>
  </form>

  <div id="status" class="status"></div>
</div>

<div class="card" id="connecting-card" style="display:none; text-align:center;">
  <h1>Connecting…</h1>
  <p class="subtitle">MeetingBox is connecting to <strong id="connecting-ssid"></strong>.</p>
  <p class="subtitle" style="margin-top:8px;">This may take up to 30 seconds. Please wait.</p>
  <div id="connect-loader" class="loader active" style="margin-top:16px;">⏳</div>
</div>

<div class="card" id="error-card" style="display:none; text-align:center;">
  <div style="font-size:48px; margin-bottom:16px;">✕</div>
  <h1 style="color:#FF453A;">Connection Failed</h1>
  <p id="error-message" class="subtitle" style="margin-top:8px;"></p>
  <button class="btn" style="margin-top:20px; background:linear-gradient(135deg,#FF6B6B,#FF453A);" onclick="retrySetup()">Try Again</button>
</div>

<div class="card success-card" id="success-card" style="display:none;">
  <div class="checkmark">&#10003;</div>
  <h1>WiFi Configured!</h1>
  <p class="subtitle">MeetingBox is connected to <strong id="connected-ssid"></strong>.</p>
  <p id="redirect-status" class="subtitle" style="margin-top:12px;">
    Redirecting to dashboard in <strong id="countdown">15</strong>s...
  </p>
  <p style="text-align:center; margin-top:16px;">
    <span class="url">meetingbox.local:8000</span>
  </p>
  <p id="redirect-fallback" class="note" style="display:none; margin-top:16px; color:#8E8E93; font-size:13px; text-align:center;">
    Redirect not working? Make sure your phone reconnected to your WiFi, then
    <a href="http://meetingbox.local:8000/" style="color:#3888FA;">tap here</a>
    or open <strong>http://meetingbox.local:8000</strong> in your browser.
  </p>
</div>

<script>
async function scanNetworks() {
  try {
    const res = await fetch('/api/scan');
    const networks = await res.json();
    const list = document.getElementById('scan-list');
    document.getElementById('scan-loader').classList.remove('active');
    if (networks.length === 0) {
      list.innerHTML = '<div class="scan-item">No networks found</div>';
      return;
    }
    list.innerHTML = '';
    networks.forEach(n => {
      const div = document.createElement('div');
      div.className = 'scan-item';
      div.innerHTML = '<span>' + n.ssid + '</span><span class="signal">' + n.signal + '%</span>';
      div.onclick = () => { document.getElementById('ssid').value = n.ssid; };
      list.appendChild(div);
    });
  } catch(e) {
    document.getElementById('scan-loader').textContent = 'Scan failed — enter network name manually';
    document.getElementById('scan-loader').classList.add('active');
  }
}

var _lastSsid = '';

async function submitWifi(e) {
  e.preventDefault();
  var ssid = document.getElementById('ssid').value;
  var password = document.getElementById('password').value;
  var status = document.getElementById('status');
  var btn = document.getElementById('connect-btn');
  _lastSsid = ssid;

  btn.disabled = true;
  btn.textContent = 'Saving...';
  status.className = 'status';
  status.textContent = 'Saving WiFi credentials...';

  try {
    var res = await fetch('/api/connect', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ssid: ssid, password: password})
    });
    var data = await res.json();
    if (data.status === 'saved') {
      document.getElementById('setup-card').style.display = 'none';
      document.getElementById('connecting-ssid').textContent = ssid;
      document.getElementById('connecting-card').style.display = 'block';
      pollConnectionStatus();
    } else {
      status.className = 'status error';
      status.textContent = data.message || 'Failed to save. Please try again.';
      btn.disabled = false;
      btn.textContent = 'Connect';
    }
  } catch(e) {
    status.className = 'status error';
    status.textContent = 'Something went wrong. Please try again.';
    btn.disabled = false;
    btn.textContent = 'Connect';
  }
}

function pollConnectionStatus() {
  var attempts = 0;
  var maxAttempts = 30;
  var interval = setInterval(async function() {
    attempts++;
    try {
      var res = await fetch('/api/status', {signal: AbortSignal.timeout(4000)});
      var data = await res.json();

      if (data.state === 'connected') {
        clearInterval(interval);
        document.getElementById('connecting-card').style.display = 'none';
        document.getElementById('connected-ssid').textContent = _lastSsid;
        document.getElementById('success-card').style.display = 'block';
        startRedirectCountdown();
        return;
      }
      if (data.state === 'failed') {
        clearInterval(interval);
        document.getElementById('connecting-card').style.display = 'none';
        document.getElementById('error-message').textContent =
          data.message || 'Could not connect to the network.';
        document.getElementById('error-card').style.display = 'block';
        return;
      }
    } catch(e) {
      // Phone lost connection to hotspot — keep polling; hotspot may be restarting
    }
    if (attempts >= maxAttempts) {
      clearInterval(interval);
      document.getElementById('connecting-card').style.display = 'none';
      document.getElementById('error-message').textContent =
        'Connection timed out. The password may be wrong or the network is out of range.';
      document.getElementById('error-card').style.display = 'block';
    }
  }, 2000);
}

function retrySetup() {
  document.getElementById('error-card').style.display = 'none';
  document.getElementById('setup-card').style.display = 'block';
  document.getElementById('status').textContent = '';
  document.getElementById('connect-btn').disabled = false;
  document.getElementById('connect-btn').textContent = 'Connect';
  document.getElementById('password').value = '';
  scanNetworks();
}

function startRedirectCountdown() {
  var seconds = 15;
  var el = document.getElementById('countdown');
  var timer = setInterval(function() {
    seconds--;
    el.textContent = seconds;
    if (seconds <= 0) {
      clearInterval(timer);
      document.getElementById('redirect-status').textContent = 'Redirecting now...';
      document.getElementById('redirect-fallback').style.display = 'block';
      window.location.href = 'http://meetingbox.local:8000/';
    }
  }, 1000);
}

scanNetworks();
</script>
</body>
</html>
"""


class OnboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[Onboard] {args[0]}", flush=True)

    def do_GET(self):
        if self.path == "/api/scan":
            self._handle_scan()
        elif self.path == "/api/status":
            self._handle_status()
        else:
            self._serve_page()

    def do_POST(self):
        if self.path == "/api/connect":
            self._handle_connect()
        else:
            self.send_error(404)

    def _serve_page(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode())

    def _handle_scan(self):
        networks = []
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
                capture_output=True, text=True, timeout=15,
            )
            seen = set()
            for line in result.stdout.strip().splitlines():
                parts = line.split(":")
                if len(parts) >= 3 and parts[0] and parts[0] not in seen:
                    if parts[0].startswith("MeetingBox-"):
                        continue
                    seen.add(parts[0])
                    networks.append({
                        "ssid": parts[0],
                        "signal": int(parts[1]) if parts[1].isdigit() else 0,
                        "security": parts[2] or "open",
                    })
            networks.sort(key=lambda n: n["signal"], reverse=True)
        except Exception as e:
            print(f"[Onboard] Scan error: {e}", flush=True)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(networks).encode())

    def _handle_status(self):
        global _wifi_status
        with _wifi_status_lock:
            data = dict(_wifi_status)
        self._json_response(data)

    def _handle_connect(self):
        global _wifi_status
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        ssid = body.get("ssid", "")
        password = body.get("password", "")

        if not ssid:
            self._json_response({"status": "failed", "message": "No SSID provided"})
            return

        print(f"[Onboard] Saving WiFi credentials for: {ssid}", flush=True)

        try:
            subprocess.run(
                ["nmcli", "connection", "delete", ssid],
                capture_output=True, text=True, timeout=10,
            )

            if password:
                result = subprocess.run(
                    ["nmcli", "connection", "add",
                     "type", "wifi",
                     "ifname", "wlan0",
                     "con-name", ssid,
                     "ssid", ssid,
                     "autoconnect", "yes",
                     "--",
                     "wifi-sec.key-mgmt", "wpa-psk",
                     "wifi-sec.psk", password,
                     "wifi-sec.psk-flags", "0"],  # store PSK in profile file, not keyring
                    capture_output=True, text=True, timeout=15,
                )
            else:
                result = subprocess.run(
                    ["nmcli", "connection", "add",
                     "type", "wifi",
                     "ifname", "wlan0",
                     "con-name", ssid,
                     "ssid", ssid,
                     "autoconnect", "yes"],
                    capture_output=True, text=True, timeout=15,
                )

            if result.returncode != 0:
                msg = result.stderr.strip() or result.stdout.strip() or "Failed to save credentials"
                print(f"[Onboard] Failed to create profile: {msg}", flush=True)
                self._json_response({"status": "failed", "message": msg})
                return

            print(f"[Onboard] WiFi profile created for {ssid}", flush=True)

            with _wifi_status_lock:
                _wifi_status = {"state": "connecting", "message": f"Connecting to {ssid}…"}

            self._json_response({"status": "saved", "ssid": ssid})

            def delayed_wifi_switch():
                global _wifi_status
                print(f"[Onboard] Waiting {WIFI_SWITCH_DELAY}s before switching WiFi...", flush=True)
                time.sleep(WIFI_SWITCH_DELAY)

                print(f"[Onboard] Activating WiFi connection: {ssid}", flush=True)
                connect_result = subprocess.run(
                    ["nmcli", "connection", "up", ssid],
                    capture_output=True, text=True, timeout=30,
                )

                if connect_result.returncode == 0:
                    print(f"[Onboard] WiFi connected to {ssid}", flush=True)
                    with _wifi_status_lock:
                        _wifi_status = {"state": "connected", "message": ""}

                    marker = Path(SETUP_MARKER)
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.write_text("1")
                    print(f"[Onboard] Setup marker written: {SETUP_MARKER}", flush=True)

                    subprocess.run(
                        ["bash", HOTSPOT_SCRIPT, "stop"],
                        capture_output=True, text=True, timeout=15,
                    )
                    print("[Onboard] Hotspot stopped", flush=True)

                    print("[Onboard] Stopping onboard server...", flush=True)
                    self.server.shutdown()
                    self.server.server_close()

                    for _attempt in range(10):
                        check = subprocess.run(
                            ["ss", "-tlnp"],
                            capture_output=True, text=True, timeout=5,
                        )
                        if ":80 " not in check.stdout:
                            break
                        time.sleep(1)
                    else:
                        print("[Onboard] WARNING: port 80 still held after 10s", flush=True)

                    print("[Onboard] Starting nginx for normal operation...", flush=True)
                    subprocess.run(
                        ["docker", "compose", "up", "-d", "nginx"],
                        capture_output=True, text=True, timeout=60,
                        cwd=_PROJECT_ROOT,
                    )
                    print("[Onboard] Onboarding complete — nginx started", flush=True)
                else:
                    msg = connect_result.stderr.strip() or "Connection failed"
                    print(f"[Onboard] WiFi activation failed: {msg}", flush=True)

                    subprocess.run(
                        ["nmcli", "connection", "delete", ssid],
                        capture_output=True, text=True, timeout=10,
                    )

                    print("[Onboard] Restarting hotspot after failed connection...", flush=True)
                    subprocess.run(
                        ["bash", HOTSPOT_SCRIPT, "start"],
                        capture_output=True, text=True, timeout=20,
                    )
                    print("[Onboard] Hotspot restarted — user can retry", flush=True)

                    with _wifi_status_lock:
                        _wifi_status = {
                            "state": "failed",
                            "message": "Wrong password or network unreachable. Please try again.",
                        }

            threading.Thread(target=delayed_wifi_switch, daemon=False).start()

        except Exception as e:
            print(f"[Onboard] Error: {e}", flush=True)
            with _wifi_status_lock:
                _wifi_status = {"state": "failed", "message": str(e)}
            self._json_response({"status": "failed", "message": str(e)})

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


def main():
    if os.geteuid() != 0:
        print("[Onboard] ERROR: Must run as root (port 80 requires root)", flush=True)
        sys.exit(1)

    marker = Path(SETUP_MARKER)
    if marker.exists():
        print("[Onboard] Setup already complete — exiting", flush=True)
        sys.exit(0)

    print(f"[Onboard] Marker path: {SETUP_MARKER}", flush=True)
    print(f"[Onboard] Project root: {_PROJECT_ROOT}", flush=True)
    print(f"[Onboard] Starting onboarding server on 0.0.0.0:{LISTEN_PORT}", flush=True)

    try:
        server = http.server.HTTPServer(("0.0.0.0", LISTEN_PORT), OnboardHandler)
    except OSError as e:
        print(f"[Onboard] ERROR: Cannot bind to port {LISTEN_PORT}: {e}", flush=True)
        print(f"[Onboard] Is another process using port {LISTEN_PORT}? Check with: sudo ss -tlnp | grep :{LISTEN_PORT}", flush=True)
        sys.exit(1)

    print(f"[Onboard] READY — listening on http://0.0.0.0:{LISTEN_PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[Onboard] Server stopped", flush=True)


if __name__ == "__main__":
    main()
