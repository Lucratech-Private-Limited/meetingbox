"""Windows plumbing for the always-on-top Pepper dock overlay.

The Pepper dock must float *above every other application* (Chrome, Office,
Explorer …) like a macOS menu-bar app. On Windows that means turning the single
Kivy/SDL2 window into a **layered, top-most, per-pixel-transparent overlay** that
covers the whole virtual desktop and, crucially, lets mouse clicks pass through
to the apps underneath everywhere except the dock/screen region.

Everything here is pure ``ctypes`` and degrades to a harmless no-op on non-Windows
platforms (the Linux appliance never imports the Windows branch behaviour), so a
missing DLL or an unexpected SDL build can never crash the app — the dock simply
falls back to a normal in-window overlay.

Design notes
------------
* Per-pixel transparency for an OpenGL (SDL2) window is achieved with the
  well-known DWM trick: ``DwmEnableBlurBehindWindow`` with an *empty* blur region
  makes the compositor honour the window's alpha channel, so GL fragments cleared
  to ``alpha = 0`` become truly transparent (and soft shadows keep their partial
  alpha). No CPU bitmap / ``UpdateLayeredWindow`` is needed, so normal GL
  rendering keeps working.
* Selective click-through can't be done per-pixel with a GL layered window, so we
  toggle the whole window's ``WS_EX_TRANSPARENT`` bit from a cursor poll: when the
  pointer is over an interactive region we let the window receive input, otherwise
  we make it transparent to the mouse so the user can keep working underneath.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Optional

import platform_compat

logger = logging.getLogger(__name__)

IS_WINDOWS = platform_compat.IS_WINDOWS

# ── Win32 constants ──────────────────────────────────────────────────────────
GWL_EXSTYLE = -20
GWL_STYLE = -16

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080  # keep the overlay out of the taskbar / Alt-Tab
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_FRAMECHANGED = 0x0020

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

VK_LBUTTON = 0x01

_HWND_TOPMOST = wintypes.HWND(-1)


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


class _DWM_BLURBEHIND(ctypes.Structure):
    _fields_ = [
        ("dwFlags", ctypes.c_uint),
        ("fEnable", ctypes.c_int),
        ("hRgnBlur", wintypes.HRGN),
        ("fTransitionOnMaximized", ctypes.c_int),
    ]


_DWM_BB_ENABLE = 0x00000001
_DWM_BB_BLURREGION = 0x00000002


def _user32():
    return ctypes.windll.user32


def _configure_argtypes() -> None:
    """Pin argtypes so 64-bit HWNDs / LONG_PTRs are not truncated by ctypes."""
    u = _user32()
    u.FindWindowW.restype = wintypes.HWND
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.GetWindowLongPtrW.restype = ctypes.c_longlong
    u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetWindowLongPtrW.restype = ctypes.c_longlong
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
    u.SetWindowPos.restype = wintypes.BOOL
    u.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    u.GetSystemMetrics.restype = ctypes.c_int
    u.GetSystemMetrics.argtypes = [ctypes.c_int]
    u.GetForegroundWindow.restype = wintypes.HWND
    u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    u.GetAsyncKeyState.restype = ctypes.c_short
    u.GetAsyncKeyState.argtypes = [ctypes.c_int]
    u.EnumWindows.restype = wintypes.BOOL
    u.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindowRect.restype = wintypes.BOOL
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetDC.restype = wintypes.HDC
    u.GetDC.argtypes = [wintypes.HWND]
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]


_argtypes_ready = False


def _ensure_argtypes() -> None:
    global _argtypes_ready
    if not _argtypes_ready:
        _configure_argtypes()
        _argtypes_ready = True


def find_hwnd(title: str) -> int:
    """Return the HWND for the SDL window with *title* (0 if not found)."""
    if not IS_WINDOWS:
        return 0
    try:
        _ensure_argtypes()
        return int(_user32().FindWindowW(None, title) or 0)
    except Exception:
        logger.debug("find_hwnd failed", exc_info=True)
        return 0


_EnumWindowsProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
) if IS_WINDOWS else None


def find_own_hwnd() -> int:
    """Return this process's own top-level SDL window HWND (0 if not found).

    Far more reliable than :func:`find_hwnd`: Kivy/SDL can override the window
    title after we set it, so a title lookup often misses. We instead enumerate
    top-level windows and return the first visible one that belongs to our PID.
    """
    if not IS_WINDOWS:
        return 0
    try:
        _ensure_argtypes()
        u = _user32()
        k = ctypes.windll.kernel32
        our_pid = int(k.GetCurrentProcessId())
        found: list[int] = []

        def _cb(hwnd, _lparam):
            pid = wintypes.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) == our_pid and u.IsWindowVisible(hwnd):
                r = wintypes.RECT()
                if u.GetWindowRect(hwnd, ctypes.byref(r)):
                    if (r.right - r.left) > 0 and (r.bottom - r.top) > 0:
                        found.append(int(hwnd))
                        return False  # stop enumeration
            return True

        u.EnumWindows(_EnumWindowsProc(_cb), 0)
        return found[0] if found else 0
    except Exception:
        logger.debug("find_own_hwnd failed", exc_info=True)
        return 0


def system_scale() -> float:
    """Primary monitor display-scale factor (1.0 = 96 DPI, 1.5 = 150%, …)."""
    if not IS_WINDOWS:
        return 1.0
    try:
        _ensure_argtypes()
        u = _user32()
        dc = u.GetDC(0)
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        finally:
            u.ReleaseDC(0, dc)
        if dpi and dpi > 0:
            return float(dpi) / 96.0
    except Exception:
        logger.debug("system_scale failed", exc_info=True)
    return 1.0


def physical_ppcm() -> Optional[tuple[float, float]]:
    """Primary monitor TRUE physical pixels-per-cm from EDID, or None.

    Uses the monitor's real image size (millimetres) recorded in its EDID —
    independent of the Windows display-scale setting — so a target expressed in
    centimetres renders at that real-world size on any screen.
    """
    if not IS_WINDOWS:
        return None
    try:
        _ensure_argtypes()
        u = _user32()
        res_w = int(u.GetSystemMetrics(0))   # SM_CXSCREEN (physical, DPI-aware)
        res_h = int(u.GetSystemMetrics(1))   # SM_CYSCREEN
        size = _edid_image_mm()
        if size is None or res_w <= 0 or res_h <= 0:
            return None
        mm_w, mm_h = size
        if mm_w <= 0 or mm_h <= 0:
            return None
        return (res_w / (mm_w / 10.0), res_h / (mm_h / 10.0))
    except Exception:
        logger.debug("physical_ppcm failed", exc_info=True)
        return None


def _edid_image_mm() -> Optional[tuple[int, int]]:
    """Read the active monitor's physical image size (mm) from its EDID."""
    try:
        import winreg
    except Exception:
        return None
    try:
        base = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            i = 0
            while True:
                try:
                    model = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                with winreg.OpenKey(root, model) as mk:
                    j = 0
                    while True:
                        try:
                            inst = winreg.EnumKey(mk, j)
                        except OSError:
                            break
                        j += 1
                        try:
                            dp = model + "\\" + inst + "\\Device Parameters"
                            with winreg.OpenKey(root, dp) as dk:
                                edid, _t = winreg.QueryValueEx(dk, "EDID")
                        except OSError:
                            continue
                        if not edid or len(edid) < 69:
                            continue
                        # First detailed timing descriptor (bytes 54-71): image
                        # size in mm is bytes 66/67 with the high nibbles in 68.
                        mm_w = ((edid[68] & 0xF0) << 4) | edid[66]
                        mm_h = ((edid[68] & 0x0F) << 8) | edid[67]
                        if mm_w > 0 and mm_h > 0:
                            return (mm_w, mm_h)
                        # Fallback: bytes 21/22 hold max image size in whole cm.
                        if edid[21] > 0 and edid[22] > 0:
                            return (edid[21] * 10, edid[22] * 10)
    except Exception:
        logger.debug("_edid_image_mm failed", exc_info=True)
    return None


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """(x, y, w, h) spanning all monitors; falls back to a sane default."""
    if not IS_WINDOWS:
        return (0, 0, 1920, 1080)
    try:
        _ensure_argtypes()
        u = _user32()
        x = u.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = u.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = u.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = u.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return (x, y, w, h)
    except Exception:
        logger.debug("virtual_screen_rect failed", exc_info=True)
    return (0, 0, 1920, 1080)


