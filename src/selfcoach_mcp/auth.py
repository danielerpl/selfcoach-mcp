"""Garmin authentication: one interactive login, then tokens only.

The password is never stored or logged. Only OAuth tokens are written to
TOKEN_DIR (directory 0700, files 0600). The library refreshes them on its own;
a new interactive login is only needed if they are revoked or expire for good.
"""

import getpass
import os
import shutil
import threading

from garminconnect import (
    Garmin,
    GarminConnectTooManyRequestsError,
)

from .config import IS_CN, TOKEN_DIR


class AuthError(RuntimeError):
    """Login error carrying a message the user can act on."""


_client: Garmin | None = None
_lock = threading.Lock()


def _secure_token_dir() -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(TOKEN_DIR.parent, 0o700)
    os.chmod(TOKEN_DIR, 0o700)
    for f in TOKEN_DIR.iterdir():
        os.chmod(f, 0o600)


def explain(err: Exception) -> str:
    """Turn library errors into concrete next steps."""
    msg = str(err)
    if isinstance(err, GarminConnectTooManyRequestsError) or "429" in msg:
        return (
            "Garmin is rate-limiting requests (HTTP 429). Wait 15-30 minutes before "
            "retrying: every attempt in the meantime extends the block."
        )
    if "Invalid Username or Password" in msg:
        return (
            "Garmin rejected the email/password. Check for typos and try logging in at "
            "connect.garmin.com. If you sign in with Google or Apple your account has no "
            "Garmin password: set one with 'Forgot password' and retry."
        )
    if "Username and password are required" in msg or "garth-format" in msg or "token" in msg.lower():
        return (
            "No valid Garmin tokens found. Run the one-time login in a terminal: "
            "`selfcoach-mcp login` (or `uv run selfcoach-mcp login` from a clone)."
        )
    return f"Garmin login failed: {type(err).__name__}: {msg}"


def has_tokens() -> bool:
    return TOKEN_DIR.is_dir() and any(TOKEN_DIR.iterdir())


def get_client() -> Garmin:
    """Shared authenticated client, restored from saved tokens."""
    global _client
    with _lock:
        if _client is None:
            if not has_tokens():
                raise AuthError(explain(RuntimeError("token")))
            g = Garmin(is_cn=IS_CN)
            try:
                g.login(str(TOKEN_DIR))
            except Exception as e:  # noqa: BLE001
                raise AuthError(explain(e)) from e
            _client = g
        return _client


def login_cli() -> None:
    """Interactive login. Reads GARMIN_EMAIL / GARMIN_PASSWORD if set (e.g. in CI)."""
    _secure_token_dir()
    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.getenv("GARMIN_PASSWORD") or getpass.getpass("Garmin password (hidden): ")
    g = Garmin(
        email,
        password,
        is_cn=IS_CN,
        prompt_mfa=lambda: input("MFA code (from email or authenticator app): ").strip(),
    )
    try:
        g.login(str(TOKEN_DIR))
        g.client.dump(str(TOKEN_DIR))
    except Exception as e:  # noqa: BLE001
        raise SystemExit(explain(e)) from None
    _secure_token_dir()
    print(f"Logged in as {g.get_full_name()}. Tokens saved in {TOKEN_DIR}")
    print("Your password was not stored. You can now start the MCP server.")


def logout_cli() -> None:
    """Delete saved tokens (the local database is kept)."""
    if TOKEN_DIR.exists():
        shutil.rmtree(TOKEN_DIR)
        print(f"Tokens deleted from {TOKEN_DIR}.")
    else:
        print("No tokens to delete.")
