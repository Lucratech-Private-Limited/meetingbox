# MeetingBox Native Ubuntu Mini-PC Deployment

This is the non-Docker appliance path for the Intel mini-PC / old DDR3 mini-PC build.
It now supports two layers:

- `native runtime`: host services instead of Docker
- `sealed appliance mode`: hide Ubuntu surfaces and keep only a controlled maintenance escape hatch

## What changes from the Raspberry Pi path

- No Docker Compose
- No nginx container
- No Pi boot config or Pi display assumptions
- Native `systemd` services for audio, transcription, AI, web, and touchscreen UI
- Native `redis-server` and native `ollama`
- Native X server on `tty1` for the touchscreen UI

## Boot strategy

At boot, the mini PC should behave like an appliance:

1. Ubuntu boots with `quiet splash`
2. `meetingbox-x.service` starts a dedicated X server on `:0`
3. `redis-server` and `ollama` start as host services
4. `meetingbox.target` starts:
   - `meetingbox-web.service`
   - `meetingbox-audio.service`
   - `meetingbox-transcription.service`
   - `meetingbox-ai.service`
   - `meetingbox-ui.service`
5. The Kivy UI connects directly to the local backend on `http://localhost:8000`

## Runtime layout

- App root: `/opt/meetingbox`
- Env file: `/etc/meetingbox/meetingbox.env`
- Data:
  - `/opt/meetingbox/data/audio/temp`
  - `/opt/meetingbox/data/audio/recordings`
  - `/opt/meetingbox/data/transcripts`
  - `/opt/meetingbox/data/config`
- Python virtualenvs: `/opt/meetingbox/.venvs`
- Whisper.cpp runtime: `/opt/meetingbox/runtime/whisper.cpp`

## Installer

Run on the target Ubuntu mini PC:

```bash
cd /path/to/meetingbox
sudo bash scripts/install_native_minipc.sh
```

Then edit:

```bash
sudo nano /etc/meetingbox/meetingbox.env
```

Set at minimum:

- `JWT_SECRET_KEY`
- `ANTHROPIC_API_KEY` if Claude fallback is still needed
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `LOCAL_LLM_MODEL` if you want a different Ollama model
- display width and height if the touchscreen is not `480x320`

## Seal it into appliance mode

After validating the native runtime on the mini PC, seal it:

```bash
sudo bash /opt/meetingbox/scripts/seal_native_appliance.sh
sudo reboot
```

This does the following:

- hides GRUB/menu noise
- masks `tty1` shell access
- disables suspend, hibernate, and hybrid sleep
- disables common desktop-facing Ubuntu services that do not help the appliance
- keeps one explicit maintenance path instead of leaving the box generally interactive

## Maintenance mode

The sealed image still keeps a support path.

To temporarily expose a maintenance shell on `tty2`:

```bash
sudo /usr/local/bin/meetingbox-enter-maintenance
sudo reboot
```

To disable it again:

```bash
sudo /usr/local/bin/meetingbox-exit-maintenance
sudo reboot
```

## Services

```bash
systemctl status meetingbox.target
systemctl status meetingbox-x
systemctl status meetingbox-web
systemctl status meetingbox-ui
```

Logs:

```bash
journalctl -u meetingbox.target -f
journalctl -u meetingbox-web -f
journalctl -u meetingbox-ui -f
journalctl -u meetingbox-transcription -f
```

## Why this fits the mini-PC better

- It removes Raspberry Pi boot/display setup from the production path
- It removes Docker overhead from the appliance runtime
- It keeps all processing local on the device
- It uses host-native services, which are easier to tune for latency and boot order
- It can be sealed so the product behaves like an appliance instead of a visible Ubuntu workstation

## Remaining assumptions

- Ubuntu has `NetworkManager` for Wi-Fi operations
- Touch input is exposed through normal X/libinput on the host
- Ollama remains the local summarization runtime
- The browser dashboard is served directly by FastAPI from `frontend/dist`
- Sealed mode intentionally prioritizes appliance behavior over ad hoc local administration
