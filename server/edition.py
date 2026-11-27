"""Edition: which CSI program this server process is for (0.7.0: one MCP server per program).

The same code is packaged three times. The program comes from, in order:
    1. `--program ETABS|SAP2000|SAFE` on the command line (development: python server.py --program SAP2000),
    2. the environment variable CSI_MCP_PROGRAM,
    3. the name of the packaged exe: etabs-mcp-server.exe / sap2000-mcp-server.exe / safe-mcp-server.exe.
With none of these the server serves all three programs (development and offline tests only; not packaged).

An edition has its own MCP server and tool name (etabs / sap2000 / safe), lists only the actions its program's API
has, and fills params.software by itself.
"""
from __future__ import annotations

import os
import sys
from pathlib import PureWindowsPath

ALL = ("ETABS", "SAP2000", "SAFE")
EXE = {"ETABS": "etabs-mcp-server.exe", "SAP2000": "sap2000-mcp-server.exe", "SAFE": "safe-mcp-server.exe"}
PACKAGE = {"ETABS": "EtabsMcp", "SAP2000": "Sap2000Mcp", "SAFE": "SafeMcp"}

_forced: str | None = None


def _normal(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().upper()
    return value if value in ALL else None


def force(program: str | None) -> None:
    """Set from --program. An unknown name is an error (it would otherwise silently serve all programs)."""
    global _forced
    if program is not None and _normal(program) is None:
        raise SystemExit(f"--program must be one of {', '.join(ALL)}, not {program!r}.")
    _forced = _normal(program)


def from_exe(path: str) -> str | None:
    stem = PureWindowsPath(path).stem.lower()   # the exe path is a Windows path
    for prog in ALL:
        if stem.startswith(prog.lower() + "-") or stem == prog.lower():
            return prog
    return None


def current() -> str | None:
    """The program of this edition, or None for the all-programs development server."""
    if _forced:
        return _forced
    env = _normal(os.environ.get("CSI_MCP_PROGRAM"))
    if env:
        return env
    if getattr(sys, "frozen", False):
        return from_exe(sys.executable)
    return None


def server_name() -> str:
    """MCP server name, tool name and client-config key: etabs / sap2000 / safe (csi for the development server)."""
    prog = current()
    return prog.lower() if prog else "csi"


def tool_names(split: bool) -> tuple[str, ...]:
    base = server_name()
    return (f"{base}_read", f"{base}_edit") if split else (base,)
