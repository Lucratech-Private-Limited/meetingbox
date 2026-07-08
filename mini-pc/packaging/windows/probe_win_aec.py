"""Feasibility probe for the Windows Voice Capture DSP (CWMAudioAEC).

Goal: prove that the OS-provided acoustic echo canceller can be driven from
Python (comtypes) on THIS machine, and that it actually removes the device's
own speaker output from the captured mic stream.

Method:
  Phase A (baseline echo): open a raw sounddevice mic + play a loud tone out the
      default speakers; measure the raw mic RMS. This is the uncancelled echo.
  Phase B (OS AEC): run the Voice Capture DSP in SINGLE_CHANNEL_AEC source mode
      (it opens the default mic + render itself), play the same tone, pull the
      cleaned 16 kHz mono output, measure its RMS after a short converge window.

If Phase B RMS is dramatically lower than Phase A RMS, the OS AEC works and we
can wire it into the realtime session for true full-duplex barge-in.

Run:  python packaging\windows\probe_win_aec.py
"""

from __future__ import annotations

import ctypes as C
import math
import sys
import time
from ctypes import POINTER, byref, c_long, c_longlong, c_ulong, c_ushort, c_void_p, pointer
from ctypes.wintypes import BOOL, BYTE, DWORD, WORD

import numpy as np

try:
    import sounddevice as sd
except Exception as e:  # pragma: no cover
    print("sounddevice unavailable:", e)
    sys.exit(2)

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, COMObject, IUnknown

# ---------------------------------------------------------------------------
# GUIDs
# ---------------------------------------------------------------------------
CLSID_CWMAudioAEC = GUID("{745057C7-F353-4F2D-A7EE-58434477730E}")
IID_IMediaObject = GUID("{d8ad0f58-5494-4102-97c5-ec798e59bcf4}")
IID_IPropertyStore = GUID("{886d8eeb-8cf2-4446-8d02-cdba1dbdcf99}")
IID_IMediaBuffer = GUID("{59eff8b9-938c-4a26-82f2-95cb84cdc837}")

MEDIATYPE_Audio = GUID("{73647561-0000-0010-8000-00AA00389B71}")
MEDIASUBTYPE_PCM = GUID("{00000001-0000-0010-8000-00AA00389B71}")
FORMAT_WaveFormatEx = GUID("{05589f81-c356-11ce-bf01-00aa0055595a}")

# WMAAECMA property keys share this fmtid; pid selects the property.
FMTID_WMAAECMA = GUID("{6f52c567-0360-4bd2-9617-ccbf1421c939}")
PID_SYSTEM_MODE = 2
PID_DEVICE_INDEXES = 4
PID_FEATR_NS = 9
PID_FEATR_AGC = 10
PID_FEATR_CENTER_CLIP = 12
PID_DMO_SOURCE_MODE = 3  # VARIANT_BOOL: TRUE = source mode

SINGLE_CHANNEL_AEC = 0

VT_I4 = 3
VT_BOOL = 11
VT_UI4 = 19

S_OK = 0
S_FALSE = 1
CLSCTX_INPROC_SERVER = 0x1

OUT_RATE = 16000  # the DSP emits 16 kHz mono PCM16


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------
REFERENCE_TIME = c_longlong


class WAVEFORMATEX(C.Structure):
    _fields_ = [
        ("wFormatTag", WORD),
        ("nChannels", WORD),
        ("nSamplesPerSec", DWORD),
        ("nAvgBytesPerSec", DWORD),
        ("nBlockAlign", WORD),
        ("wBitsPerSample", WORD),
        ("cbSize", WORD),
    ]


class DMO_MEDIA_TYPE(C.Structure):
    _fields_ = [
        ("majortype", GUID),
        ("subtype", GUID),
        ("bFixedSizeSamples", BOOL),
        ("bTemporalCompression", BOOL),
        ("lSampleSize", c_ulong),
        ("formattype", GUID),
        ("pUnk", c_void_p),
        ("cbFormat", c_ulong),
        ("pbFormat", POINTER(BYTE)),
    ]


