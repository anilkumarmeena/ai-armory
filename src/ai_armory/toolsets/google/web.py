"""Signing accounts in from a host's own web page, with nothing web-framework specific: the host serves one callback
path and hands its query to `WebSignIns.callback`.

    signins = WebSignIns(load_client_json, "/google/callback", port=8787, app="My host")
    started = signins.start("work", 0, origin="https://my-host.example")  # send the browser to started["url"]
    html = await signins.callback(request.query)  # at the callback path
    found = await signins.wait(started["state"])  # the page asks how it went

Where Google sends a sign-in back depends on the OAuth client:

  Desktop app client  http://127.0.0.1:<port><callback>. On the host's computer that's the host itself; on another
                      device (a phone) the page doesn't load, so its address is pasted back (`finish_pasted`).
  Web app client      the page's own origin + callback, so any device finishes by itself. That address must be one of
                      the client's redirect URIs; `setup` lists the ones it lacks.

Each sign-in has an unguessable `state`, used once and kept `minutes`, and PKCE (auth.begin), so a stray or replayed
callback does nothing. Begun sign-ins live in memory: run one process serving both the page and the callback.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs, urlparse

from ai_armory.toolsets.google import auth, checks
from ai_armory.toolsets.google.checks import Outcome
from ai_armory.toolsets.google.signin_page import render

LOCAL_HOSTS = ("127.0.0.1", "localhost")
OLD = "That sign-in link is old or was already used. Start the sign-in again."
NO_CLIENT = "There's no Google OAuth client yet: create one in Google Cloud (APIs & Services → Credentials) first."
NOT_AN_ADDRESS = ("That isn't the address Google sent you to: copy the whole address of the page that didn't load, "
                  "starting with http://127.0.0.1.")


@dataclass
class Pending:
    begun: auth.WebSignIn
    started: float = field(default_factory=time.time)
    back: asyncio.Event = field(default_factory=asyncio.Event)  # set when Google's answer arrives
    taken: bool = False  # Google's answer arrived: any other is old
    result: Outcome | None = None  # once it's handled


@dataclass
class Route:
    """How a sign-in started from the page's origin comes back."""
    kind: str  # "installed" (Desktop app), "web", or "" with no client
    redirect: str  # the redirect URI to ask Google for
    paste: bool  # it ends on a page that doesn't load, whose address is pasted back
    missing: list[str]  # redirect URIs a Web client lacks


def origin_of(origin: str) -> str:
    """The page's origin, checked: https anywhere, or http on this computer. Raises ValueError."""
    url = urlparse(origin.strip())
    secure = url.scheme == "https" or (url.scheme == "http" and url.hostname in LOCAL_HOSTS)
    if not (secure and url.hostname) or url.path not in ("", "/") or url.query or url.username:
        raise ValueError("Open the page from its usual address to sign in.")
    return f"{url.scheme}://{url.netloc}"


