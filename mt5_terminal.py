"""
Windows helpers for driving the MT5 terminal UI.

The MetaTrader5 Python API can read whether Algo Trading is enabled
(terminal_info().trade_allowed) but cannot change it, so we toggle it the same
way a user would: the terminal's "Algo Trading" command (also bound to Ctrl+E).
"""
import ctypes
import logging
import os
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_COMMAND = 0x0111
# Command ID of the "Algo Trading" toolbar button in MT5 (same as pressing Ctrl+E)
MT5_CMD_ALGO_TRADING = 32851

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_E = 0x45

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _process_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def find_main_window(terminal_dir: str) -> int | None:
    """Returns the main window handle of the terminal installed in terminal_dir."""
    target = os.path.normcase(os.path.join(terminal_dir, "terminal64.exe"))
    found = []

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if os.path.normcase(_process_path(pid.value)) == target:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found[0] if found else None


def _press_ctrl_e(hwnd: int):
    # Tapping Alt lets this process steal foreground focus (Windows foreground lock)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_E, 0, 0, 0)
    user32.keybd_event(VK_E, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def enable_algo_trading(terminal_dir: str, is_enabled, timeout: float = 5.0) -> bool:
    """
    Toggles Algo Trading on. `is_enabled` is a callable returning the current state;
    it is checked before every toggle because the command flips the state.
    """
    if is_enabled():
        return True

    hwnd = find_main_window(terminal_dir)
    if hwnd is None:
        logger.error("Could not find the MT5 window for %s", terminal_dir)
        return False

    for name, toggle in (
        ("WM_COMMAND", lambda: user32.PostMessageW(hwnd, WM_COMMAND, MT5_CMD_ALGO_TRADING, 0)),
        ("Ctrl+E", lambda: _press_ctrl_e(hwnd)),
    ):
        if is_enabled():
            return True
        logger.info("Algo Trading is off — enabling it via %s", name)
        toggle()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if is_enabled():
                return True

    return is_enabled()
