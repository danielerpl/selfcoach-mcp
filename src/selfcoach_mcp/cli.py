"""Command line: `selfcoach-mcp` with no arguments starts the MCP server (what MCP
clients run); the subcommands are for the human setting it up."""

import argparse
import sys

USAGE = """\
selfcoach-mcp              start the MCP server (stdio) — this is what your MCP client runs
selfcoach-mcp login        one-time login: email, password, MFA code; saves tokens only
selfcoach-mcp check        verify tokens, Garmin connection and local database
selfcoach-mcp sync [-d N]  download the last N days now (default 60, max 730)
selfcoach-mcp logout       delete saved tokens
"""


def _check() -> int:
    from sqlalchemy import func, select

    from . import db
    from .auth import AuthError, get_client, has_tokens
    from .config import DB_URL, TOKEN_DIR

    print(f"Tokens:   {TOKEN_DIR} {'✅' if has_tokens() else '❌ missing — run: selfcoach-mcp login'}")
    safe_url = DB_URL.split("@")[-1] if "@" in DB_URL else DB_URL
    print(f"Database: {safe_url}")
    try:
        g = get_client()
        print(f"Garmin:   ✅ connected as {g.get_full_name()} (profile {g.profile_id})")
    except AuthError as e:
        print(f"Garmin:   ❌ {e}")
        return 1
    n_act = db.query(select(func.count()).select_from(db.activities))[0]
    n_day = db.query(select(func.count()).select_from(db.daily))[0]
    last = db.get_state(g.profile_id, "last_sync")
    print(f"Local:    {list(n_act.values())[0]} activities, {list(n_day.values())[0]} days of metrics, "
          f"last sync {last or 'never'}")
    return 0


def main() -> None:
    if len(sys.argv) == 1:
        from .server import main as serve
        serve()
        return
    parser = argparse.ArgumentParser(prog="selfcoach-mcp", usage=USAGE)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    sub.add_parser("login")
    sub.add_parser("logout")
    sub.add_parser("check")
    s = sub.add_parser("sync")
    s.add_argument("-d", "--days", type=int, default=60)
    s.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.cmd == "serve":
        from .server import main as serve
        serve()
    elif args.cmd == "login":
        from .auth import login_cli
        login_cli()
    elif args.cmd == "logout":
        from .auth import logout_cli
        logout_cli()
    elif args.cmd == "check":
        sys.exit(_check())
    elif args.cmd == "sync":
        from .auth import AuthError
        from .sync import sync
        try:
            r = sync(max(1, min(args.days, 730)), force=args.force)
        except AuthError as e:
            sys.exit(f"⚠️ {e}")
        print(f"Synced: {r['activities']} activities, {r['planned']} planned workouts, "
              f"{r['daily']} days of metrics.")
    else:
        print(USAGE)
