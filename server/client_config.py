"""Add or remove this edition's server ("etabs", "sap2000" or "safe") in the MCP clients on this PC (install.bat).

    etabs-mcp-server.exe --configure-clients <path of the installed exe>
    server.py --program SAP2000 --configure-clients     entry for this python + server.py --program SAP2000
    server.py --program SAP2000 --remove-clients

Configuring an edition also removes the old combined "csi" entry (0.6.0 and earlier), which the editions replace.

Done in Python rather than PowerShell: company Group Policy often blocks PowerShell scripts. Every file is backed up,
merged (other servers such as civil3d and revit, and other settings, are kept), written, then read back to confirm.
Exit code 0 when every client found was handled.

Claude Desktop: %APPDATA%\\Claude\\claude_desktop_config.json, for the Microsoft Store (MSIX) app also
%LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude\\claude_desktop_config.json, and for Claude Desktop 6.x
%LOCALAPPDATA%\\Claude-Data\\claude_*\\claude_desktop_config.json (the one 6.x reads; it holds other settings too, which
are kept). Codex: ~/.codex/config.toml.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import edition
import tomlkit
from safety import staged_file
from windows_lock import exclusive

LEGACY = "csi"     # the combined server of 0.6.0 and earlier


def name() -> str:
    return edition.server_name()


def _env(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def claude_files() -> list[Path]:
    appdata, local = _env("APPDATA"), _env("LOCALAPPDATA")
    files: list[Path] = []
    packages = sorted((local / "Packages").glob("Claude_*")) if local and (local / "Packages").is_dir() else []
    installed = bool(packages) or bool(local and (local / "AnthropicClaude").is_dir())
    if appdata and ((appdata / "Claude").is_dir() or installed):
        files.append(appdata / "Claude" / "claude_desktop_config.json")
    for package in packages:
        store = package / "LocalCache" / "Roaming" / "Claude"
        if store.is_dir():
            files.append(store / "claude_desktop_config.json")
    # Claude Desktop 6.x (Store app) reads %LOCALAPPDATA%\Claude-Data\claude_<publisher id>\claude_desktop_config.json;
    # the two files above are no longer read by it (found 26 Nov 2026: entries there were ignored).
    data = local / "Claude-Data" if local else None
    if data and data.is_dir():
        files += [d / "claude_desktop_config.json" for d in sorted(data.glob("claude_*"))
                  if (d / "claude_desktop_config.json").is_file()]
    return files


def _backup(path: Path) -> None:
    if path.exists():
        path.with_name(path.name + ".bak-" + uuid.uuid4().hex).write_bytes(path.read_bytes())


def _claude(path: Path, entry: dict[str, Any] | None) -> str:
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    try:
        config = json.loads(text) if text.strip() else {}
    except ValueError as exc:
        raise RuntimeError(f"not valid JSON ({exc}); fix it or add the entry by hand") from None
    if not isinstance(config, dict):
        raise RuntimeError("the file is not a JSON object; add the entry by hand")
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError('"mcpServers" is not an object; add the entry by hand')
    key = name()
    legacy = key != LEGACY and entry is not None and LEGACY in servers
    if entry is None:
        if key not in servers:
            return f"no {key} entry"
        _backup(path)
        del servers[key]
    else:
        previous = servers.get(key, {})
        if not isinstance(previous, dict):
            raise RuntimeError("Existing MCP entry is not an object")
        updated = {**previous, **entry}
        if "args" not in entry:
            updated.pop("args", None)
        if updated == previous and not legacy:
            return "already configured"
        _backup(path)
        servers[key] = updated
        if legacy:
            del servers[LEGACY]
    config["mcpServers"] = servers
    path.parent.mkdir(parents=True, exist_ok=True)
    with staged_file(path, overwrite=True) as tmp:
        tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    check = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
    if check != servers:
        raise RuntimeError("written but not found when read back")
    return "removed" if entry is None else ("configured (old combined csi entry removed)" if legacy else "configured")


def codex_lines(entry: dict[str, Any], key: str | None = None) -> list[str]:
    key = key or name()
    lines = [f"[mcp_servers.{key}]", "command = " + json.dumps(entry["command"])]
    if entry.get("args"):
        lines.append("args = [" + ", ".join(json.dumps(a) for a in entry["args"]) + "]")
    lines += ["", f"[mcp_servers.{key}.env]", 'CSI_MCP_SPLIT = "1"']
    return lines


def _codex(path: Path, entry: dict[str, Any] | None) -> str:
    key = name()
    original = path.read_text(encoding="utf-8-sig")
    doc = tomlkit.parse(original)
    servers = doc.setdefault("mcp_servers", tomlkit.table())
    if entry is None:
        if key not in servers:
            return f"no {key} entry"
        del servers[key]
    else:
        target = servers.setdefault(key, tomlkit.table())
        target["command"] = entry["command"]
        if entry.get("args"):
            target["args"] = entry["args"]
        else:
            target.pop("args", None)
        target.setdefault("env", tomlkit.table()).setdefault("CSI_MCP_SPLIT", "1")
        if key != LEGACY:
            servers.pop(LEGACY, None)
    revised = tomlkit.dumps(doc)
    if revised == original:
        return "already configured"
    if tomlkit.parse(revised).unwrap() != doc.unwrap():
        raise RuntimeError("TOML round-trip validation failed")
    _backup(path)
    with staged_file(path, overwrite=True) as tmp:
        tmp.write_text(revised, encoding="utf-8")
    return "removed" if entry is None else "configured"


def run(entry: dict[str, Any] | None) -> int:
    """entry: {"command": ..., "args"?: [...]} to add, or None to remove. Prints one line per client."""
    failed = 0
    files = claude_files()
    if not files:
        print("Claude Desktop: not installed for this user (no Claude folder or app found)")
    for path in files:
        try:
            with exclusive("client-config"):
                print(f"Claude Desktop: {_claude(path, entry)}: {path}")
        except Exception as exc:  # noqa: BLE001 - report every client, then fail at the end
            failed += 1
            print(f"Claude Desktop: FAILED: {path}: {exc}")
    codex = (_env("CODEX_HOME") or ((_env("USERPROFILE") or Path.home()) / ".codex")) / "config.toml"
    if codex.exists():
        try:
            with exclusive("client-config"):
                print(f"Codex: {_codex(codex, entry)}: {codex}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"Codex: FAILED: {codex}: {exc}")
    if failed:
        print("Add the entry by hand: Claude Desktop > Settings > Developer > Edit Config, inside \"mcpServers\": "
              + json.dumps({name(): entry or {"command": "<path of the installed server exe>"}}))
    return 1 if failed else 0
