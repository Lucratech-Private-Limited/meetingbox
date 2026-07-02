# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the MeetingBox Windows desktop port.

Builds TWO one-dir executables that share a single ``_internal`` payload:

  * ``MeetingBox.exe``        – the Kivy UI (entry: device-ui/src/main.py)
  * ``meetingbox-audio.exe``  – the audio capture child (entry: audio/audio_capture.py)

The UI's ``audio_supervisor`` launches ``meetingbox-audio.exe`` as a sibling
process (it cannot run ``audio_capture.py`` as a script once frozen, because
``sys.executable`` is the UI binary).

Run from the repo root:

    device-ui\\.venv\\Scripts\\pyinstaller.exe --noconfirm packaging\\windows\\MeetingBox.spec
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)
from kivy_deps import sdl2, glew, angle

# ``SPECPATH`` is injected by PyInstaller at runtime.
REPO_ROOT = Path(SPECPATH).resolve().parent.parent  # packaging/windows -> repo root
DEVICE_UI = REPO_ROOT / "device-ui"
SRC = DEVICE_UI / "src"
AUDIO = REPO_ROOT / "audio"

# ---------------------------------------------------------------------------
# Shared data files (land under _internal/, i.e. sys._MEIPASS at runtime).
# ---------------------------------------------------------------------------
datas = []
datas += [(str(DEVICE_UI / "assets"), "assets")]
if (AUDIO / "config.yaml").is_file():
    datas += [(str(AUDIO / "config.yaml"), ".")]

# Bundle a default desktop env next to the exe payload as a fallback.
_env_template = REPO_ROOT / "packaging" / "windows" / "device-ui.env"
if _env_template.is_file():
    datas += [(str(_env_template), ".")]

# Vendored Speex DSP library for acoustic echo cancellation + noise suppression
# on the realtime voice path. There is no system libspeexdsp on Windows (the
# Linux appliance got it from the OS), so _aec.py loads this bundled copy from
# _internal/vendor/windows/. Without it the voice falls back to half-duplex
# mic-mute: the assistant reacts to its own audio and the mic input is noisy.
_speexdsp_dll = SRC / "vendor" / "windows" / "libspeexdsp.dll"
if _speexdsp_dll.is_file():
    datas += [(str(_speexdsp_dll), os.path.join("vendor", "windows"))]

