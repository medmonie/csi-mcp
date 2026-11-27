"""Write claude_config_snippet.json and codex_config_snippet.toml for THIS checkout (run by setup_server.bat).

One entry per edition (etabs / sap2000 / safe), each pointing at server\\.venv\\Scripts\\python.exe + server\\server.py
--program <PROGRAM>. The snippets are machine-specific, so .gitignore keeps them out of the repository.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import client_config  # noqa: E402
import edition  # noqa: E402

python = ROOT / "server" / ".venv" / "Scripts" / "python.exe"
entries = {p.lower(): {"command": str(python), "args": [str(ROOT / "server" / "server.py"), "--program", p]}
           for p in edition.ALL}
(ROOT / "claude_config_snippet.json").write_text(json.dumps({"mcpServers": entries}, indent=2) + "\n", encoding="utf-8")
lines: list[str] = []
for key, entry in entries.items():
    lines += client_config.codex_lines(entry, key) + [""]
(ROOT / "codex_config_snippet.toml").write_text("\n".join(lines), encoding="utf-8")
print("snippets written for", python)
