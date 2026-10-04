"""Expose tool sets as an in-process Claude Agent SDK MCP server.

    from ai_armory import load_many
    from ai_armory.adapters.claude_sdk import create_sdk_server, confirmation_required

    toolsets = load_many(["clock"])
    options = ClaudeAgentOptions(mcp_servers={"ai-armory": create_sdk_server(toolsets)})
    gated = confirmation_required(toolsets)  # full tool names to ask about
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, ToolAnnotations, create_sdk_mcp_server

from ai_armory import __version__
from ai_armory.core import Tool, ToolSet, all_tools, call


def create_sdk_server(toolsets: list[ToolSet], name: str = "ai-armory") -> McpSdkServerConfig:
    tools = [_to_sdk_tool(t) for t in all_tools(toolsets)]
    return create_sdk_mcp_server(name, __version__, tools)


def confirmation_required(toolsets: list[ToolSet], server_name: str = "ai-armory") -> set[str]:
    """Full Claude tool names (``mcp__<server>__<tool>``) that need the user's go-ahead."""
    return {f"mcp__{server_name}__{t.name}" for t in all_tools(toolsets) if t.needs_confirmation}


def _to_sdk_tool(tool: Tool) -> SdkMcpTool[Any]:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        result = await call(tool, args)
        return {"content": [{"type": "text", "text": result.text}], "is_error": result.is_error}

    return SdkMcpTool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        handler=handler,
        annotations=ToolAnnotations(readOnlyHint=True) if tool.read_only else None,
    )