# Third-party packages that ship data / need full collection.
#
# ``pywebrtc_audio``  – genuine WebRTC AEC3 (native _webrtc_audio*.pyd). Our
#   built-from-source wheel (pybind11<3); powers the preferred full-duplex echo
#   path via webrtc_apm.py. collect_all grabs the package + its .pyd.
# ``pyaudiowpatch``   – WASAPI loopback capture for the AEC far-end reference.
#   Its native extension is the top-level ``_portaudiowpatch`` .pyd (pinned as a
#   hidden import below so PyInstaller bundles it).
binaries = []
hiddenimports = []
for pkg in ("vosk", "sounddevice", "_cffi_backend", "cffi",
            "pywebrtc_audio", "pyaudiowpatch"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# Explicitly bundle the native extension modules for AEC3 + WASAPI loopback.
# collect_all returns them only as hidden imports (not binaries), so pin the
# actual ``.pyd`` files by path to guarantee they land in the payload:
#   * pywebrtc_audio/_webrtc_audio*.pyd  -> inside the package dir
#   * _portaudiowpatch*.pyd              -> top-level (PyAudioWPatch's C ext)
import importlib.util as _ilu
for _mod, _dest in (("pywebrtc_audio._webrtc_audio", "pywebrtc_audio"),
                    ("_portaudiowpatch", ".")):
    try:
        _spec = _ilu.find_spec(_mod)
        if _spec and _spec.origin and os.path.isfile(_spec.origin):
            binaries += [(_spec.origin, _dest)]
    except Exception:
        pass

# certifi CA bundle (httpx / websockets TLS).
try:
    datas += collect_data_files("certifi")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Hidden imports — our own packages plus dynamically imported deps.
# ---------------------------------------------------------------------------
hiddenimports += collect_submodules("screens")
hiddenimports += collect_submodules("components")
# websockets.connect lazily imports websockets.asyncio.client via __getattr__,
# so the submodule is a *dynamic* import. The contrib hook normally collects it,
# but pin it explicitly so a hook change can never drop it (realtime voice +
# device-events WS both rely on it).
hiddenimports += collect_submodules("websockets")
hiddenimports += [
    "single_instance",
    "env_file",
    "net_status",
    "tts_windows",
    "audio_output",
    # OS-grade AEC via the Windows Voice Capture DSP (lazy-imported inside
    # RealtimeVoiceSession; drives comtypes COM/DMO directly).
    "windows_aec",
    "windows_aec_dmo",
    # Preferred desktop echo path: genuine WebRTC AEC3 + WASAPI loopback
    # reference. All lazy-imported inside RealtimeVoiceSession, so pin them so
    # static analysis can never drop them. ``_portaudiowpatch`` is PyAudioWPatch's
    # top-level native extension; ``_webrtc_audio`` is the AEC3 .pyd.
    "webrtc_apm",
    "aec_reference",
    "aec_reference_windows",
    "aec_reference_macos",
    "pywebrtc_audio",
    "pywebrtc_audio._webrtc_audio",
    "pyaudiowpatch",
    "_portaudiowpatch",
    "google_signin",
    "mic_permission",
    "kivy.core.window.window_sdl2",
    "kivy.core.text.text_sdl2",
    "kivy.core.image.img_sdl2",
    "kivy.core.audio.audio_sdl2",
    "kivy.core.clipboard.clipboard_sdl2",
    "pyttsx3",
    "pyttsx3.drivers",
    "pyttsx3.drivers.sapi5",
    "comtypes",
    "comtypes.client",
    "comtypes.stream",
    "win32com",
    "win32com.client",
    "sounddevice",
    "soundfile",
    "vosk",
    "numpy",
    "yaml",
    "httpx",
    "websockets",
    "qrcode",
    "PIL",
    "PIL.Image",
]

# webrtcvad is optional on Windows (capture forces the sounddevice backend and
# guards self.vad). The stock pyinstaller-hooks-contrib hook for ``webrtcvad``
# is incompatible with the ``webrtcvad-wheels`` build, so exclude it entirely.
VAD_EXCLUDES = ["webrtcvad", "webrtcvad_wheels"]

block_cipher = None

PATHEX = [str(SRC), str(AUDIO), str(REPO_ROOT)]

# ---------------------------------------------------------------------------
# UI analysis
# ---------------------------------------------------------------------------
ui_a = Analysis(
    [str(SRC / "main.py")],
    pathex=PATHEX,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"] + VAD_EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ---------------------------------------------------------------------------
# Audio child analysis
# ---------------------------------------------------------------------------
audio_hidden = [
    "pyaudio",
    "sounddevice",
    "numpy",
    "yaml",
    "redis",
    "httpx",
    "requests",
    "scipy",
    "scipy.signal",
    # Audio child pins urllib TLS to the certifi bundle (see _install_certifi_opener).
    "certifi",
]

audio_a = Analysis(
    [str(AUDIO / "audio_capture.py")],
    pathex=PATHEX,
    binaries=[],
    datas=[(str(AUDIO / "config.yaml"), ".")] if (AUDIO / "config.yaml").is_file() else [],
    hiddenimports=audio_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "kivy"] + VAD_EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

MERGE((ui_a, "MeetingBox", "MeetingBox"), (audio_a, "meetingbox-audio", "meetingbox-audio"))

ui_pyz = PYZ(ui_a.pure, ui_a.zipped_data, cipher=block_cipher)
audio_pyz = PYZ(audio_a.pure, audio_a.zipped_data, cipher=block_cipher)

_icon = REPO_ROOT / "packaging" / "windows" / "meetingbox.ico"
icon_arg = str(_icon) if _icon.is_file() else None

# Native splash shown instantly by the bootloader while the Kivy UI loads, so
# the user sees branded feedback immediately instead of a blank delay.
_splash_img = REPO_ROOT / "packaging" / "windows" / "splash.png"
ui_splash = None
if _splash_img.is_file():
    ui_splash = Splash(
        str(_splash_img),
        binaries=ui_a.binaries,
        datas=ui_a.datas,
        text_pos=None,
        always_on_top=True,
    )

_ui_exe_extra = [ui_splash] if ui_splash is not None else []
ui_exe = EXE(
    ui_pyz,
    ui_a.scripts,
    *_ui_exe_extra,
    [],
    exclude_binaries=True,
    name="MeetingBox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon_arg,
)

audio_exe = EXE(
    audio_pyz,
    audio_a.scripts,
    [],
    exclude_binaries=True,
    name="meetingbox-audio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=icon_arg,
)

_splash_binaries = [ui_splash.binaries] if ui_splash is not None else []
coll = COLLECT(
    ui_exe,
    *_splash_binaries,
    ui_a.binaries,
    ui_a.zipfiles,
    ui_a.datas,
    audio_exe,
    audio_a.binaries,
    audio_a.zipfiles,
    audio_a.datas,
    *[Tree(p) for p in (sdl2.dep_bins + glew.dep_bins + angle.dep_bins)],
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MeetingBox",
)
