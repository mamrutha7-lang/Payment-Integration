"""Call the dev MCP surface the way an MCP client does, and print what it answers.

This is the verification step 08-tooling-mcp.md §3 rule 3 asks for: while
building a screen, call the endpoint the screen reads AS THE SEEDED ROLE and
compare the payload with the board, rather than clicking through a browser and
trusting what you see.

It speaks the real protocol — `initialize`, `notifications/initialized`,
`tools/list`, `tools/call` over streamable HTTP — so what it proves is that the
mount works for any MCP client, not just for curl.

    python tools/mcp_probe.py --list
    python tools/mcp_probe.py --email student@bgscet.ac.in --call student_profile
    python tools/mcp_probe.py --email mentor@bgscet.ac.in --call mentor_mentees

Development only: it needs the surface mounted, which needs ENV on the dev
allowlist and MCP_DEV_SURFACE=true.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

# Run from anywhere: this lives under apps/api-py/tools/, and `app` is a
# sibling of that directory rather than of this file.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MCP_URL = "http://127.0.0.1:3300/mcp"
PROTOCOL_VERSION = "2025-06-18"


def parse_response(text: str) -> dict:
    """One JSON-RPC response, out of either transport framing.

    Streamable HTTP answers with `text/event-stream` when the server chooses
    to, and with plain JSON when it does not. A client has to read both.
    """
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])
    return json.loads(text)


class DevMcpClient:
    """The smallest client that can hold a session and call a tool."""

    def __init__(self, cookie: str | None) -> None:
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if cookie:
            # The surface forwards this into the real handler, so every call
            # runs through the same require_* dependencies the browser meets.
            self.headers["Cookie"] = cookie
        self.client = httpx.Client(timeout=30.0)
        self.session_id: str | None = None

    def send(self, method: str, params: dict | None = None, request_id: int | None = 1) -> dict:
        body: dict = {"jsonrpc": "2.0", "method": method}
        if request_id is not None:
            body["id"] = request_id
        if params is not None:
            body["params"] = params
        headers = dict(self.headers)
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        response = self.client.post(MCP_URL, headers=headers, json=body)
        response.raise_for_status()
        if "mcp-session-id" in response.headers:
            self.session_id = response.headers["mcp-session-id"]
        if not response.text.strip():
            return {}
        return parse_response(response.text)

    def initialize(self) -> dict:
        answer = self.send(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "reep-mcp-probe", "version": "1"},
            },
        )
        self.send("notifications/initialized", {}, request_id=None)
        return answer

    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            answer = self.send("tools/list", params, request_id=2)
            result = answer.get("result", {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self.send("tools/call", {"name": name, "arguments": arguments or {}}, request_id=3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", help="seeded account to call as; omit to call signed out")
    parser.add_argument("--list", action="store_true", help="list the tools the surface publishes")
    parser.add_argument("--call", help="tool name to call")
    parser.add_argument("--grep", help="only list tools whose name contains this")
    parser.add_argument(
        "--keys",
        help="print only these top-level keys of the answer, comma separated",
    )
    parser.add_argument(
        "--full", action="store_true", help="print the whole answer, not the first 4000 characters"
    )
    arguments = parser.parse_args(argv)

    cookie = None
    if arguments.email:
        from app.dev_session import session_cookie_for

        cookie = session_cookie_for(arguments.email)

    client = DevMcpClient(cookie)
    handshake = client.initialize()
    server = handshake.get("result", {}).get("serverInfo", {})
    print(f"connected to {server.get('name')} (protocol {handshake.get('result', {}).get('protocolVersion')})")
    if arguments.email:
        print(f"calling as {arguments.email}")

    if arguments.list or arguments.grep:
        tools = client.list_tools()
        shown = [t for t in tools if not arguments.grep or arguments.grep in t["name"]]
        print(f"{len(tools)} tools published, {len(shown)} shown")
        for tool in sorted(shown, key=lambda t: t["name"]):
            print(f"  {tool['name']}")

    if arguments.call:
        answer = client.call_tool(arguments.call)
        result = answer.get("result", {})
        if "error" in answer:
            print(json.dumps(answer["error"], indent=2))
            return 1
        for block in result.get("content", []):
            if block.get("type") != "text":
                continue
            try:
                payload = json.loads(block["text"])
            except json.JSONDecodeError:
                print(block["text"][:4000])
                continue
            if arguments.keys and isinstance(payload, dict):
                wanted = [key.strip() for key in arguments.keys.split(",")]
                payload = {key: payload.get(key) for key in wanted}
            rendered = json.dumps(payload, indent=2)
            print(rendered if arguments.full else rendered[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
