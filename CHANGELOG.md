# Changelog

## 0.4.0

- Remote mode: `selfcoach-mcp serve --http` serves MCP over HTTPS with built-in single-user OAuth
  (dynamic client registration, PKCE, passphrase login page, rotating refresh tokens stored hashed,
  redirect hosts limited to the Claude apps and localhost, lockout after wrong attempts). Add it to
  claude.ai as a custom connector to use selfcoach from the Claude mobile apps.
- `deploy/`: systemd unit and settings template. New guide: docs/remote.md (server install,
  Cloudflare Tunnel, connecting claude.ai, Claude Code and mobile).

## 0.3.1

- `today_status` accounts for sessions already done today: after a long or hard one it says
  "done for today" instead of suggesting a hard session.

## 0.3.0

- 8 slash commands (MCP prompts): `start`, `today`, `plan`, `log`, `week`, `progress`, `goal`,
  `coach_report`. Each wraps a full coaching routine and asks before writing to Garmin.
- `SELFCOACH_LANGUAGE` to force the commands' reply language.
- Docs: commands, how to see tools in Claude Code, local vs claude.ai/mobile.

## 0.2.0 — first public release

- Daily readiness suggestion (rest / easy / moderate / hard) with reasons in `today_status`.
- `schedule_workout` / `remove_workout`: structured workouts (HR/pace/power targets, repeats,
  strength sets from Garmin's catalog) pushed to the Garmin calendar and watch.
- `set_goal` with phase-aware advice (base, build, peak, taper, race week).
- Insights in `trends`: intensity distribution, monotony/strain, ramp rate, rest days,
  resting-HR drift, average RPE; RPE vs HR check in `analyze_session`.
- `weekly_review` replaces `coach_report` (`for_coach=True` for a report to a coach).
- CLI: `selfcoach-mcp login | check | sync | logout`; cached-data fallback when Garmin is unreachable.
- English output (the model answers in the user's language), MIT license, setup guide, tests.

## 0.1.0

- First private version: coach-aware sync of Garmin Clipboard workouts, 9 tools.
