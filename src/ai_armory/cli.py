"""Command line: ``ai-armory list`` and ``ai-armory serve --toolsets clock,...``."""

from __future__ import annotations

import argparse
import sys

import anyio

from ai_armory import registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-armory", description="MCP tool sets.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list available tool sets")
    serve = sub.add_parser("serve", help="run a stdio MCP server")
    serve.add_argument(
        "--toolsets",
        default="all",
        help="comma-separated tool set or group names (e.g. google), or 'all' (default)",
    )
    serve.add_argument("--name", default="ai-armory", help="server name reported to the client")
    args = parser.parse_args(argv)

    if args.command == "list":
        for name in registry.available():
            try:
                ts = registry.load(name)
            except ImportError:
                print(f"{name}: not installed (pip install 'ai-armory[{name}]')")
                continue
            print(f"{name}: {ts.description} [{', '.join(t.name for t in ts.tools)}]")
        for name, members in registry.GROUPS.items():
            print(f"{name}: a group of {', '.join(members)}")
        return 0

    # stdout belongs to the MCP protocol, so messages go to stderr
    if args.toolsets == "all":
        toolsets = []
        for name in registry.available():
            try:
                toolsets.append(registry.load(name))
            except ImportError as e:
                print(f"ai-armory: skipping {name}: {e}", file=sys.stderr)
    else:
        names = [n.strip() for n in args.toolsets.split(",") if n.strip()]
        try:
            toolsets = registry.load_many(names)
        except (LookupError, ImportError) as e:
            print(f"ai-armory: {e}", file=sys.stderr)
            return 2

    from ai_armory.adapters.stdio import serve as serve_stdio

    anyio.run(serve_stdio, toolsets, args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
