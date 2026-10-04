import pytest

from ai_armory import ToolError, ToolSet


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def notes():
    """A throwaway tool set covering plain, JSON, failing and gated tools."""
    ts = ToolSet("notes", "Test notes.")
    saved: list[str] = []

    @ts.tool("add", "Add a note.", {"properties": {"text": {"type": "string"}}, "required": ["text"]}, needs_confirmation=True)
    async def add(args):
        saved.append(args["text"])
        return f"saved {args['text']}"

    @ts.tool("list", "List notes.", read_only=True)
    async def list_(args):
        return {"notes": saved}

    @ts.tool("fail", "Always fails.")
    async def fail(args):
        raise ToolError("nope")

    return ts
