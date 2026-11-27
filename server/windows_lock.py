"""Non-blocking coordination between CSI MCP processes in one Windows session."""
import contextlib
import ctypes
import os
from ctypes import wintypes


@contextlib.contextmanager
def exclusive(name: str):
    if os.name != "nt":
        yield
        return
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.ReleaseMutex.argtypes = api.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = api.CreateMutexW(None, False, "Local\\CsiMcp-" + name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        status = api.WaitForSingleObject(handle, 0)
        if status == 258:
            raise ValueError("Another CSI MCP process is busy. This request did not start.")
        if status not in (0, 128):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if status == 128:
                raise ValueError("Previous CSI worker exited during an operation. Inspect CSI before retrying.")
            yield
        finally:
            api.ReleaseMutex(handle)
    finally:
        api.CloseHandle(handle)

