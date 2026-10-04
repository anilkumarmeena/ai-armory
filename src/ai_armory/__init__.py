"""AI Armory: MCP tool sets, declared once, served in-process or over stdio."""

from ai_armory.core import Result, Tool, ToolError, ToolSet, all_tools, call
from ai_armory.registry import available, load, load_many

__version__ = "0.1.0"

__all__ = [
    "Result",
    "Tool",
    "ToolError",
    "ToolSet",
    "all_tools",
    "available",
    "call",
    "load",
    "load_many",
]
