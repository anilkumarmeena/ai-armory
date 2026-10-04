"""Finding and loading tool sets by name.

Each tool set lives in its own module exposing a module-level ``toolset``.
Modules are imported only when asked for, so a tool set whose optional
dependencies are missing never breaks the others.

Third-party packages can add tool sets through the ``ai_armory.toolsets``
entry point group, pointing at a ``ToolSet`` object.

A group such as ``google`` names several built-in tool sets at once.
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points

from ai_armory.core import ToolSet

BUILTIN: dict[str, str] = {
    "clock": "ai_armory.toolsets.clock",
    "gmail": "ai_armory.toolsets.google.gmail",
    "calendar": "ai_armory.toolsets.google.calendar",
    "chat": "ai_armory.toolsets.google.chat",
    "docs": "ai_armory.toolsets.google.docs",
    "sheets": "ai_armory.toolsets.google.sheets",
    "mac": "ai_armory.toolsets.mac",
}

# Names that stand for several tool sets at once. They aren't tool sets themselves, so serving "all" never loads
# a group's members twice.
GROUPS: dict[str, tuple[str, ...]] = {
    "google": ("gmail", "calendar", "chat", "docs", "sheets"),
}

ENTRY_POINT_GROUP = "ai_armory.toolsets"


def available() -> list[str]:
    """Names of every known tool set, built-in or installed by plugins."""
    names = set(BUILTIN)
    names.update(ep.name for ep in entry_points(group=ENTRY_POINT_GROUP))
    return sorted(names)


def expand(names: list[str]) -> list[str]:
    """The tool set names, with each group replaced by its members, each name once, in order."""
    return list(dict.fromkeys(member for n in names for member in GROUPS.get(n, (n,))))


def load(name: str) -> ToolSet:
    if name in GROUPS:  # one tool set holding every member's tools
        members = load_many([name])
        return ToolSet(name, "; ".join(ts.description for ts in members),
                       [t for ts in members for t in ts.tools])
    if name in BUILTIN:
        toolset = importlib.import_module(BUILTIN[name]).toolset
    else:
        matches = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]
        if not matches:
            raise LookupError(f"unknown tool set {name!r}; available: {', '.join(available())}")
        toolset = matches[0].load()
    if not isinstance(toolset, ToolSet):
        raise TypeError(f"tool set {name!r} did not resolve to a ToolSet")
    return toolset


def load_many(names: list[str]) -> list[ToolSet]:
    return [load(n) for n in expand(names)]
