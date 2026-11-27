"""CSI MCP server (stdio). Claude launches this process; it drives ETABS, SAP2000 or SAFE through the CSI Open API.

One server per program (0.7.0, see edition.py): etabs-mcp-server.exe, sap2000-mcp-server.exe, safe-mcp-server.exe,
each with ONE tool named after its program - `etabs(action, params)` etc. - so the Claude app has a single thing to
allow, once. Set CSI_MCP_SPLIT=1 to expose two tools instead (`etabs_read`, `etabs_edit`) to allow reading without
allowing changes to models.

    server.py --program SAP2000                     run one edition over stdio (development)
    server.py                                       all three programs in one tool `csi` (development / tests only)
    server.py [--program X] --configure-clients [cmd]   add the edition to Claude Desktop / Codex (and remove the
                                                    old combined "csi" entry)
    server.py [--program X] --remove-clients        remove it again
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import contextlib
import json
import logging
import os
import sys
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

# Reserve the original pipe before importing SDKs or native COM libraries. This
# also catches native writes to fd 1 and leaves one stable protocol writer.
_wire = None
if __name__ == "__main__" and not {"--configure-clients", "--remove-clients", "--run-chatgpt-tunnel", "-h", "--help"} & set(sys.argv[1:]):
    _wire = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import CallToolRequest, CallToolResult, ListToolsRequest, PingRequest, TextContent, ToolAnnotations
from pydantic import ValidationError

import catalog
import edition
import programs
from actions import ACTIONS, ActionFailed, HelpP, bridge
from csi_bridge import CSIError, CSIValidationError, ComExecutor
from table_tools import TableError
from stdio_transport import serve
from windows_lock import exclusive
import safety

_level = os.environ.get("CSI_MCP_LOG", "INFO").strip().upper()
logging.basicConfig(level=_level if isinstance(logging.getLevelName(_level), int) else "INFO", stream=sys.stderr,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("csi_mcp.server")

HEARTBEAT_SECONDS = 10
executor = ComExecutor()
atexit.register(lambda: executor.shutdown(bridge.release_all))


@dataclass
class Call:
    """A CSI call waiting for, or running on, the COM thread."""
    action: str
    started: float
    future: Future


CALLS: dict[Any, Call] = {}       # MCP request id -> its call (event-loop thread only)
ABANDONED: list[Call] = []        # at most one: a call the client cancelled while CSI was already working on it


def may_cancel(request_id: Any) -> bool:
    """notifications/cancelled: withdraw a call that has not started; a call CSI is working on cannot be stopped.

    Without this the SDK answers "Request cancelled" while the edit still completes in the program, and a retry
    then repeats it. An ignored cancellation is remembered, so later calls say what happened (run_action).
    """
    call = CALLS.get(request_id)
    if call is None or call.future.cancel():
        return True
    if not call.future.done():
        ABANDONED[:] = [call]
        log.warning("Cancellation of '%s' ignored: the CSI call is already running and cannot be stopped.", call.action)
    return False


def _earlier_call() -> tuple[CallToolResult | None, dict[str, Any] | None]:
    """(refusal while a cancelled call still runs, or a note on how it ended for the first call after it)."""
    if not ABANDONED:
        return None, None
    call = ABANDONED[0]
    seconds = round(time.time() - call.started)
    if not call.future.done():
        return _error({"error_type": "Busy", "message": (
            f"The earlier '{call.action}' call (started {seconds} s ago) was cancelled or timed out on the client, "
            "but a CSI call cannot be stopped: the program is still working on it. This call did not start."),
            "hint": "If the program shows a dialog, close it. When it has finished, check the model before "
                    "repeating an edit: the earlier call may have completed."}), None
    ABANDONED.clear()
    failure = call.future.exception()
    return None, {"action": call.action, "started_seconds_ago": seconds,
                  "outcome": "finished" if failure is None else f"failed: {failure}",
                  "note": "This call was cancelled or timed out on the client while CSI was working on it; it was "
                          "not stopped. Check the model before repeating that action."}


class CsiServer(FastMCP):
    """Tools only; refuse unknown top-level arguments instead of silently dropping them."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        handlers = self._mcp_server.request_handlers      # FastMCP also registers empty prompts and resources:
        for request in [r for r in handlers if r not in (PingRequest, ListToolsRequest, CallToolRequest)]:
            del handlers[request]                         # do not advertise capabilities this server does not have

    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema = {**tool.inputSchema, "additionalProperties": False}
        return tools

    async def call_tool(self, name, arguments):
        allowed = {"action", "params"}
        if set(arguments or {}) - allowed:
            return _error({"error_type": "InvalidParameters", "message": "Only action and params are accepted."})
        if not isinstance((arguments or {}).get("action"), str):
            return _error({"error_type": "InvalidParameters", "message": "action must be a string."})
        if arguments.get("params") is not None and not isinstance(arguments["params"], dict):
            return _error({"error_type": "InvalidParameters", "message": "params must be an object."})
        return await super().call_tool(name, arguments)

    async def run_stdio_async(self):
        await serve(self._mcp_server, {t.name for t in await self.list_tools()},
                    sys.stdin.buffer, _wire or sys.stdout.buffer, may_cancel)


