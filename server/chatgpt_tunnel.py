"""Launch OpenAI's Secure MCP Tunnel for one local CSI edition."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def run(program: str | None) -> int:
    if program is None:
        print("Select an edition with --program ETABS, SAP2000 or SAFE.", file=sys.stderr)
        return 2
    client = shutil.which("tunnel-client")
    if not client:
        print("Install the OpenAI tunnel-client CLI and add it to PATH. See the ChatGPT section of README.md.",
              file=sys.stderr)
        return 2
    for name in ("CONTROL_PLANE_TUNNEL_ID", "CONTROL_PLANE_API_KEY"):
        if not os.environ.get(name):
            print(f"Set {name} in the launching process.", file=sys.stderr)
            return 2
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        folder, command = exe.parent, "./" + exe.name
    else:
        folder = Path(__file__).resolve().parent
        if not (folder / ".venv/Scripts/python.exe").is_file():
            print("Run setup_server.bat to create the source edition's virtual environment.", file=sys.stderr)
            return 2
        command = "./.venv/Scripts/python.exe server.py"
    # Relative commands are resolved in the selected working directory, including paths containing spaces.
    args = [client, "run", "--mcp.command", f"{command} --program {program} --chatgpt",
            "--mcp.stdio-send-initialized-notification", "--health.listen-addr", "127.0.0.1:0"]
    try:
        return subprocess.run(args, cwd=folder, check=False).returncode
    except OSError as exc:
        print(f"Could not start tunnel-client: {exc.strerror or type(exc).__name__}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
