"""Example tool set: the current date and time."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ai_armory.core import ToolError, ToolSet

toolset = ToolSet("clock", "Current date and time.")


@toolset.tool(
    "now",
    "Get the current date and time, optionally in a given IANA time zone.",
    {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA time zone such as 'Asia/Kolkata'. Defaults to the machine's local zone.",
            }
        },
    },
    read_only=True,
)
async def now(args):
    tz_name = args.get("timezone")
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ToolError(f"Unknown time zone: {tz_name}") from None
        current = datetime.now(tz)
    else:
        current = datetime.now().astimezone()
    return current.strftime("%A %d %B %Y, %H:%M:%S %Z (UTC%z)")
