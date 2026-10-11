"""The page a host shows when Google redirects a browser sign-in back to it (auth.begin / auth.complete).

It says how the sign-in went (checks.Outcome), lists each Google API with its check, with a button to turn on any
that's off, and anything else worth knowing. Everything shown is escaped.
"""

from __future__ import annotations

import html
from pathlib import Path
from string import Template

from ai_armory.toolsets.google.checks import Outcome

PAGE = Template(Path(__file__).with_suffix(".html").read_text())
ICONS = {"ok": '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
         "warn": '<svg viewBox="0 0 24 24"><path d="M12 7v6M12 17h.01"/></svg>',
         "bad": '<svg viewBox="0 0 24 24"><path d="M7 7l10 10M17 7L7 17"/></svg>'}
TITLES = {"ok": "Signed in", "warn": "Signed in, almost ready", "bad": "Not signed in"}
STATES = {"ok": "Ready", "denied": "Not allowed", "error": "Didn't answer"}


def render(o: Outcome, app: str = "AI Armory", brand: str = "", where: str = "") -> str:
    """The page for `o`. `app` names the host ("Back to JARVIS"), `brand` heads the page (default `app`), and `where`
    is where to check again or start over (default `app`). Its back button goes to the host's "/"."""
    e, where = html.escape, where or app
    fixes = [c for c in o.checks if c.state in ("off", "error")]
    tone = "bad" if o.state != "done" else "warn" if fixes else "ok"
    rows = []
    for c in o.checks:
        right = (f'<a class="enable" href="{e(c.fix)}" target="_blank" rel="noopener">Turn on</a>' if c.state == "off"
                 else f'<span class="st">{STATES[c.state]}</span>')
        why = c.why or ("Off in your Google Cloud project" if c.state == "off" else "")
        rows.append(f'<li class="c-{c.state}"><span class="dot"></span><span class="name">{e(c.name)}'
                    f'{f"<small>{e(why)}</small>" if why else ""}</span>{right}</li>')
    who = ""
    if o.label:
        who = f'<p class="who"><b>{e(o.label)}</b>' + (f" · <code>{e(o.email)}</code>" if o.email else "") + "</p>"
    return PAGE.substitute(
        tone=tone, icon=ICONS[tone], title=TITLES[tone], app=e(app), brand=e(brand or app), who=who,
        said="" if o.checks else f'<p class="said">{e(o.said)}</p>',
        checks=f'<ul class="checks">{"".join(rows)}</ul>' if rows else "",
        hint=f'<p class="hint">Turn each one on in Google Cloud and give it a minute, then check again in {e(where)}. '
             "No need to sign in again.</p>" if any(c.state == "off" for c in o.checks) else "",
        notes=f'<ul class="notes">{"".join(f"<li>{e(n)}</li>" for n in o.notes)}</ul>' if o.notes else "",
        foot="You can close this tab." if o.state == "done" else
        f"Start the sign-in again from {e(where)}." if o.label else "")
