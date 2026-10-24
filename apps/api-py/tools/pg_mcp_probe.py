"""Ask the Postgres MCP server about the dev database, over stdio.

The counterpart to tools/mcp_probe.py. That one calls this repository's own
API; this one drives `postgres-mcp` in RESTRICTED mode, which is what
08-tooling-mcp.md §3 rule 2 asks for before and after a migration: read the
touched tables' shape, count rows, sample what a backfill wrote, and `EXPLAIN`
a new scoped query.

Restricted mode is read-only with a statement timeout, so this cannot write.
Migrations stay Alembic files applied with `alembic upgrade head`; nothing here
changes the database.

    python tools/pg_mcp_probe.py --list
    python tools/pg_mcp_probe.py --sql "select count(*) from users"
    python tools/pg_mcp_probe.py --schema users

Uses the official `mcp` client from requirements-dev.txt, so it speaks the same
protocol any MCP client would.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DATABASE_URI = os.environ.get(
    "DATABASE_URI", "postgresql://reep:reep_dev_password@localhost:5433/reep_py"
)


def render(result) -> None:
    """Print a tool result's text blocks, pretty where they are JSON."""
    for block in result.content:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            print(json.dumps(json.loads(text), indent=2)[:6000])
        except json.JSONDecodeError:
            print(text[:6000])


async def run(arguments: argparse.Namespace) -> int:
    server = StdioServerParameters(
        command="postgres-mcp",
        # Restricted: read-only transactions and an execution-time limit.
        args=["--access-mode=restricted"],
        env={**os.environ, "DATABASE_URI": DATABASE_URI},
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            if arguments.list:
                tools = await session.list_tools()
                print(f"{len(tools.tools)} tools published by postgres-mcp")
                for tool in sorted(tools.tools, key=lambda t: t.name):
                    summary = (tool.description or "").strip().splitlines()
                    print(f"  {tool.name:22} {summary[0] if summary else ''}"[:110])
                return 0

            if arguments.objects:
                render(await session.call_tool("list_objects", {"schema_name": "public"}))
                return 0

            if arguments.schema:
                render(
                    await session.call_tool(
                        "get_object_details",
                        {"schema_name": "public", "object_name": arguments.schema},
                    )
                )
                return 0

            if arguments.explain:
                render(await session.call_tool("explain_query", {"sql": arguments.explain}))
                return 0

            if arguments.sql:
                render(await session.call_tool("execute_sql", {"sql": arguments.sql}))
                return 0

    print("Nothing asked. Try --list.", file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list the server's tools")
    parser.add_argument("--objects", action="store_true", help="list the public schema's tables")
    parser.add_argument("--schema", help="describe one table")
    parser.add_argument("--sql", help="run one read-only statement")
    parser.add_argument("--explain", help="EXPLAIN one statement")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