class WebSignIns:
    """The sign-ins a host's page has begun, until Google sends each back.

    `client` returns the OAuth client's JSON, or {} when there's none. `on_done` runs (in a thread) on each sign-in's
    outcome before the page hears it, e.g. to store the email it chose or add notes. `app`, `brand` and `where` name
    the host on the callback page (signin_page.render)."""

    def __init__(self, client: Callable[[], dict], callback: str, port: int, *, minutes: int = 15,
                 wait_seconds: float = 25, on_done: Callable[[Outcome], None] | None = None,
                 no_client: str = NO_CLIENT, app: str = "AI Armory", brand: str = "", where: str = ""):
        self._client, self.callback_path, self.port = client, callback, port
        self.minutes, self.wait_seconds, self.on_done, self.no_client = minutes, wait_seconds, on_done, no_client
        self._page = {"app": app, "brand": brand, "where": where}
        self._pending: dict[str, Pending] = {}  # by state, until `minutes` after it began

    @property
    def local_redirect(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.callback_path}"

    def client(self) -> dict:
        """The OAuth client's JSON. Raises ValueError when there's none."""
        if not (found := self._client()):
            raise ValueError(self.no_client)
        return found

    def _purge(self) -> None:
        now = time.time()
        for key in [k for k, p in self._pending.items() if now - p.started > self.minutes * 60]:
            del self._pending[key]

    def _wanted(self, origin: str) -> list[str]:
        """The redirect URIs a Web client needs: the page's own, and localhost's."""
        return list(dict.fromkeys([origin_of(origin) + self.callback_path,
                                   f"http://localhost:{self.port}{self.callback_path}"]))

    def route(self, found: dict, origin: str) -> Route:
        """The one place a client's kind decides where Google sends a sign-in back."""
        here = origin_of(origin) + self.callback_path
        if not found:
            return Route("", "", False, [])
        if "web" not in found:
            return Route("installed", self.local_redirect, urlparse(here).hostname not in LOCAL_HOSTS, [])
        listed = found["web"].get("redirect_uris") or []
        return Route("web", here, False, [u for u in self._wanted(origin) if u not in listed])

    def setup(self, origin: str) -> dict:
        """What the page shows about the client: its kind ("" for none), the APIs to turn on, the redirect URIs a Web
        client needs and lacks, and whether a sign-in started from `origin` ends with a pasted address."""
        route = self.route(found := self._client() or {}, origin)
        return {"kind": route.kind, "apis": checks.apis(found), "redirects": self._wanted(origin),
                "missing": route.missing, "paste": route.paste}

    def start(self, label: str, step: int, origin: str, email: str = "") -> dict:
        """Begin a sign-in for `label` (configured first): {"url" (Google's consent page), "state", "paste" (finish by
        pasting the address it ends on), "next" (the next scope step's words, offered when a Workspace refuses this
        one, or "")}. `email` picks the account on Google's page."""
        route = self.route(found := self.client(), origin)
        url, state, begun = auth.begin(label, step, found, route.redirect, email)
        self._purge()
        self._pending[state] = Pending(begun)
        return {"url": url, "state": state, "paste": route.paste, "next": auth.next_step(label, step)}

    def status(self, state: str) -> dict:
        """{"state": "waiting"} while the sign-in hasn't come back, then its outcome; "old" once it's gone."""
        self._purge()
        if not (p := self._pending.get(state)):
            return {"state": "old", "said": OLD}
        return asdict(p.result) if p.result else {"state": "waiting"}

    async def wait(self, state: str) -> dict:
        """`status`, once the sign-in comes back or after `wait_seconds`, so the page asks about twice a minute."""
        if (p := self._pending.get(state)) and not p.result:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(p.back.wait(), self.wait_seconds)
        return self.status(state)

    async def finish(self, state: str, code: str, error: str = "") -> Outcome:
        """Complete a sign-in from Google's answer (code, or an error). An unknown or used state changes nothing."""
        self._purge()
        if not (p := self._pending.get(state)) or p.taken:
            return Outcome("", "failed", OLD)
        p.taken = True
        try:
            result = await asyncio.to_thread(auth.finish, p.begun, code, error)
            if self.on_done:
                await asyncio.to_thread(self.on_done, result)
        except Exception as e:  # never leave the page waiting
            result = Outcome(p.begun.label, "failed", f"The sign-in didn't finish ({type(e).__name__}: {e}).")
        p.result = result
        p.back.set()
        return result

    async def finish_pasted(self, address: str) -> None:
        """Complete a sign-in from the address Google's redirect ended on, pasted from another device's browser; the
        page hears how it went from `wait`. Raises ValueError for an address that isn't one, or a sign-in that's gone."""
        query = parse_qs(urlparse(address.strip()).query)
        state, code, error = (query.get(k, [""])[0] for k in ("state", "code", "error"))
        if not state:
            raise ValueError(NOT_AN_ADDRESS)
        if (await self.finish(state, code, error)).said == OLD:
            raise ValueError(OLD)

    async def callback(self, query: Mapping[str, str]) -> str:
        """The page for Google's redirect to the callback path, given its query: finishes the sign-in it names."""
        return self.page(await self.finish(query.get("state", ""), query.get("code", ""), query.get("error", "")))

    def page(self, o: Outcome) -> str:
        """The callback page for an outcome."""
        return render(o, **self._page)
