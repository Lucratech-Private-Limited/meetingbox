# Building MeetingBox for Windows

This produces the Windows desktop build of the MeetingBox appliance: a single
application folder containing `MeetingBox.exe` (the Kivy UI) and its bundled
audio-capture child `meetingbox-audio.exe`, plus a shared `_internal\` payload.

> **Why two .exe files?** This mirrors the Docker appliance exactly. In
> `docker-compose.yml` there is one `device-ui` container that runs
> `python main.py` and internally spawns `audio_capture.py` as a child process
> (via `audio_supervisor.py`). Once frozen by PyInstaller, the UI binary can't
> run a `.py` script, so the audio child ships as a sibling `meetingbox-audio.exe`.
> It is **not** a separate service — `MeetingBox.exe` launches and manages it
> automatically. Keep the whole `MeetingBox\` folder together.

---

## Prerequisites (install once)

- **Python 3.11** (64-bit) from [python.org](https://www.python.org/downloads/) —
  during install, tick **"Add python.exe to PATH"**.
  - Use **3.11 specifically.** The required wheels (`vosk`, `pyaudio`,
    `kivy_deps.sdl2/glew/angle`, Kivy 2.3.1) ship prebuilt `cp311` wheels.
    Newer Python (3.13+) may have no matching wheels and the install can fail.
- **Git** — [git-scm.com](https://git-scm.com/download/win).
- *(Optional, only to build the installer)* **Inno Setup 6** —
  [jrsoftware.org](https://jrsoftware.org/isdl.php).

Verify Python:

```powershell
py -3.11 --version
# Python 3.11.x
```

---

## Build commands (PowerShell)

Run from wherever you want the source checkout to live.

```powershell
# 1. Get the code
git clone https://github.com/trilokpotluri27/meetingbox-mini-pc-release.git
cd meetingbox-mini-pc-release

# 2. Create + activate a Python 3.11 virtual environment
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install the app deps + the build toolchain
python -m pip install --upgrade pip
python -m pip install -r device-ui\requirements.txt
python -m pip install -r packaging\windows\requirements-build.txt

# 4. Build both exes (UI + audio child) into one folder
python -m PyInstaller packaging\windows\MeetingBox.spec --noconfirm `
  --distpath packaging\windows\dist --workpath packaging\windows\build
```

**Output:** `packaging\windows\dist\MeetingBox\MeetingBox.exe`, with
`meetingbox-audio.exe` and `_internal\` beside it. Ship the whole folder.

### Notes on harmless build output
PyInstaller prints a few `ERROR: Hidden import '...' not found` lines for
`soundfile`, `redis`, `scipy`, and `scipy.signal`. These are **optional**
imports listed defensively in the spec; they are not needed on Windows and do
not affect the build. Likewise the `CRITICAL [Camera]` / `[Spelling]` lines from
Kivy are normal (those providers don't exist on a headless build machine).

---

## Optional: build the installer (`MeetingBoxSetup.exe`)

The Inno Setup script expects the bundle at `packaging\windows\dist\MeetingBox`
(the default `--distpath` above), so just run:

```powershell
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" packaging\windows\MeetingBox.iss
```

**Output:** `packaging\windows\Output\MeetingBoxSetup.exe`. It installs the
bundle into Program Files, seeds `%PROGRAMDATA%\MeetingBox\device-ui.env`
(only if absent), and creates Start Menu / optional desktop shortcuts.

---

## Running the build

Launch `MeetingBox.exe` from inside the `MeetingBox\` folder (it needs its
`_internal\` payload and sibling audio exe next to it).

Configuration comes from `device-ui.env`. For an installed copy this lives at
`%PROGRAMDATA%\MeetingBox\device-ui.env`; the bundled default
(`packaging\windows\device-ui.env`) points at the cloud backend.

- **Real backend:** set `BACKEND_URL` (and pair the device) in `device-ui.env`.
- **Quick local UI test (no backend):** set `MOCK_BACKEND=1`. In mock mode the
  app does not spawn the audio child (there is no backend to record/upload to),
  so you won't see audio-command polling.

```powershell
$env:MOCK_BACKEND = "1"
.\packaging\windows\dist\MeetingBox\MeetingBox.exe
```

### If Windows blocks the exe
On machines with an **Application Control policy** (WDAC / AppLocker / Smart App
Control), an unsigned `MeetingBox.exe` may be blocked
(*"An Application Control policy has blocked this file"*). For distribution to
such machines the executables need to be **code-signed**, or the policy must
allow them. This is a Windows security-policy issue, not a build problem.
