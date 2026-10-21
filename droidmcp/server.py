"""Minimal, dependency-free Model Context Protocol server over stdio.

The official ``mcp`` Python SDK drags in pydantic/anyio and pins Python
versions.  This server speaks the same wire protocol (JSON-RPC 2.0, one
JSON object per line on stdin/stdout) using nothing but the standard library,
which means it installs anywhere Python 3.9+ runs — including Termux and
locked-down CI containers.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "droidmcp"


class ToolError(Exception):
    """Raised by a tool to return an MCP-level error to the client."""


def _content_text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def text_result(text: str) -> dict[str, Any]:
    return {"content": [_content_text(text)]}


def json_result(payload: Any) -> dict[str, Any]:
    return {"content": [_content_text(json.dumps(payload, ensure_ascii=False, indent=2))]}


def error_result(message: str) -> dict[str, Any]:
    return {"content": [_content_text(message)], "isError": True}


class MCPServer:
    """Tiny MCP server: register tools, then :meth:`run` the stdio loop."""

    def __init__(self, name: str = SERVER_NAME, version: str = "0.1.0") -> None:
        self.name = name
        self.version = version
        self._tools: dict[str, tuple[dict[str, Any], Callable[..., Any]]] = {}

    # ---------------------------------------------------------------- registration

    def tool(
        self,
        name: str,
        description: str,
        schema: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator registering a function as an MCP tool."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[name] = (
                {
                    "name": name,
                    "description": description,
                    "inputSchema": schema or {"type": "object", "properties": {}},
                },
                func,
            )
            return func

        return decorator

    def list_tools(self) -> list[dict[str, Any]]:
        return [spec for spec, _ in self._tools.values()]

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        if name not in self._tools:
            raise ToolError(f"unknown tool: {name}")
        _, func = self._tools[name]
        try:
            return func(**(arguments or {}))
        except ToolError:
            raise
        except TypeError as exc:
            raise ToolError(f"invalid arguments for {name}: {exc}") from exc

    # ---------------------------------------------------------------- JSON-RPC

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        req_id = request.get("id")
        params = request.get("params") or {}

        # Notifications (no id) never get a response.
        is_notification = req_id is None

        try:
            if method == "initialize":
                result: Any = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.name, "version": self.version},
                }
            elif method == "tools/list":
                result = {"tools": self.list_tools()}
            elif method == "tools/call":
                result = self.call_tool(params.get("name", ""), params.get("arguments"))
            elif method == "ping":
                result = {}
            elif method in ("notifications/initialized", "initialized"):
                return None
            else:
                if is_notification:
                    return None
                return self._error(req_id, -32601, f"method not found: {method}")
        except ToolError as exc:
            return self._error(req_id, -32602, str(exc))
        except Exception as exc:  # noqa: BLE001 - surface everything to the client
            # A crashing server is far worse than a failed call: keep the
            # process alive and report the failure as tool content so the model
            # can react to it.
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"tool {method!r} failed: {type(exc).__name__}: {exc}",
                        }
                    ],
                    "isError": True,
                },
            }

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    # ---------------------------------------------------------------- transport

    def run(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = self._error(None, -32700, "parse error")
            else:
                response = self.handle(request)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
