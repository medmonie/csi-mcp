"""Read-only: find every Claude Desktop config file on this PC and list its MCP server names, commands and file time.

Prints no env values (they can hold keys). Used when an installer reports 'already configured' but Claude Desktop
shows other servers. Output: tests\\results\\diag_claude_config.txt (run_diag_claude_config.bat)."""
import json
import os
import time
from pathlib import Path


def candidates() -> list[Path]:
    found: list[Path] = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base or not Path(base).is_dir():
            continue
        for root, dirs, files in os.walk(base):
            depth = len(Path(root).relative_to(base).parts)
            # only descend into folders on a path that mentions claude / anthropic (and Packages to reach Claude_*)
            dirs[:] = [d for d in dirs if depth < 7 and (
                "claude" in d.lower() or "anthropic" in d.lower() or d in ("Packages", "LocalCache", "Roaming")
                or "claude" in root.lower() or "anthropic" in root.lower())]
            for f in files:
                if f.lower().startswith("claude_desktop_config") or (f.lower() == "config.json" and "claude" in root.lower()
                                                                     and "mcpServers" in _peek(Path(root) / f)):
                    found.append(Path(root) / f)
    return found


def _peek(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8-sig", errors="ignore")[:200000]
    except OSError:
        return ""


def main() -> None:
    print("Claude config files", time.strftime("%Y-%m-%d %H:%M:%S"))
    for p in candidates():
        st = p.stat()
        print(f"\n{p}\n  modified {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}, {st.st_size} bytes")
        try:
            data = json.loads(_peek(p) or "{}")
        except ValueError as exc:
            print("  not valid JSON:", exc)
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            print("  no mcpServers; top-level keys:", list(data)[:20] if isinstance(data, dict) else type(data).__name__)
            continue
        for name, entry in servers.items():
            cmd = entry.get("command") if isinstance(entry, dict) else entry
            args = entry.get("args") if isinstance(entry, dict) else None
            print(f"  {name}: {cmd} {args or ''}")
    print("\nDONE")


main()
