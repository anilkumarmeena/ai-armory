"""The Google accounts' sign-ins:

    python -m ai_armory.toolsets.google                     list the configured accounts
    python -m ai_armory.toolsets.google login LABEL [--client-secret PATH]
    python -m ai_armory.toolsets.google check-chat [LABEL]

The accounts themselves are listed in the Google config file (see settings.py).
"""

from __future__ import annotations

import argparse
import sys

from ai_armory.toolsets.google.settings import config_path, current


def show() -> str:
    s = current()
    if not s.accounts:
        return f"No Google accounts yet. List them in {config_path()}, then sign each in with: " \
               f"{s.sign_in('LABEL')}"
    width = max(len(l) for l in s.labels)
    lines = []
    for a in s.accounts:
        flags = ["default"] if a.label == s.default_label else []
        flags += ["Google Chat"] if a.chat else []
        flags.append("signed in" if s.token_path(a.label).exists() else "not signed in")
        note = f"  {a.description}" if a.description else ""
        lines.append(f"  {a.label:<{width}}  {a.email or '(email not given)'}  [{', '.join(flags)}]{note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_armory.toolsets.google",
                                     description="Sign the Google tool sets' accounts in.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("list", help="list the accounts (the default)")
    login = commands.add_parser("login", help="sign an account in, or in again")
    login.add_argument("label")
    login.add_argument("--client-secret", default="", help="the OAuth client JSON's path")
    commands.add_parser("check-chat", help="check Google Chat works").add_argument("label", nargs="?")
    args = parser.parse_args(argv)

    if (args.command or "list") == "list":
        print(show())
        return 0
    from ai_armory.toolsets.google import auth
    from ai_armory.toolsets.google.common import account_email

    s = current()
    if args.command == "check-chat":
        if not (label := args.label or next(iter(s.chat_labels), "")):
            sys.exit("No account has Google Chat. Set chat = true for one in the Google config.")
        return auth.check_chat(label)
    if s.get(args.label) is None:
        sys.exit(f"There's no {args.label} account. The accounts: {', '.join(s.labels) or 'none yet'}.")
    if not (secret := auth.client_secret(args.client_secret)):
        sys.exit("Give the OAuth client JSON's path with --client-secret, client_secret in the Google config, or "
                 "$GOOGLE_CLIENT_SECRET.")
    if code := auth.connect(args.label, secret):
        return code
    try:
        email = account_email(args.label)
    except Exception as e:
        print(f"Couldn't look up the {args.label} account's email: {e}")
        return 0
    known = s.get(args.label).email
    if known and known.lower() != email.lower():
        print(f"The {args.label} account signed in as {email}, but the config says {known}. Check which is right.")
    else:
        print(f"The {args.label} account is {email}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