class PROPERTYKEY(C.Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class PROPVARIANT(C.Structure):
    # Minimal: enough for VT_I4 / VT_UI4 / VT_BOOL.
    _fields_ = [
        ("vt", c_ushort),
        ("wReserved1", c_ushort),
        ("wReserved2", c_ushort),
        ("wReserved3", c_ushort),
        ("lVal", c_long),
        ("pad", c_long * 3),
    ]


class DMO_OUTPUT_DATA_BUFFER(C.Structure):
    _fields_ = [
        ("pBuffer", c_void_p),  # IMediaBuffer*
        ("dwStatus", DWORD),
        ("rtTimestamp", REFERENCE_TIME),
        ("rtTimelength", REFERENCE_TIME),
    ]


# ---------------------------------------------------------------------------
# COM interfaces
# ---------------------------------------------------------------------------
class IMediaBuffer(IUnknown):
    _iid_ = IID_IMediaBuffer
    _methods_ = [
        COMMETHOD([], HRESULT, "SetLength", (["in"], DWORD, "cbLength")),
        COMMETHOD([], HRESULT, "GetMaxLength", (["out"], POINTER(DWORD), "pcbMaxLength")),
        COMMETHOD(
            [],
            HRESULT,
            "GetBufferAndLength",
            (["out"], POINTER(POINTER(BYTE)), "ppBuffer"),
            (["out"], POINTER(DWORD), "pcbLength"),
        ),
    ]


class IMediaObject(IUnknown):
    _iid_ = IID_IMediaObject
    _methods_ = [
        COMMETHOD([], HRESULT, "GetStreamCount",
                  (["out"], POINTER(DWORD), "pcInputStreams"),
                  (["out"], POINTER(DWORD), "pcOutputStreams")),
        COMMETHOD([], HRESULT, "GetInputStreamInfo",
                  (["in"], DWORD, "dwInputStreamIndex"),
                  (["out"], POINTER(DWORD), "pdwFlags")),
        COMMETHOD([], HRESULT, "GetOutputStreamInfo",
                  (["in"], DWORD, "dwOutputStreamIndex"),
                  (["out"], POINTER(DWORD), "pdwFlags")),
        COMMETHOD([], HRESULT, "GetInputType",
                  (["in"], DWORD, "dwInputStreamIndex"),
                  (["in"], DWORD, "dwTypeIndex"),
                  (["in"], POINTER(DMO_MEDIA_TYPE), "pmt")),
        COMMETHOD([], HRESULT, "GetOutputType",
                  (["in"], DWORD, "dwOutputStreamIndex"),
                  (["in"], DWORD, "dwTypeIndex"),
                  (["in"], POINTER(DMO_MEDIA_TYPE), "pmt")),
        COMMETHOD([], HRESULT, "SetInputType",
                  (["in"], DWORD, "dwInputStreamIndex"),
                  (["in"], POINTER(DMO_MEDIA_TYPE), "pmt"),
                  (["in"], DWORD, "dwFlags")),
        COMMETHOD([], HRESULT, "SetOutputType",
                  (["in"], DWORD, "dwOutputStreamIndex"),
                  (["in"], POINTER(DMO_MEDIA_TYPE), "pmt"),
                  (["in"], DWORD, "dwFlags")),
        COMMETHOD([], HRESULT, "GetInputCurrentType",
                  (["in"], DWORD, "dwInputStreamIndex"),
                  (["in"], POINTER(DMO_MEDIA_TYPE), "pmt")),
        COMMETHOD([], HRESULT, "GetOutputCurrentType",
                  (["in"], DWORD, "dwOutputStreamIndex"),
                  (["in"], POINTER(DMO_MEDIA_TYPE), "pmt")),
        COMMETHOD([], HRESULT, "GetInputSizeInfo",
                  (["in"], DWORD, "dwInputStreamIndex"),
                  (["out"], POINTER(DWORD), "pcbSize"),
                  (["out"], POINTER(DWORD), "pcbMaxLookahead"),
                  (["out"], POINTER(DWORD), "pcbAlignment")),
        COMMETHOD([], HRESULT, "GetOutputSizeInfo",
                  (["in"], DWORD, "dwOutputStreamIndex"),
                  (["out"], POINTER(DWORD), "pcbSize"),
                  (["out"], POINTER(DWORD), "pcbAlignment")),
        COMMETHOD([], HRESULT, "GetInputMaxLatency",
                  (["in"], DWORD, "dwInputStreamIndex"),
                  (["out"], POINTER(REFERENCE_TIME), "prtMaxLatency")),
        COMMETHOD([], HRESULT, "SetInputMaxLatency",
                  (["in"], DWORD, "dwInputStreamIndex"),
                  (["in"], REFERENCE_TIME, "rtMaxLatency")),
        COMMETHOD([], HRESULT, "Flush"),
        COMMETHOD([], HRESULT, "Discontinuity",
                  (["in"], DWORD, "dwInputStreamIndex")),
        COMMETHOD([], HRESULT, "AllocateStreamingResources"),
        COMMETHOD([], HRESULT, "FreeStreamingResources"),
        COMMETHOD([], HRESULT, "GetInputStatus",
                  (["in"], DWORD, "dwInputStreamIndex"),
                  (["out"], POINTER(DWORD), "pdwFlags")),
        COMMETHOD([], HRESULT, "ProcessInput",
                  (["in"], DWORD, "dwInputStreamIndex"),
                  (["in"], POINTER(IMediaBuffer), "pBuffer"),
                  (["in"], DWORD, "dwFlags"),
                  (["in"], REFERENCE_TIME, "rtTimestamp"),
                  (["in"], REFERENCE_TIME, "rtTimelength")),
        COMMETHOD([], HRESULT, "ProcessOutput",
                  (["in"], DWORD, "dwFlags"),
                  (["in"], DWORD, "cOutputBufferCount"),
                  (["in"], POINTER(DMO_OUTPUT_DATA_BUFFER), "pOutputBuffers"),
                  (["out"], POINTER(DWORD), "pdwStatus")),
        COMMETHOD([], HRESULT, "Lock", (["in"], c_long, "bLock")),
    ]


class IPropertyStore(IUnknown):
    _iid_ = IID_IPropertyStore
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(DWORD), "cProps")),
        COMMETHOD([], HRESULT, "GetAt",
                  (["in"], DWORD, "iProp"),
                  (["out"], POINTER(PROPERTYKEY), "pkey")),
        COMMETHOD([], HRESULT, "GetValue",
                  (["in"], POINTER(PROPERTYKEY), "key"),
                  (["out"], POINTER(PROPVARIANT), "pv")),
        COMMETHOD([], HRESULT, "SetValue",
                  (["in"], POINTER(PROPERTYKEY), "key"),
                  (["in"], POINTER(PROPVARIANT), "propvar")),
        COMMETHOD([], HRESULT, "Commit"),
    ]


