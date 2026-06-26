"""Windows 실행 최적화."""

from __future__ import annotations

import ctypes
import sys

_mutex_handle = None


def ensure_single_instance(app_id: str) -> bool:
    """이미 실행 중이면 False."""
    global _mutex_handle
    if sys.platform != "win32":
        return True

    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    _mutex_handle = kernel32.CreateMutexW(None, True, f"Local\\{app_id}")
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def notify_already_running(title: str, message: str) -> None:
    if sys.platform != "win32":
        return
    ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
