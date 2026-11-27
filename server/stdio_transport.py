"""Bounded UTF-8 stdio framing for the handshake-based MCP Python SDK."""
from __future__ import annotations

import json
import re
import time
from typing import BinaryIO, Callable

import anyio
from mcp import types
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

MAX_FRAME = 8 << 20     # the largest request the action schemas allow (20 000 table rows) is a few MiB
MAX_RESPONSE = 8 << 20
_ID = rb'(-?\d{1,18}|"[^"\\]{0,128}")'
# the id is read only where it cannot belong to a nested object: before params at the very start of the frame
# (Python SDK order) or as the last member before the closing brace (TypeScript SDK order: method, params, jsonrpc, id)
_HEAD_ID = re.compile(rb'^\s*\{(?:\s*"(?:jsonrpc|method)"\s*:\s*"[^"\\]{0,80}"\s*,)*\s*"id"\s*:\s*' + _ID + rb'\s*[,}]')
_TAIL_ID = re.compile(rb'[\]}"\d]\s*,\s*(?:"jsonrpc"\s*:\s*"2\.0"\s*,\s*)?"id"\s*:\s*' + _ID + rb'\s*\}\s*$')


class Oversized:
    """A frame that was dropped; `rid` is its request id when it could be read without parsing the frame."""

    def __init__(self, head: bytes, tail: bytes) -> None:
        found = _HEAD_ID.match(head) or _TAIL_ID.search(tail)
        self.rid = json.loads(found.group(1)) if found else None


def _read_frame(stream: BinaryIO) -> bytes | Oversized | None:
    """One line. b"" = end of input; None = a last line cut off by the end of input; Oversized = dropped."""
    line = stream.readline(MAX_FRAME + 1)
    if line and not line.endswith(b"\n"):
        if len(line) <= MAX_FRAME:
            return None
        head = tail = line[:512]
        while line and not line.endswith(b"\n"):
            tail = (tail + line)[-512:]
            line = stream.readline(MAX_FRAME + 1)
        return Oversized(head, (tail + line)[-512:])
    return line


def _invalid_constant(value: str):
    raise ValueError("non-finite JSON number")


async def serve(server, tool_names: set[str], stdin: BinaryIO, stdout: BinaryIO,
                may_cancel: Callable[[object], bool] | None = None) -> None:
    """The SDK owns negotiation, tools, progress and result serialization.

    Cancellation: the SDK answers a cancelled request with "Request cancelled" at once, whatever the handler is
    doing. A CSI call that already runs on the COM thread cannot be stopped, so `may_cancel(request_id)` decides:
    True = forward the notification (the call had not started and is withdrawn), False = ignore it, as the MCP
    specification allows for a request that cannot be cancelled; the call then answers with its real outcome.
    """
    source, incoming = anyio.create_memory_object_stream(0)
    outgoing, sink = anyio.create_memory_object_stream(0)
    methods = {model.model_fields["method"].default for model in server.request_handlers} | {"initialize"}
    state = "new"
    init_id = None
    pending = set()
    lock = anyio.Lock()
    credits, last = 32.0, time.monotonic()

    async def write(data: bytes) -> None:
        async with lock:
            await anyio.to_thread.run_sync(stdout.write, data + b"\n")
            await anyio.to_thread.run_sync(stdout.flush)

    async def error(rid, code, message):
        await write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                "error": {"code": code, "message": message}}).encode())

    async def read():
        nonlocal state, init_id, credits, last
        async with source:
            while True:
                line = await anyio.to_thread.run_sync(_read_frame, stdin)
                if line == b"":
                    return
                if isinstance(line, Oversized):
                    # answer with the request's own id when it could be read, so the client is not left waiting
                    await error(line.rid, -32600, f"MCP frame over {MAX_FRAME >> 20} MiB. This request did not start.")
                    continue
                if line is None:
                    await error(None, -32600, "Incomplete MCP frame")
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"), parse_constant=_invalid_constant)
                except (ValueError, RecursionError):
                    await error(None, -32700, "Parse error")
                    continue
                if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                    await error(None, -32600, "Invalid request")
                    continue
                method, rid = msg.get("method"), msg.get("id")
                if "method" not in msg and ("error" in msg or "result" in msg):
                    # This tools-only server makes no requests to the client.
                    continue
                notification = "id" not in msg
                if not isinstance(method, str) or (not notification and type(rid) not in (int, str)):
                    await error(None, -32600, "Invalid request")
                    continue
                if "result" in msg or "error" in msg:
                    if not notification:
                        await error(rid, -32600, "Mixed request and response fields")
                    continue
                if not notification and method not in methods:
                    await error(rid, -32601, "Method not found")
                    continue
                try:
                    if "params" in msg and not isinstance(msg["params"], dict):
                        raise ValueError("params must be an object")
                    if notification:
                        types.ClientNotification.model_validate(msg, strict=True)
                    else:
                        types.ClientRequest.model_validate(msg, strict=True)
                    envelope = types.JSONRPCMessage.model_validate(msg, strict=True)
                except (ValueError, ValidationError, RecursionError):
                    if not notification:
                        await error(rid, -32602, "Invalid request parameters")
                    continue
                if notification:
                    if method == "notifications/initialized" and state == "negotiated":
                        state = "ready"
                    elif state != "ready" or method == "notifications/initialized":
                        continue
                    elif (method == "notifications/cancelled" and may_cancel is not None
                          and not may_cancel(msg.get("params", {}).get("requestId"))):
                        continue
                else:
                    if len(pending) >= 64:
                        await error(rid, -32000, "Too many pending requests")
                        continue
                    if rid in pending:
                        await error(rid, -32600, "Request ID already in use")
                        continue
                    if method == "initialize" and state == "new":
                        state, init_id = "initializing", rid
                    elif method == "initialize" or (state != "ready" and method != "ping"):
                        await error(rid, -32600, "Invalid request for the current initialization state")
                        continue
                    if method == "tools/call":
                        if msg["params"].get("name") not in tool_names:
                            await error(rid, -32602, "Unknown tool")
                            continue
                        now = time.monotonic()
                        credits, last = min(32.0, credits + (now - last) * 8), now
                        if credits < 1:
                            await error(rid, -32000, "CSI tool call rate exceeded. This call did not start.")
                            continue
                        credits -= 1
                    pending.add(rid)
                await source.send(SessionMessage(envelope))

    async def output():
        nonlocal state, init_id
        async with sink:
            async for message in sink:
                response = message.message.root
                if isinstance(response, (types.JSONRPCResponse, types.JSONRPCError)):
                    pending.discard(response.id)
                    if state == "initializing" and response.id == init_id:
                        state = "negotiated" if isinstance(response, types.JSONRPCResponse) else "new"
                data = response.model_dump_json(by_alias=True, exclude_none=True).encode()
                if len(data) > MAX_RESPONSE and isinstance(response, types.JSONRPCResponse):
                    await error(response.id, -32603, "Result exceeds delivery limit. Inspect edits before retrying.")
                else:
                    await write(data)

    async with anyio.create_task_group() as group:
        group.start_soon(read)
        group.start_soon(output)
        await server.run(incoming, outgoing, server.create_initialization_options())

