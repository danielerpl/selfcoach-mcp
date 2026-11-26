"""Paths and settings, all overridable with environment variables."""

import os
from pathlib import Path

HOME = Path(os.getenv("SELFCOACH_HOME", Path.home() / ".selfcoach")).expanduser()
TOKEN_DIR = Path(os.getenv("SELFCOACH_TOKENS", HOME / "tokens")).expanduser()

# Local SQLite by default. For Postgres/Supabase use a SQLAlchemy URL, e.g.
# postgresql+psycopg://user:password@host:5432/postgres
DB_URL = os.getenv("SELFCOACH_DB_URL", f"sqlite:///{HOME / 'garmin.db'}")

# How often tools re-sync recent days automatically, and how far back the first sync goes.
AUTO_SYNC_MINUTES = int(os.getenv("SELFCOACH_AUTO_SYNC_MINUTES", "30"))
INITIAL_SYNC_DAYS = int(os.getenv("SELFCOACH_INITIAL_SYNC_DAYS", "60"))

# Set to 1 for accounts on garmin.cn.
IS_CN = os.getenv("SELFCOACH_CHINA", "0").lower() in ("1", "true", "yes")

# Set to 0 to make the server read-only (no workouts written to Garmin Connect).
ALLOW_WRITE = os.getenv("SELFCOACH_ALLOW_WRITE", "1").lower() not in ("0", "false", "no")

# Language for the slash-command prompts (e.g. "Italian"). Empty = the athlete's language.
LANGUAGE = os.getenv("SELFCOACH_LANGUAGE", "").strip()

# --- Remote mode (selfcoach-mcp serve --http) ---------------------------------
# Public HTTPS address the server is reached at, e.g. https://selfcoach.example.com
PUBLIC_URL = os.getenv("SELFCOACH_PUBLIC_URL", "").rstrip("/")
# Secret asked on the login page when a client (claude.ai, mobile app…) connects.
PASSPHRASE = os.getenv("SELFCOACH_PASSPHRASE", "")
# OAuth redirect hosts allowed besides localhost (Claude apps by default).
OAUTH_REDIRECT_HOSTS = [h.strip().lower() for h in
                        os.getenv("SELFCOACH_OAUTH_REDIRECT_HOSTS", "claude.ai,claude.com").split(",") if h.strip()]