# --------------------------------------------------------------------------- dispatch (runs on the COM thread)

def dispatch(action: str, params: dict[str, Any] | None, allow_read: bool = True, allow_edit: bool = True) -> Any:
    params = {} if params is None else params
    if not isinstance(params, dict):
        raise ValueError('params must be an object, e.g. {"software": "ETABS"}.')
    name = (action or "").strip()
    if name == "help":
        topic = HelpP.model_validate(params).topic
        return catalog.help_index() if topic is None else catalog.help_data(None if topic == "all" else topic)
    act = ACTIONS.get(name)
    if act is None:
        raise ValueError(f"Unknown action '{name}'. Actions: help, {', '.join(ACTIONS)}.")
    if act.kind == "edit" and not allow_edit:
        raise ValueError(f"'{name}' changes the model or the program: use the {edition.tool_names(True)[1]} tool.")
    if act.kind == "read" and not allow_read:
        raise ValueError(f"'{name}' only reads: use the {edition.tool_names(True)[0]} tool.")
    one = edition.current()
    if one and "software" in act.params.model_fields:
        params = {"software": one, **params}     # an edition fills software; another program is refused below
    validated = act.params.model_validate(params)
    software = getattr(validated, "software", None)
    if software:
        programs.check_enabled(software)
        if software not in programs.expand(act.programs):
            alt = programs.MODULES[software].ALTERNATIVES.get(name, "")
            raise CSIValidationError(f"'{name}' is not available in {software}: its API has no such feature.",
                                     hint=alt or f"Available in {', '.join(programs.expand(act.programs))}.")
        catalog.check_program_options(act, validated, software)
    if not catalog.available(act):
        raise ValueError(f"'{name}' is not offered by this server (CSI_MCP_PROGRAMS={','.join(programs.enabled())}).")
    # an edit action in its read mode (edit_database_table mode 'read') changes nothing: not a change-log entry
    logged = act.kind == "edit" and not getattr(validated, "dry_run", False) and getattr(validated, "mode", "") != "read"
    # one name per program whatever the action (list_programs has no software): Windows object names are case-sensitive
    lock = f"model-access-{(software or edition.server_name()).upper()}"
    with exclusive(lock), bridge.action_scope(act.kind == "edit" and name != "connect"):
        try:
            result = act.run(validated)
        except Exception as exc:
            if logged:
                _log_edit(name, validated, ok=False, outcome=getattr(exc, "payload", None) or str(exc))
            raise
        if logged:
            _log_edit(name, validated, ok=True, outcome=result)
        return result