def primary_screen_size() -> tuple[int, int]:
    if not IS_WINDOWS:
        return (1920, 1080)
    try:
        _ensure_argtypes()
        u = _user32()
        return (u.GetSystemMetrics(0), u.GetSystemMetrics(1))
    except Exception:
        return (1920, 1080)


def make_overlay(hwnd: int) -> bool:
    """Turn *hwnd* into a borderless, top-most, per-pixel-transparent overlay.

    Returns True on success. Any failure is swallowed and reported so the caller
    can continue with a plain (non-transparent) overlay.
    """
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        _ensure_argtypes()
        u = _user32()

        # Frameless popup so there is no title bar / border around the overlay.
        style = u.GetWindowLongPtrW(hwnd, GWL_STYLE)
        style &= ~(WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX
                   | WS_MAXIMIZEBOX | WS_SYSMENU)
        style |= WS_POPUP | WS_VISIBLE
        u.SetWindowLongPtrW(hwnd, GWL_STYLE, style)

        ex = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        ex |= (WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
               | WS_EX_NOACTIVATE)
        u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)

        _enable_dwm_alpha(hwnd)

        # Cover the whole virtual desktop and pin top-most.
        x, y, w, h = virtual_screen_rect()
        u.SetWindowPos(
            hwnd, _HWND_TOPMOST, x, y, w, h,
            SWP_FRAMECHANGED | SWP_SHOWWINDOW | SWP_NOACTIVATE,
        )
        return True
    except Exception:
        logger.warning("make_overlay failed; dock will not be transparent",
                       exc_info=True)
        return False


