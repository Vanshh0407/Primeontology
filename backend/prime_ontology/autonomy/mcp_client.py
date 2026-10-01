"""Minimal MCP (JSON-RPC 2.0 over stdio) client used for tool discovery and calls.

Security: only commands on the server-side allow-list may be spawned. The allow-list is `settings.PRIME_ONTOLOGY_MCP_COMMANDS`
(a list of argv lists). The special registration {"command": "builtin"} resolves to this project's own read-only MCP server,
restricted to the ontology's tenant. Users can therefore never make the server run an arbitrary program.
"""
import json
import os
import queue
import subprocess
import sys
import threading

from django.conf import settings

TIMEOUT = 20
PROTOCOL = "2024-11-05"
MAX_OUTPUT = 200_000


class McpError(Exception):
    pass


def builtin_argv(tenant: str = "") -> list[str]:
    manage = os.path.join(str(settings.BASE_DIR), "manage.py")
    return [sys.executable, manage, "mcp_server", *(["--tenant", tenant] if tenant else [])]


def resolve_command(command, tenant: str = "") -> list[str]:
    if command == "builtin" or command == ["builtin"]:
        return builtin_argv(tenant)
    allowed = getattr(settings, "PRIME_ONTOLOGY_MCP_COMMANDS", None) or []
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise McpError("command must be 'builtin' or an argv list.")
    if command not in allowed:
        raise McpError("This command is not on the server's MCP allow-list (PRIME_ONTOLOGY_MCP_COMMANDS).")
    return command


class Session:
    def __init__(self, argv: list[str]):
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                  env={k: v for k, v in os.environ.items()})
        self.q: queue.Queue = queue.Queue()
        self.n = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        try:
            for line in self.p.stdout:
                self.q.put(line)
        finally:
            self.q.put(None)

    def _send(self, msg: dict):
        try:
            self.p.stdin.write(json.dumps(msg) + "\n")
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            raise McpError("The MCP server closed the connection.") from None

    def request(self, method: str, params: dict | None = None):
        self.n += 1
        rid = self.n
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        while True:
            try:
                line = self.q.get(timeout=TIMEOUT)
            except queue.Empty:
                raise McpError(f"The MCP server did not answer '{method}' within {TIMEOUT}s.") from None
            if line is None:
                raise McpError("The MCP server exited unexpectedly.")
            if len(line) > MAX_OUTPUT:
                raise McpError("The MCP server returned an oversized message.")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore stray log lines
            if msg.get("id") == rid:
                if "error" in msg:
                    raise McpError(f"MCP error: {msg['error'].get('message', 'unknown')}"[:300])
                return msg.get("result") or {}

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self.p.kill()

    def __enter__(self):
        self.info = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "prime-ontology-agents", "version": "1.0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *a):
        self.close()


def discover(argv: list[str]) -> dict:
    with Session(argv) as s:
        info = s.info
        tools = s.request("tools/list").get("tools", [])
    return {"serverInfo": info.get("serverInfo", {}), "tools": [{"name": t.get("name"), "description": (t.get("description") or "")[:500],
                                                                  "inputSchema": t.get("inputSchema") or {}} for t in tools if t.get("name")]}


def call(argv: list[str], tool: str, arguments: dict):
    with Session(argv) as s:
        res = s.request("tools/call", {"name": tool, "arguments": arguments})
    text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
    if res.get("isError"):
        raise McpError(f"Tool error: {text[:300]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text[:20000]}
