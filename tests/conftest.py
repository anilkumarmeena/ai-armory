import pytest

from ai_armory import ToolError, ToolSet


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_google_config(monkeypatch, tmp_path):
    """Tests never read a real Google config file, and start with no settings."""
    from ai_armory.toolsets.google import settings

    monkeypatch.setenv(settings.CONFIG_ENV, str(tmp_path / "no-such-google.toml"))
    monkeypatch.setattr(settings, "_current", None)


@pytest.fixture
def google_accounts(tmp_path, monkeypatch):
    """Two accounts, work (with Chat, the default) and personal, their tokens in a temporary folder, and no drafts."""
    from ai_armory.toolsets.google import drafts
    from ai_armory.toolsets.google.settings import Account, GoogleSettings, configure

    s = GoogleSettings(accounts=(Account("work", "me@example.com", "for work", chat=True),
                                 Account("personal", "me@example.org")),
                       token_dir=tmp_path / "tokens", sign_in_command="sign-in {label}", user_name="Sam")
    configure(s)
    monkeypatch.setattr(drafts, "_DRAFTS", {})
    return s


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