def _log_edit(name: str, params: Any, ok: bool, outcome: Any) -> None:
    """One change-log line per edit that ran (safety.log_change never raises)."""
    software = getattr(params, "software", None)
    model = None
    if software:
        with contextlib.suppress(Exception):
            model = bridge.cached_filename(software)
    safety.log_change({"action": name, "software": software, "model": model, "ok": ok,
                       "params": params.model_dump(), "outcome": outcome})


def _error(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(isError=True, structuredContent=payload,
                          content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=1,
                                                                            default=str))])


def _validation_text(action: str, exc: ValidationError) -> dict[str, Any]:
    problems = [f"{'.'.join(str(x) for x in e['loc']) or 'params'}: {e['msg']}" for e in exc.errors()]
    return {"error_type": "InvalidParameters", "message": f"{action}: " + "; ".join(problems),
            "hint": f"action='help', params={{\"topic\": \"{action}\"}} lists the parameters."}


async def run_action(ctx: Context | None, action: str, params: dict[str, Any] | None,
                     allow_read: bool, allow_edit: bool) -> Any:
    """Run on the COM thread; send a progress heartbeat while long actions (analysis, opening files) work."""

    async def heartbeat(context: Context) -> None:
        beats = 0
        while True:
            await anyio.sleep(HEARTBEAT_SECONDS)
            beats += 1
            try:
                await context.report_progress(beats, None, f"Waiting for {waiting_for}")
            except Exception:  # noqa: BLE001 - progress is best effort
                return

    software = (params or {}).get("software") if isinstance(params, dict) else None
    waiting_for = software if isinstance(software, str) else (edition.current() or "CSI")
    is_help = action.strip() == "help"
    busy, earlier = (None, None) if is_help else _earlier_call()
    if busy is not None:
        return busy

    result: Any = None
    error: BaseException | None = None
    async with anyio.create_task_group() as tg:
        if ctx is not None and ctx.request_context.meta and ctx.request_context.meta.progressToken is not None:
            tg.start_soon(heartbeat, ctx)
        try:
            if is_help:
                result = dispatch(action, params, allow_read, allow_edit)
            else:
                result = await _on_com_thread(ctx, action.strip(), params, allow_read, allow_edit)
        except Exception as exc:  # noqa: BLE001 - turned into a tool error below
            error = exc
        finally:
            tg.cancel_scope.cancel()

    def noted(payload: dict[str, Any]) -> dict[str, Any]:
        return {**payload, "earlier_cancelled_call": earlier} if earlier else payload

    if error is None:
        return noted(result) if isinstance(result, dict) else result
    if isinstance(error, ActionFailed):
        return _error(noted(error.payload))
    if isinstance(error, ValidationError):
        return _error(noted(_validation_text(action, error)))
    if isinstance(error, (CSIError, TableError)):
        return _error(noted({"error_type": type(error).__name__, "message": str(error),
                             "hint": getattr(error, "hint", "")}))
    if isinstance(error, ValueError):
        return _error(noted({"error_type": "InvalidRequest", "message": str(error)}))
    log.exception("Unexpected error in %s", action, exc_info=error)
    return _error(noted({"error_type": "UnexpectedError", "message": f"{type(error).__name__}: {error}",
                         "hint": "If the program shows a dialog, close it. Inspect the model before retrying an "
                                 "edit; details are in the server log (stderr)."}))


async def _on_com_thread(ctx: Context | None, action: str, params: dict[str, Any] | None,
                         allow_read: bool, allow_edit: bool) -> Any:
    """Queue the action for the COM thread under its MCP request id, so may_cancel can find it."""
    future = executor.submit(dispatch, action, params, allow_read, allow_edit)
    key = ctx.request_context.request_id if ctx is not None else object()
    CALLS[key] = Call(action, time.time(), future)
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        if future.cancelled() and ctx is not None:
            # withdrawn by may_cancel before it started: the SDK cancels this request next and answers the client
            await anyio.sleep_forever()
        raise
    finally:
        CALLS.pop(key, None)


