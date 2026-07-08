"""COM / DMO plumbing for the Windows Voice Capture DSP (CWMAudioAEC).

Hand-declared interfaces (IMediaObject, IMediaBuffer, IPropertyStore) and the
structs/helpers needed to drive the DSP from Python via comtypes. Kept separate
from windows_aec.py so the COM surface is isolated and easy to bundle.

Windows-only. Importing this on a non-Windows interpreter (or without comtypes)
raises; callers must gate on windows_aec.is_available() first.
"""

from __future__ import annotations

import ctypes as C
from ctypes import POINTER, byref, c_long, c_longlong, c_ulong, c_ushort, c_void_p
from ctypes.wintypes import BOOL, BYTE, DWORD, WORD

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
PID_DMO_SOURCE_MODE = 3  # VARIANT_BOOL: TRUE = source mode (DMO opens devices)
PID_DEVICE_INDEXES = 4

SINGLE_CHANNEL_AEC = 0

VT_I4 = 3
VT_BOOL = 11
VT_UI4 = 19

S_OK = 0
S_FALSE = 1
CLSCTX_INPROC_SERVER = 0x1

REFERENCE_TIME = c_longlong


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------
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

    # IMediaBuffer COM methods (called by the DMO).
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

    # Plain Python helpers (NOT COM — used by our pull loop).
    def reset(self) -> None:
        self._len = 0

    def read_bytes(self) -> bytes:
        if self._len <= 0:
            return b""
        # wintypes.BYTE is signed; string_at returns raw bytes regardless.
        return C.string_at(self._buf, self._len)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_propkey(pid: int) -> PROPERTYKEY:
    k = PROPERTYKEY()
    k.fmtid = FMTID_WMAAECMA
    k.pid = pid
    return k


def set_prop_i4(store, pid: int, value: int, vt: int = VT_I4) -> None:
    pk = make_propkey(pid)
    pv = PROPVARIANT()
    pv.vt = vt
    pv.lVal = value
    store.SetValue(byref(pk), byref(pv))


def make_output_mediatype(rate: int = 16000) -> DMO_MEDIA_TYPE:
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
    # Keep the format block alive for as long as the media type is used.
    buf = (BYTE * C.sizeof(WAVEFORMATEX)).from_buffer_copy(wfx)
    mt._wfx_keepalive = buf
    mt.pbFormat = C.cast(buf, POINTER(BYTE))
    return mt
