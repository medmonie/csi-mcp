r"""Start a server over stdio exactly as Claude Desktop does and check the MCP protocol side.

    protocol_check.py <path to exe>                 a packaged edition (etabs- / sap2000- / safe-mcp-server.exe)
    protocol_check.py --program SAP2000             the Python server of this checkout, one edition
    protocol_check.py                               the Python development server (all programs, tool "csi")

Reads only. Started by run_protocol.bat (Python) / run_exe_check.bat (the three packaged exes); output in
tests\results\.
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import edition  # noqa: E402

args = sys.argv[1:]
if args and args[0] == "--program":
    PROGRAM = args[1].upper()
    PARAMS = StdioServerParameters(command=sys.executable,
                                   args=[str(ROOT / "server" / "server.py"), "--program", PROGRAM])
elif args:
    PROGRAM = edition.from_exe(args[0])
    PARAMS = StdioServerParameters(command=args[0], args=[])
else:
    PROGRAM = None
    PARAMS = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server" / "server.py")])
TOOL = PROGRAM.lower() if PROGRAM else "csi"
PROGRAMS = (PROGRAM,) if PROGRAM else edition.ALL

async def main() -> int:
    ok = True
    print("command:", PARAMS.command, PARAMS.args, "| edition:", PROGRAM or "all (development)")
    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            print("server:", init.serverInfo.name, init.serverInfo.version, "protocol", init.protocolVersion)
            ok &= init.serverInfo.name == TOOL
            tools = (await s.list_tools()).tools
            print("tools:", [t.name for t in tools], "| description chars:", [len(t.description or "") for t in tools])
            ok &= [t.name for t in tools] == [TOOL]
            desc = tools[0].description or ""
            if PROGRAM:
                others = [p for p in edition.ALL if p != PROGRAM]
                listing = desc.split("READ ACTIONS")[1]
                print("edition text: software hidden", "software" not in listing, "| other programs named in the "
                      "action list:", [p for p in others if p in listing])
                ok &= "software" not in listing and f"works with {PROGRAM} only" in desc
            r = await s.call_tool(TOOL, {"action": "help", "params": {"topic": "run_analysis"}})
            print("help:", "ERROR" if r.isError else "ok", r.content[0].text[:300])
            ok &= not r.isError
            r = await s.call_tool(TOOL, {"action": "list_programs"})
            print("list_programs:", "ERROR" if r.isError else "ok", r.content[0].text[:1200])
            ok &= not r.isError
            listed = json.loads(r.content[0].text)["programs"]
            ok &= tuple(listed) == PROGRAMS
            for name in PROGRAMS:
                if listed[name]["reachable"]:
                    p = {} if PROGRAM else {"software": name}
                    st = await s.call_tool(TOOL, {"action": "get_model_status", "params": p})
                    print(f"{name} status:", "ERROR" if st.isError else "ok", st.content[0].text[:500])
                    ok &= not st.isError
                    out = ROOT / "tests" / "results" / "reports" / f"protocol_{name}.pdf"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    rep = await s.call_tool(TOOL, {"action": "export_report", "params": {
                        **p, "output_path": str(out), "overwrite": True}})
                    print(f"{name} export_report:", "ERROR" if rep.isError else "ok", rep.content[0].text[:400])
                    ok &= not rep.isError and out.is_file() and out.read_bytes()[:5] == b"%PDF-"
            bad = await s.call_tool(TOOL, {"action": "help", "extra": 1})
            print("unknown argument refused:", bad.isError, bad.content[0].text[:160])
            ok &= bad.isError
            bad = await s.call_tool(TOOL, {"action": "get_model_status", "params": {"software": "Tekla"}})
            print("invalid software refused:", bad.isError, bad.content[0].text[:200])
            ok &= bad.isError
            if PROGRAM:
                other = next(p for p in edition.ALL if p != PROGRAM)
                bad = await s.call_tool(TOOL, {"action": "get_model_status", "params": {"software": other}})
                print(f"{other} refused by the {PROGRAM} server:", bad.isError, bad.content[0].text[:250])
                ok &= bad.isError and f"{PROGRAM} server" in bad.content[0].text
    print("RESULT=OK" if ok else "RESULT=FAILED")
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
