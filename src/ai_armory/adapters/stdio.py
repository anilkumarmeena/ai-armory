"""Expose tool sets as a standalone MCP server over stdio (official ``mcp`` SDK)."""

from __future__ import annotations

from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult
from mcp.types import Tool as McpTool

from ai_armory import __version__
from ai_armory.core import Result, ToolSet, all_tools, call

# Clients that understand it can gate these tools; others just ignore _meta.
CONFIRMATION_META_KEY = "ai-armory/needsConfirmation"


def build_server(toolsets: list[ToolSet], name: str = "ai-armory") -> Server:
    tools = {t.name: t for t in all_tools(toolsets)}
    listed = [
        McpTool.model_validate(
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
                "annotations": {"readOnlyHint": True} if t.read_only else None,
                "_meta": {CONFIRMATION_META_KEY: True} if t.needs_confirmation else None,
            }
        )
        for t in tools.values()
    ]

    async def on_list_tools(ctx: Any, params: Any) -> ListToolsResult:
        return ListToolsResult(tools=listed)

    async def on_call_tool(ctx: Any, params: Any) -> CallToolResult:
        tool = tools.get(params.name)
        if tool is None:
            result = Result(f"Unknown tool: {params.name}", is_error=True)
        else:
            result = await call(tool, params.arguments or {})
        return CallToolResult.model_validate(
            {"content": [{"type": "text", "text": result.text}], "isError": result.is_error}
        )

    return Server(name, version=__version__, on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def serve(toolsets: list[ToolSet], name: str = "ai-armory") -> None:
    server = build_server(toolsets, name)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