# ---------------------------------------------------------------------------
# IMediaBuffer implementation (receives the DSP's cleaned output)
# ---------------------------------------------------------------------------
class MediaBuffer(COMObject):
    _com_interfaces_ = [IMediaBuffer]

    def __init__(self, max_len: int):
        super().__init__()
        self._max = int(max_len)
        self._buf = (BYTE * self._max)()
        self._len = 0

    # IMediaBuffer
    def SetLength(self, this, cbLength):
        if cbLength > self._max:
            return -2147024809  # E_INVALIDARG
        self._len = int(cbLength)
        return S_OK

    def GetMaxLength(self, this, pcbMaxLength):
        pcbMaxLength[0] = self._max
        return S_OK

    def GetBufferAndLength(self, this, ppBuffer, pcbLength):
        if ppBuffer:
            ppBuffer[0] = C.cast(self._buf, POINTER(BYTE))
        if pcbLength:
            pcbLength[0] = self._len
        return S_OK

    def reset(self) -> None:
        self._len = 0

    def read_bytes(self) -> bytes:
        if self._len <= 0:
            return b""
        return C.string_at(self._buf, self._len)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_propkey(pid: int) -> PROPERTYKEY:
    k = PROPERTYKEY()
    k.fmtid = FMTID_WMAAECMA
    k.pid = pid
    return k


