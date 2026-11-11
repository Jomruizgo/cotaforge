"""Measure what the server costs the model before any work happens: tool schemas and instructions.

Starts the real server over stdio (fake adapter unless ``--adapter freecad``), lists its tools and prints, per tool
and in total, the size of what the client sends to the model (name + description + input schema, as compact JSON)
in characters and in estimated tokens. The estimate is chars / 4: an upper-ish bound for English text and JSON,
NOT a tokenizer count; use the client's own context report for the exact figure. The ``instructions`` text sent at
initialize is measured the same way.

    python scripts/measure_schema.py [--adapter fake|freecad] [--json]
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CHARS_PER_TOKEN = 4


def tokens(chars: int) -> int:
    return -(-chars // CHARS_PER_TOKEN)


async def measure(adapter: str) -> dict:
    env = {
        **os.environ,
        "COTAFORGE_ADAPTER": adapter,
        "COTAFORGE_WORKSPACE": tempfile.mkdtemp(prefix="cotaforge-measure-"),
        "COTAFORGE_HOME": tempfile.mkdtemp(prefix="cotaforge-measure-home-"),  # empty memory, never the real profile
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "cotaforge_mcp"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        listed = await session.list_tools()
    rows = []
    for tool in listed.tools:
        payload = {"name": tool.name, "description": tool.description, "inputSchema": tool.inputSchema}
        chars = len(json.dumps(payload, separators=(",", ":")))
        rows.append({"tool": tool.name, "chars": chars, "tokens_est": tokens(chars)})
    instructions = init.instructions or ""
    total_chars = sum(r["chars"] for r in rows)
    return {
        "adapter": adapter,
        "tools": rows,
        "tools_total": {"chars": total_chars, "tokens_est": tokens(total_chars)},
        "instructions": {"chars": len(instructions), "tokens_est": tokens(len(instructions))},
        "grand_total": {
            "chars": total_chars + len(instructions),
            "tokens_est": tokens(total_chars + len(instructions)),
        },
        "note": f"tokens_est = ceil(chars / {CHARS_PER_TOKEN}); an estimate, not a tokenizer count",
    }


def render(result: dict) -> str:
    lines = [f"server cad (adapter: {result['adapter']})", f"{'tool':<12}{'chars':>8}{'~tokens':>9}"]
    lines.extend(f"{r['tool']:<12}{r['chars']:>8}{r['tokens_est']:>9}" for r in result["tools"])
    for key in ("tools_total", "instructions", "grand_total"):
        lines.append(f"{key:<12}{result[key]['chars']:>8}{result[key]['tokens_est']:>9}")
    lines.append(result["note"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adapter", default="fake", choices=["fake", "freecad"])
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()
    result = asyncio.run(measure(args.adapter))
    print(json.dumps(result, indent=2) if args.json else render(result))


if __name__ == "__main__":
    main()