def _enable_dwm_alpha(hwnd: int) -> None:
    """Let the DWM compositor honour the window's per-pixel alpha channel."""
    try:
        dwm = ctypes.windll.dwmapi
    except Exception:
        return
    # Extend the (glassless) frame so the client area participates in alpha.
    try:
        margins = _MARGINS(-1, -1, -1, -1)
        dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd),
                                         ctypes.byref(margins))
    except Exception:
        logger.debug("DwmExtendFrameIntoClientArea failed", exc_info=True)
    # Empty blur region → alpha respected without any real blur.
    try:
        gdi = ctypes.windll.gdi32
        region = gdi.CreateRectRgn(0, 0, -1, -1)
        bb = _DWM_BLURBEHIND()
        bb.dwFlags = _DWM_BB_ENABLE | _DWM_BB_BLURREGION
        bb.fEnable = 1
        bb.hRgnBlur = region
        bb.fTransitionOnMaximized = 0
        dwm.DwmEnableBlurBehindWindow(wintypes.HWND(hwnd), ctypes.byref(bb))
        gdi.DeleteObject(region)
    except Exception:
        logger.debug("DwmEnableBlurBehindWindow failed", exc_info=True)


def reassert_topmost(hwnd: int) -> None:
    """Re-pin the overlay above everything (call periodically / on focus loss)."""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        _ensure_argtypes()
        _user32().SetWindowPos(
            hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception:
        logger.debug("reassert_topmost failed", exc_info=True)


def set_click_through(hwnd: int, click_through: bool) -> None:
    """Toggle whether the whole overlay ignores the mouse (passes clicks down)."""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        _ensure_argtypes()
        u = _user32()
        ex = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        new = (ex | WS_EX_TRANSPARENT) if click_through else (ex & ~WS_EX_TRANSPARENT)
        if new != ex:
            u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
    except Exception:
        logger.debug("set_click_through failed", exc_info=True)


def get_cursor_pos() -> Optional[tuple[int, int]]:
    """Global cursor position in virtual-desktop pixels, or None on failure."""
    if not IS_WINDOWS:
        return None
    try:
        _ensure_argtypes()
        pt = wintypes.POINT()
        if _user32().GetCursorPos(ctypes.byref(pt)):
            return (int(pt.x), int(pt.y))
    except Exception:
        logger.debug("get_cursor_pos failed", exc_info=True)
    return None


def left_button_down() -> bool:
    """True while the physical left mouse button is held down."""
    if not IS_WINDOWS:
        return False
    try:
        _ensure_argtypes()
        return bool(_user32().GetAsyncKeyState(VK_LBUTTON) & 0x8000)
    except Exception:
        return False


def is_foreground(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        _ensure_argtypes()
        return int(_user32().GetForegroundWindow() or 0) == int(hwnd)
    except Exception:
        return False