def set_i4(store: IPropertyStore, pid: int, value: int, vt: int = VT_I4):
    pk = make_propkey(pid)
    pv = PROPVARIANT()
    pv.vt = vt
    pv.lVal = value
    store.SetValue(byref(pk), byref(pv))


def make_output_mediatype(rate: int = OUT_RATE) -> DMO_MEDIA_TYPE:
    wfx = WAVEFORMATEX()
    wfx.wFormatTag = 1  # WAVE_FORMAT_PCM
    wfx.nChannels = 1
    wfx.nSamplesPerSec = rate
    wfx.wBitsPerSample = 16
    wfx.nBlockAlign = wfx.nChannels * wfx.wBitsPerSample // 8
    wfx.nAvgBytesPerSec = wfx.nSamplesPerSec * wfx.nBlockAlign
    wfx.cbSize = 0

    mt = DMO_MEDIA_TYPE()
    mt.majortype = MEDIATYPE_Audio
    mt.subtype = MEDIASUBTYPE_PCM
    mt.bFixedSizeSamples = 1
    mt.bTemporalCompression = 0
    mt.lSampleSize = wfx.nBlockAlign
    mt.formattype = FORMAT_WaveFormatEx
    mt.pUnk = None
    mt.cbFormat = C.sizeof(WAVEFORMATEX)
    # Keep the wfx alive by stashing it on the mt object.
    buf = (BYTE * C.sizeof(WAVEFORMATEX)).from_buffer_copy(wfx)
    mt._wfx_keepalive = buf
    mt.pbFormat = C.cast(buf, POINTER(BYTE))
    return mt


def rms_int16(data: bytes) -> float:
    if not data:
        return 0.0
    a = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    if a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(a * a)))


def make_tone(seconds: float, rate: int, freq: float = 440.0, amp: float = 0.6) -> np.ndarray:
    n = int(seconds * rate)
    t = np.arange(n, dtype=np.float32) / rate
    # Mix a couple of frequencies so it reads more like "voice" energy.
    sig = (
        amp * np.sin(2 * math.pi * freq * t)
        + 0.3 * amp * np.sin(2 * math.pi * (freq * 2.1) * t)
    )
    return sig.astype(np.float32)


# ---------------------------------------------------------------------------
# Phase A: raw echo baseline (no AEC)
# ---------------------------------------------------------------------------
def phase_a_baseline() -> float:
    print("\n[Phase A] Raw echo baseline (no AEC) ...")
    rate = 16000
    tone = make_tone(1.6, rate)
    captured = []

    def cb(indata, frames, time_info, status):
        captured.append(indata.copy())

    try:
        with sd.InputStream(samplerate=rate, channels=1, dtype="int16", callback=cb):
            time.sleep(0.3)  # warm up
            sd.play(tone, rate)
            time.sleep(1.6)
            sd.stop()
            time.sleep(0.1)
    except Exception as e:
        print("  Phase A failed:", e)
        return -1.0

    if not captured:
        print("  Phase A: no mic data")
        return -1.0
    pcm = np.concatenate(captured).astype(np.int16).tobytes()
    r = rms_int16(pcm)
    print(f"  raw mic RMS while tone playing: {r:8.1f}")
    return r


