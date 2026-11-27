"""Bounded UTF-8 stdio framing for the handshake-based MCP Python SDK."""
from __future__ import annotations

import json
import time
from typing import BinaryIO

import anyio
from mcp import types
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

MAX_FRAME = 1 << 20
MAX_RESPONSE = 8 << 20


def _read_frame(stream: BinaryIO) -> bytes | None:
    line = stream.readline(MAX_FRAME + 1)
    if line and (len(line) > MAX_FRAME or not line.endswith(b"\n")):
        while line and not line.endswith(b"\n"):
            line = stream.readline(MAX_FRAME + 1)
        return None
    return line


def _invalid_constant(value: str):
    raise ValueError("non-finite JSON number")


async def serve(server, tool_names: set[str], stdin: BinaryIO, stdout: BinaryIO) -> None:
    """The SDK owns negotiation, tools, progress, cancellation and result serialization."""
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
                if line is None:
                    await error(None, -32600, "Incomplete or oversized MCP frame")
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

