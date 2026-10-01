"""A tiny stand-in MCP server (stdio JSON-RPC) used to test discovery and calls without needing a database."""
import json
import sys

TOOLS = [{"name": "echo", "description": "Echo the text back.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
         {"name": "explode", "description": "Always fails.", "inputSchema": {"type": "object", "properties": {}}}]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        continue
    if method == "initialize":
        res = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0.1"}}
    elif method == "tools/list":
        res = {"tools": TOOLS}
    elif method == "tools/call":
        if params.get("name") == "echo":
            res = {"content": [{"type": "text", "text": json.dumps({"echo": params["arguments"].get("text")})}], "isError": False}
        else:
            res = {"content": [{"type": "text", "text": "boom"}], "isError": True}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "nope"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}), flush=True)