# ---------------------------------------------------------------------------
# Phase B: OS AEC via Voice Capture DSP
# ---------------------------------------------------------------------------
def phase_b_os_aec() -> float:
    print("\n[Phase B] Windows Voice Capture DSP (OS AEC), source mode ...")
    comtypes.CoInitialize()
    try:
        mo = comtypes.CoCreateInstance(
            CLSID_CWMAudioAEC,
            interface=IMediaObject,
            clsctx=CLSCTX_INPROC_SERVER,
        )
        print("  CWMAudioAEC instantiated:", bool(mo))
        store = mo.QueryInterface(IPropertyStore)

        # Configure: single-channel AEC, source mode, default devices.
        set_i4(store, PID_SYSTEM_MODE, SINGLE_CHANNEL_AEC)
        set_i4(store, PID_DMO_SOURCE_MODE, 0xFFFF, vt=VT_BOOL)  # VARIANT_TRUE
        # Default mic + default speaker => both wave indices = WAVE_MAPPER(0xFFFF).
        dev = (0xFFFF << 16) | 0xFFFF
        # signed 32-bit for VT_I4
        if dev >= 0x80000000:
            dev -= 0x100000000
        set_i4(store, PID_DEVICE_INDEXES, dev)
        # Commit is optional for this DMO and commonly returns E_NOTIMPL
        # (settings apply immediately via SetValue). Ignore failures.
        try:
            store.Commit()
        except Exception:
            pass
        print("  configured: SINGLE_CHANNEL_AEC + source mode + default devices")

        mt = make_output_mediatype(OUT_RATE)
        mo.SetOutputType(0, pointer(mt), 0)
        mo.AllocateStreamingResources()
        print("  output type set (16k mono PCM16), streaming resources allocated")

        # Output buffer: ~0.5 s of 16k mono PCM16.
        mbuf = MediaBuffer(OUT_RATE * 2)
        mbuf_ptr = mbuf.QueryInterface(IMediaBuffer)

        tone = make_tone(2.2, 16000)

        def pull(duration_s: float) -> bytes:
            out = bytearray()
            t_end = time.monotonic() + duration_s
            while time.monotonic() < t_end:
                mbuf.reset()
                odb = DMO_OUTPUT_DATA_BUFFER()
                odb.pBuffer = C.cast(mbuf_ptr, c_void_p)
                odb.dwStatus = 0
                odb.rtTimestamp = 0
                odb.rtTimelength = 0
                try:
                    mo.ProcessOutput(0, 1, byref(odb))
                except comtypes.COMError as ce:
                    # S_FALSE / no data yet is fine; anything else is real.
                    if ce.hresult not in (S_OK, S_FALSE):
                        raise
                chunk = mbuf.read_bytes()
                if chunk:
                    out.extend(chunk)
                else:
                    time.sleep(0.005)
            return bytes(out)

        # Let the DSP settle / converge, then start the echo source.
        print("  converging (0.8s) ...")
        pull(0.8)
        sd.play(tone, 16000)
        print("  tone playing; capturing cleaned output (2.0s) ...")
        cleaned = pull(2.0)
        sd.stop()

        try:
            mo.FreeStreamingResources()
        except Exception:
            pass

        r = rms_int16(cleaned)
        secs = len(cleaned) / (OUT_RATE * 2)
        print(f"  cleaned output: {len(cleaned)} bytes (~{secs:.2f}s), RMS={r:8.1f}")
        return r
    finally:
        comtypes.CoUninitialize()


def main():
    print("=" * 64)
    print("Windows Voice Capture DSP (OS AEC) feasibility probe")
    print("=" * 64)
    print("Default output:", sd.query_devices(kind="output")["name"])
    print("Default input :", sd.query_devices(kind="input")["name"])
    print("\n*** Use SPEAKERS (not headphones) so the mic hears the tone. ***")

    base = phase_a_baseline()
    aec = phase_b_os_aec()

    print("\n" + "=" * 64)
    print("RESULT")
    print("=" * 64)
    print(f"  raw echo RMS (no AEC):   {base:8.1f}")
    print(f"  OS AEC output RMS:       {aec:8.1f}")
    if base > 0 and aec >= 0:
        ratio = base / max(aec, 1.0)
        db = 20 * math.log10(max(base, 1.0) / max(aec, 1.0))
        print(f"  echo reduction:          {ratio:6.1f}x  ({db:5.1f} dB)")
        if db >= 18:
            print("  VERDICT: OS AEC cancels strongly -> full-duplex is viable.")
        elif db >= 8:
            print("  VERDICT: partial cancellation -> usable with a VAD gate.")
        else:
            print("  VERDICT: weak/no cancellation -> investigate device routing.")
    print("=" * 64)


if __name__ == "__main__":
    main()
