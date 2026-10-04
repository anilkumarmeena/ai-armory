import pytest

import ai_armory
from ai_armory import ToolSet, all_tools, call


def test_tools_are_prefixed_and_carry_metadata(notes):
    tools = {t.name: t for t in notes.tools}
    assert set(tools) == {"notes_add", "notes_list", "notes_fail"}
    assert tools["notes_add"].needs_confirmation
    assert not tools["notes_add"].read_only
    assert tools["notes_list"].read_only
    assert not tools["notes_list"].needs_confirmation


def test_schema_is_completed_to_an_object_schema(notes):
    tools = {t.name: t for t in notes.tools}
    assert tools["notes_list"].input_schema == {"type": "object", "properties": {}}
    assert tools["notes_add"].input_schema["type"] == "object"
    assert tools["notes_add"].input_schema["required"] == ["text"]


def test_duplicate_tool_in_set_is_rejected():
    ts = ToolSet("x", "")

    @ts.tool("a", "")
    async def a(args):
        return ""

    with pytest.raises(ValueError):

        @ts.tool("a", "")
        async def again(args):
            return ""


def test_clashing_names_across_sets_are_rejected(notes):
    with pytest.raises(ValueError):
        all_tools([notes, notes])


@pytest.mark.anyio
async def test_call_normalises_results(notes):
    tools = {t.name: t for t in notes.tools}
    assert (await call(tools["notes_add"], {"text": "hi"})).text == "saved hi"
    assert (await call(tools["notes_list"], {})).text == '{"notes": ["hi"]}'
    failed = await call(tools["notes_fail"], {})
    assert failed.is_error and failed.text == "nope"


def test_registry_lists_and_loads_builtin():
    assert "clock" in ai_armory.available()
    clock = ai_armory.load("clock")
    assert [t.name for t in clock.tools] == ["clock_now"]
    with pytest.raises(LookupError):
        ai_armory.load("no-such-toolset")


@pytest.mark.anyio
async def test_clock_now():
    (now,) = ai_armory.load("clock").tools
    result = await call(now, {"timezone": "Asia/Kolkata"})
    assert not result.is_error and "+0530" in result.text
    assert (await call(now, {"timezone": "Mars/Olympus"})).is_error
