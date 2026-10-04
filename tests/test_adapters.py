import sys

import pytest
from mcp import Client, StdioServerParameters

from ai_armory.adapters import claude_sdk, stdio


def _text(result):
    return "".join(block.text for block in result.content)


async def _exercise(server):
    """Drive a built server through a real MCP client session in-process."""
    async with Client(server) as client:
        listed = {t.name: t for t in (await client.list_tools()).tools}
        assert set(listed) == {"notes_add", "notes_list", "notes_fail"}
        assert listed["notes_list"].annotations.read_only_hint is True

        added = await client.call_tool("notes_add", {"text": "milk"})
        assert not added.is_error and _text(added) == "saved milk"
        assert _text(await client.call_tool("notes_list", {})) == '{"notes": ["milk"]}'

        failed = await client.call_tool("notes_fail", {})
        assert failed.is_error and _text(failed) == "nope"
        return listed


@pytest.mark.anyio
async def test_claude_sdk_adapter(notes):
    config = claude_sdk.create_sdk_server([notes])
    assert config["type"] == "sdk" and config["name"] == "ai-armory"
    await _exercise(config["instance"])


def test_claude_sdk_confirmation_names(notes):
    assert claude_sdk.confirmation_required([notes]) == {"mcp__ai-armory__notes_add"}
    assert claude_sdk.confirmation_required([notes], server_name="tools") == {"mcp__tools__notes_add"}


@pytest.mark.anyio
async def test_stdio_adapter_server(notes):
    listed = await _exercise(stdio.build_server([notes]))
    assert listed["notes_add"].meta == {stdio.CONFIRMATION_META_KEY: True}
    assert listed["notes_list"].meta is None


@pytest.mark.anyio
async def test_stdio_adapter_unknown_tool(notes):
    async with Client(stdio.build_server([notes])) as client:
        result = await client.call_tool("notes_nope", {})
        assert result.is_error and "Unknown tool" in _text(result)


@pytest.mark.anyio
async def test_ai_armory_serve_over_real_stdio():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "ai_armory.cli", "serve", "--toolsets", "clock"]
    )
    async with Client(params) as client:
        assert [t.name for t in (await client.list_tools()).tools] == ["clock_now"]
        result = await client.call_tool("clock_now", {"timezone": "UTC"})
        assert not result.is_error and "UTC" in _text(result)