# --------------------------------------------------------------------------- tools

READ_TOOL = ToolAnnotations(title="CSI (read models and export files)", readOnlyHint=False, destructiveHint=False,
                            openWorldHint=False)
EDIT_TOOL = ToolAnnotations(title="CSI", readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                            openWorldHint=False)


def create_server(chatgpt: bool = False) -> FastMCP:
    mcp = CsiServer(edition.server_name())
    mcp._mcp_server.version = catalog.SERVER_VERSION
    split = chatgpt or os.environ.get("CSI_MCP_SPLIT", "").strip().lower() in ("1", "true", "yes")
    label = edition.current() or "CSI"
    read_ann = READ_TOOL.model_copy(update={"title": f"{label} (read models and results)"})
    edit_ann = EDIT_TOOL.model_copy(update={"title": label})
    if split:
        read_name, edit_name = edition.tool_names(True)

        @mcp.tool(name=read_name, description=catalog.tool_description("read") if not chatgpt else
                  f"Read {label} models and analysis results. Call help to list actions and parameter schemas. "
                  f"Table reads can change {label} display settings. Export actions write files.", annotations=read_ann)
        async def tool_read(action: str, ctx: Context, params: dict[str, Any] | None = None) -> Any:
            return await run_action(ctx, action, params, allow_read=True, allow_edit=False)

        @mcp.tool(name=edit_name, description=catalog.tool_description("edit") if not chatgpt else
                  f"Edit the active {label} model, files or application. Use {read_name} help for action parameters. "
                  "Confirm edits, file operations and unlocking with the user. Unlocking deletes solver results.",
                  annotations=edit_ann)
        async def tool_edit(action: str, ctx: Context, params: dict[str, Any] | None = None) -> Any:
            return await run_action(ctx, action, params, allow_read=False, allow_edit=True)
    else:
        @mcp.tool(name=edition.server_name(), description=catalog.tool_description("all"), annotations=edit_ann)
        async def tool_all(action: str, ctx: Context, params: dict[str, Any] | None = None) -> Any:
            return await run_action(ctx, action, params, allow_read=True, allow_edit=True)
    return mcp


def _default_entry() -> dict[str, Any]:
    if getattr(sys, "frozen", False):
        return {"command": sys.executable}
    args = [os.path.abspath(__file__)]
    if edition.current():
        args += ["--program", edition.current()]
    return {"command": sys.executable, "args": args}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CSI ETABS / SAP2000 / SAFE MCP server (stdio)")
    parser.add_argument("--program", choices=edition.ALL, type=str.upper,
                        help="serve this program only (the packaged exes take it from their name)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--chatgpt", action="store_true", help="serve split CSI tools over stdio for Secure MCP Tunnel")
    modes.add_argument("--run-chatgpt-tunnel", action="store_true", help="run OpenAI tunnel-client for this edition")
    modes.add_argument("--configure-clients", nargs="?", const="", metavar="COMMAND",
                        help="add this server to Claude Desktop and Codex (COMMAND default: this server)")
    modes.add_argument("--remove-clients", action="store_true", help="remove it from Claude Desktop and Codex")
    args = parser.parse_args(argv)
    if args.program:
        edition.force(args.program)
    if args.run_chatgpt_tunnel:
        import chatgpt_tunnel
        return chatgpt_tunnel.run(edition.current())
    if args.configure_clients is not None or args.remove_clients:
        import client_config
        if args.remove_clients:
            return client_config.run(None)
        entry = {"command": args.configure_clients} if args.configure_clients else _default_entry()
        return client_config.run(entry)
    try:
        create_server(args.chatgpt).run(transport="stdio")
    finally:
        executor.shutdown(bridge.release_all)
        if _wire:
            _wire.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
