"""Framework-neutral tool definitions.

A tool set declares its tools once, here, with no reference to any MCP
library. The adapters in ``ai_armory.adapters`` turn them into servers.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class ToolError(Exception):
    """Raise from a handler to return a readable error result to the model."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    # Metadata for the host. AI Armory never prompts; a host like JARVIS reads
    # needs_confirmation and asks the user before letting the call through.
    needs_confirmation: bool = False
    read_only: bool = False


@dataclass(frozen=True)
class Result:
    text: str
    is_error: bool = False


@dataclass
class ToolSet:
    """A named group of tools, loaded and exposed as a unit.

    Tool names are prefixed with the tool set name when exposed, so ``now``
    in the ``clock`` tool set is served as ``clock_now``.
    """

    name: str
    description: str
    tools: list[Tool] = field(default_factory=list)

    def tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any] | None = None,
        *,
        needs_confirmation: bool = False,
        read_only: bool = False,
    ) -> Callable[[Handler], Handler]:
        """Decorator that registers an async handler as a tool in this set."""

        def register(handler: Handler) -> Handler:
            if any(t.name == f"{self.name}_{name}" for t in self.tools):
                raise ValueError(f"duplicate tool {name!r} in tool set {self.name!r}")
            self.tools.append(
                Tool(
                    name=f"{self.name}_{name}",
                    description=description,
                    input_schema={"type": "object", "properties": {}, **(input_schema or {})},
                    handler=handler,
                    needs_confirmation=needs_confirmation,
                    read_only=read_only,
                )
            )
            return handler

        return register


def all_tools(toolsets: list[ToolSet]) -> list[Tool]:
    """Flatten tool sets into one list, rejecting clashing names."""
    seen: dict[str, str] = {}
    tools: list[Tool] = []
    for ts in toolsets:
        for t in ts.tools:
            if t.name in seen:
                raise ValueError(f"tool {t.name!r} is defined by both {seen[t.name]!r} and {ts.name!r}")
            seen[t.name] = ts.name
            tools.append(t)
    return tools


async def call(tool: Tool, arguments: dict[str, Any]) -> Result:
    """Run a handler and normalise its return value or failure into a Result.

    Handlers may return a string (used as-is) or any JSON-serialisable value
    (rendered as JSON). A raised exception becomes an error result.
    """
    try:
        value = await tool.handler(arguments)
    except Exception as e:  # reported to the model, never raised to the host
        return Result(str(e) or type(e).__name__, is_error=True)
    if isinstance(value, Result):
        return value
    if isinstance(value, str):
        return Result(value)
    return Result(json.dumps(value, ensure_ascii=False, default=str))
