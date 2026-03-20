# MeetingBox -- Linux Deployment Guide

Deploy the full MeetingBox stack on a VirtualBox Ubuntu VM (or any Linux host) with a real USB microphone.

## Prerequisites

- **VirtualBox 7+** with Extension Pack (for USB passthrough)
- **Ubuntu Server 24.04 LTS** ISO
- A **USB microphone** (e.g. ReSpeaker Mic Array)
- An **Anthropic API key** for Claude

## Phase 1: Create and Configure the VM

### 1.1 Create the VM

In VirtualBox, create a new VM:

| Setting | Value |
|---------|-------|
| Name | `meetingbox-vm` |
| Type | Linux / Ubuntu 64-bit |
| RAM | 4 GB minimum (8 GB recommended) |
| CPU | 4 cores |
| Disk | 40 GB dynamically allocated |

Attach the Ubuntu Server ISO and install with defaults. Create user `meetingbox`.

### 1.2 Network Configuration

Set up two network adapters:

- **Adapter 1 -- NAT**: Internet access for pulling Docker images, packages
- **Adapter 2 -- Host-Only**: Windows-to-VM access on a stable IP

In VirtualBox: *File > Host Network Manager* -- create a host-only network (e.g. `192.168.56.0/24`).

After the VM boots, configure the host-only adapter:

```bash
# Create /etc/netplan/01-host-only.yaml (the setup script does this automatically)
sudo netplan apply
ip addr show enp0s8   # note the IP, e.g. 192.168.56.101
```

### 1.3 USB Microphone Passthrough

1. Install the **VirtualBox Extension Pack** on the Windows host.
2. In VM Settings > USB: enable USB 2.0/3.0 controller.
3. Add a USB Device Filter for your microphone.
4. Start the VM and verify inside:

```bash
arecord -l              # lists USB mic as a capture device
arecord -d 3 test.wav   # record 3 seconds
```

If `arecord -l` shows nothing, install ALSA utils: `sudo apt install -y alsa-utils`.

## Phase 2: Deploy the Stack

### 2.1 Run the Setup Script

Transfer the repo to the VM (git clone, scp, or VirtualBox shared folder), then:

```bash
cd /opt/meetingbox
chmod +x scripts/setup_vm.sh
sudo ./scripts/setup_vm.sh
sudo reboot
```

This installs Docker, Docker Compose, Node.js, ALSA utils, and configures mDNS.

### 2.2 Configure Environment

```bash
cd /opt/meetingbox
cp .env.example .env
nano .env
# Set your real ANTHROPIC_API_KEY
```

### 2.3 Build the Frontend

```bash
cd /opt/meetingbox/frontend
npm install
npm run build
cd ..
```

### 2.4 Build and Start Containers

```bash
docker compose up --build -d
```

First build takes a while (Whisper model download ~1.5 GB, whisper.cpp compilation).

Monitor progress:

```bash
docker compose logs -f
```

Verify all 5 containers are running:

```bash
docker compose ps
```

Expected containers: `meetingbox-redis`, `meetingbox-audio`, `meetingbox-transcription`, `meetingbox-ai`, `meetingbox-web`.

## Phase 3: Validation Tests

Open `http://<VM_IP>:8000` from a Windows browser (or `http://meetingbox.local` if mDNS works).

### Test 1: Dashboard Loads

- Dashboard renders without errors
- "Backend not reachable" banner does NOT appear
- Both "Start meeting" and "Record with my mic" buttons visible

### Test 2: Browser Mic Recording (Regression)

- Click "Record with my mic" > speak 10 seconds > "Stop & send"
- Verify transcript appears, then click "Summarize with AI"
- Verify summary, decisions, action items, topics appear

### Test 3: Device Mic Recording (Appliance Flow)

This is the critical test -- it verifies the USB mic works through Docker:

- Click **"Start meeting"** > speak 10-30 seconds > **"Stop & process"**
- Check `docker compose logs audio` for recording events
- Check `docker compose logs transcription` for processing events
- Open the meeting and verify transcript is populated
- Click "Summarize with AI" and verify output

### Test 4: WAV Ingest (CLI)

```bash
pip install requests
python scripts/ingest_test_wav.py /path/to/test.wav --base http://localhost:8000
```

### Test 5: Duration Stress Test

- Record a 5-minute meeting using "Start meeting"
- Verify full transcript and summary generate correctly

### Test 6: Persistence After Restart

```bash
docker compose down
docker compose up -d
```

Verify all previous meetings and transcripts are still accessible.

## Phase 4: Performance Benchmarking

Capture these metrics to decide between Pi 5 and mini PC:

| Metric | How to Measure | Target |
|--------|---------------|--------|
| Whisper speed | `docker compose logs transcription` timestamps | Under 2x real-time |
| Memory during transcription | `docker stats` | Under 3 GB peak |
| CPU during transcription | `docker stats` / `htop` | All cores utilized |
| Total pipeline latency | "Stop & process" to transcript | Under 3 min for 60s audio |
| AI summary latency | Click "Summarize" to result | Under 10 seconds |
| Idle resources | `docker stats` at rest | Under 500 MB / 5% CPU |

To simulate Pi 5 constraints, reduce the VM to 4 cores and 4 GB RAM.

## Notes

- The `docker-compose.yml` includes `devices: ["/dev/snd:/dev/snd"]` and `group_add: [audio]` on the audio service. These are required on Linux for USB mic access and will cause Docker errors on Windows. Remove or comment them out for Windows Docker Desktop development.
- The same `docker-compose.yml` works identically on any Linux host (VM, Pi 5, mini PC).
- Data persists in `./data/` (SQLite DB in `data/transcripts/`, audio in `data/audio/`).
