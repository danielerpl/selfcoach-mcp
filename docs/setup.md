# Setup guide: connecting to Garmin and getting your data

This guide covers every step, from installing to the first question, and explains what happens
behind the scenes so you know exactly what the server does with your account.

- [1. Before you start](#1-before-you-start)
- [2. How the Garmin connection works](#2-how-the-garmin-connection-works)
- [3. Install](#3-install)
- [4. Log in (once)](#4-log-in-once)
- [5. Download your history](#5-download-your-history)
- [6. Connect your MCP client](#6-connect-your-mcp-client)
- [7. First conversation and commands](#7-first-conversation-and-commands)
- [8. What data is downloaded, and how it stays fresh](#8-what-data-is-downloaded-and-how-it-stays-fresh)
- [9. Workouts on your watch](#9-workouts-on-your-watch)
- [10. Updating, moving, uninstalling](#10-updating-moving-uninstalling)
- [11. Troubleshooting](#11-troubleshooting)

---

## 1. Before you start

You need:

| | Notes |
|---|---|
| **A Garmin watch synced to Garmin Connect** | The server reads what the watch uploads to Garmin Connect. It never talks to the watch directly. |
| **A Garmin Connect email + password** | If you sign in with Google or Apple you have no Garmin password yet: on the Garmin sign-in page choose *Forgot password* to create one. Signing in with Google/Apple keeps working afterwards. |
| **[uv](https://docs.astral.sh/uv/getting-started/installation/)** | Python package runner. macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh`. Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"`. It installs the right Python by itself. |
| **An MCP client** | Claude Desktop, Claude Code, or any client that supports local (stdio) MCP servers. |

To get the most out of it, **wear the watch at night**. Sleep, HRV, Training Readiness and a
reliable resting heart rate all come from the night. Without them the daily suggestion relies on
Body Battery and training load only, and says so.

## 2. How the Garmin connection works

Garmin has no public API for individuals. The official
[Garmin Connect Developer Program](https://developer.garmin.com/gc-developer-program/) is for
companies and requires approval. Like every community Garmin tool, this server uses the same
interface as the Garmin Connect website, through the open-source library
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect).

The flow:

```
 you (terminal, once)            your computer                         Garmin
 ───────────────────            ─────────────                         ──────
 selfcoach-mcp login  ── email, password, MFA ──────────────────────────▶ Garmin SSO
                                ◀───────────────────────────────── OAuth tokens
                    ~/.selfcoach/tokens  (password is discarded)

 MCP client starts selfcoach-mcp
   tool call ─▶ local database fresh? ── no ─▶ download recent days ──▶ Garmin Connect API
                      │ yes                     (with tokens, auto-refreshed)
                      ▼
              Markdown summary ─▶ AI model ─▶ answer
```

Key points:

- **The password is used once** to obtain tokens and is never written to disk or logs.
- **Tokens** live in `~/.selfcoach/tokens` (folder `0700`, files `0600`). Treat them like a
  password: anyone with them can read your Garmin account. The library refreshes them
  automatically. You only log in again if Garmin revokes them, for example after a password
  change.
- **Tools never call Garmin for every question.** They read a local database that is refreshed
  in the background (see [section 8](#8-what-data-is-downloaded-and-how-it-stays-fresh)). This
  keeps answers fast and keeps you under Garmin's rate limits.
- If Garmin is unreachable, tools still answer from the local data and tell you it may be stale.

## 3. Install

There are two ways to install. Pick one.

**A. No clone (recommended for users).** `uvx` downloads and runs the server from GitHub. Every
command below starts with:

```bash
uvx --from git+https://github.com/danielerpl/selfcoach-mcp selfcoach-mcp …
```

To save typing, you can define an alias:

```bash
alias selfcoach-mcp='uvx --from git+https://github.com/danielerpl/selfcoach-mcp selfcoach-mcp'
```

**B. From a clone (for development or to pin a version).**

```bash
git clone https://github.com/danielerpl/selfcoach-mcp
cd selfcoach-mcp
uv sync
# then use: uv run selfcoach-mcp …
```

The rest of this guide writes plain `selfcoach-mcp`. Prefix it as above.

## 4. Log in (once)

Run this in a terminal, not inside the AI chat, because it asks for your password:

```bash
selfcoach-mcp login
```

```
Garmin email: you@example.com
Garmin password (hidden):
MFA code (from email or authenticator app): 123456      ← only if MFA is on
Logged in as Jane Doe. Tokens saved in /Users/you/.selfcoach/tokens
Your password was not stored. You can now start the MCP server.
```

Then check that everything is in place:

```bash
selfcoach-mcp check
```

```
Tokens:   /Users/you/.selfcoach/tokens ✅
Database: sqlite:////Users/you/.selfcoach/garmin.db
Garmin:   ✅ connected as Jane Doe (profile 12345678)
Local:    0 activities, 0 days of metrics, last sync never
```

Notes:

- **MFA**: supported. The code arrives by email or from your authenticator app, depending on
  your Garmin settings.
- **Non-interactive login** (servers, containers): set `GARMIN_EMAIL` and `GARMIN_PASSWORD` in
  the environment for the `login` command only, then unset them. MFA still needs a terminal.
- **China accounts** (garmin.cn): set `SELFCOACH_CHINA=1` for both `login` and the server.
- **Error 429 / "rate limiting"**: Garmin blocks logins that come too fast. Wait 15–30 minutes
  and do not retry in between, because every attempt extends the block.

## 5. Download your history

The first time a tool runs, it downloads the last 60 days by itself. That can take a couple of
minutes, and some MCP clients time out a tool call that slow. It is better to do the first
download from the terminal:

```bash
selfcoach-mcp sync --days 180     # up to 730 days
```

Expect roughly **1–2 seconds per day**. Each day needs about five calls (summary, sleep, HRV,
readiness, training status), and the server pauses between calls on purpose to stay below
Garmin's limits. 180 days takes about 4–6 minutes. You only do this once.

Trends and the training-load baseline get better with more history. Four weeks is the minimum
for meaningful comparisons, and 3–6 months is ideal.

## 6. Connect your MCP client

The client starts `selfcoach-mcp` itself, with no arguments, and talks to it over stdio. You don't
run the server by hand.

### Claude Code

```bash
# no clone
claude mcp add selfcoach -s user -- uvx --from git+https://github.com/danielerpl/selfcoach-mcp selfcoach-mcp
# from a clone
claude mcp add selfcoach -s user -- uv --directory /path/to/selfcoach-mcp run selfcoach-mcp
```

Check it with `claude mcp list`. The server should appear as connected. Then open a **new**
session: servers are loaded when a session starts, so a session that was already open (or one
resumed with `--continue`) won't see it. Inside the session, `/mcp` → *selfcoach* → *View tools*
lists the tools.

### Claude Desktop

Open *Settings → Developer → Edit Config*. That opens `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add:

```json
{
  "mcpServers": {
    "selfcoach": {
      "command": "/Users/you/.local/bin/uvx",
      "args": ["--from", "git+https://github.com/danielerpl/selfcoach-mcp", "selfcoach-mcp"],
      "env": { "SELFCOACH_ALLOW_WRITE": "1" }
    }
  }
}
```

Use the **full path** to `uvx` (`which uvx` on macOS/Linux, `where uvx` on Windows), because
Claude Desktop doesn't see your shell's `PATH`. From a clone, use `"command": "/full/path/to/uv"`
and `"args": ["--directory", "/path/to/selfcoach-mcp", "run", "selfcoach-mcp"]`.

Fully quit and reopen Claude Desktop. The tools appear under the 🔌/tools icon, and the commands
in the **+** menu under *selfcoach*.

### Other clients (Cursor, VS Code, Windsurf, …)

Any client that runs local MCP servers works. Configure a stdio server with command `uvx` and
args `--from git+https://github.com/danielerpl/selfcoach-mcp selfcoach-mcp`.

## 7. First conversation and commands

The quickest way in is the **`start`** command. In Claude Code type `/mcp__selfcoach__start`
(type `/selfcoach` and autocomplete does the rest); in Claude Desktop pick it from the **+** menu.
It checks what data you have, asks for your goal and explains the routine.

| Command | When |
|---|---|
| `start` | first time |
| `today` | every morning: suggestion + session for today |
| `plan [request] [day]` | one workout on the watch, e.g. `/mcp__selfcoach__plan "5x3' Z4" 2026-03-02` |
| `log [notes]` | after training |
| `week [week_of]` | Sunday: review + next week's plan |
| `progress [weeks]` | every few weeks |
| `goal [description]` | when your goal changes |
| `coach_report [week_of]` | only if you have a coach |

You don't have to use commands: plain questions work too. Try, in your own language:

1. *"Set my goal: 10 km race on 15 March, aiming for under 50 minutes. I can train 4 days a week,
   1 hour max on weekdays."* → `set_goal`
2. *"How am I today? What should I do?"* → `today_status`
3. *"OK, build that session and put it on my watch for tomorrow."* → `schedule_workout`. The
   model should show it to you and ask before sending.
4. After the session: *"Done, RPE 7, legs heavy in the last rep."* → `add_note`, then
   *"How did it go?"* → `analyze_session`
5. Sunday: *"Review my week and plan the next one."* → `weekly_review`

## 8. What data is downloaded, and how it stays fresh

| Data | Garmin source | Used for |
|---|---|---|
| Activities | activity list: duration, HR, HR zones, training load, Training Effect, laps, strength sets | sessions, load, intensity mix |
| Laps / splits | fetched on demand for one activity | lap/moto table, cardiac drift |
| Daily summary | resting HR, stress, Body Battery, steps, intensity minutes | readiness, trends |
| Sleep | duration, sleep score | readiness, reliability of resting HR |
| HRV | overnight average and status | readiness |
| Training Readiness | score, level, recovery time | readiness |
| Training status | acute/chronic load, ACWR, VO2max, load balance | load management |
| Calendar + workouts | planned workouts with their steps and author (you, a coach via Garmin Clipboard, or this server) | plan vs done |

Not every watch produces every metric. For example, Training Readiness and HRV status need
recent models. Missing metrics show as `-` and are left out of the suggestion.

**Refresh policy:**

- Before each tool call, if the last sync is older than 30 minutes
  (`SELFCOACH_AUTO_SYNC_MINUTES`), the last **7 days** are re-downloaded. That's about 35
  calls, a few seconds.
- The last 2 days are always re-read, because Garmin keeps updating them (sleep is processed in
  the morning, activities sync late).
- Older days already stored are not downloaded again, unless you run `sync --force`.
- Your notes and goals live only in the local database. Garmin never sees them.

**Where it's stored:** `~/.selfcoach/garmin.db` (SQLite). It's a regular file, so you can back
it up, copy it to another machine or open it with any SQLite viewer. To use Postgres/Supabase
instead, install with the extra (`uvx --from 'selfcoach-mcp[postgres] @ git+https://github.com/danielerpl/selfcoach-mcp' selfcoach-mcp`)
and set `SELFCOACH_DB_URL=postgresql+psycopg://user:pass@host:5432/db`. The schema is created
automatically.

## 9. Workouts on your watch

`schedule_workout` creates a workout in your Garmin Connect library and places it on your
calendar. On the next watch sync (Bluetooth with the phone app, or Wi-Fi) it appears on the
watch:

- under the calendar / *Training → Workouts*, depending on the model;
- many watches also offer it from the morning report or when you start the matching activity.

It supports warm-up, intervals, recovery, rest, cool-down and repeats. Steps can be time-,
distance- or lap-button-based, with HR zone, HR range, power, pace or cadence targets. Strength
workouts use exercises from Garmin's catalog of 1,500+ exercises, with sets, reps, weight and
rest. Sports without a Garmin workout type (motocross, climbing…) are created as *other* or
*cardio* with time-based steps.

Safeguards:

- The model is instructed to get your agreement before scheduling or removing anything.
  `preview=True` shows the workout without sending it.
- Workouts created here carry *"Planned with selfcoach-mcp"* in the description and are tracked as
  source **AI plan**.
- `remove_workout` only removes workouts created by this server. Workouts from a coach (Garmin
  Clipboard) or other apps are never modified.
- `SELFCOACH_ALLOW_WRITE=0` disables writing entirely.

## 10. Updating, moving, uninstalling

- **Update** (uvx): `uvx --refresh --from git+https://github.com/danielerpl/selfcoach-mcp selfcoach-mcp check`,
  then restart the client. From a clone: `git pull && uv sync`.
- **Another computer**: copy `~/.selfcoach/` (tokens + database), or log in again there.
- **Log out**: `selfcoach-mcp logout` deletes the tokens and keeps the database.
- **Remove everything**: remove the server from your client config and delete `~/.selfcoach/`.
  Workouts already on your Garmin calendar stay there until you delete them in Garmin Connect.

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "No valid Garmin tokens found" | Run `selfcoach-mcp login` in a terminal. If you set `SELFCOACH_HOME`/`SELFCOACH_TOKENS`, the client config needs the same values. |
| "Garmin rejected the email/password" | Typo, or a Google/Apple account without a Garmin password (see [section 1](#1-before-you-start)). Test at connect.garmin.com. |
| HTTP 429 / rate limiting | Too many logins or requests. Wait 15–30 minutes without retrying. Don't run several syncs in parallel. |
| Tool times out on first use | The initial download is running. Run `selfcoach-mcp sync --days 60` from the terminal, then retry. |
| "Could not sync with Garmin, showing cached data" | Garmin is unreachable or tokens expired. Answers use local data; run `selfcoach-mcp check`. |
| Resting HR marked ⚪ unreliable | No sleep recorded that night, so Garmin estimated it during the day. Wear the watch at night. |
| Tools or commands not showing in Claude Code | Open a new session (not `--continue`), then `/mcp`. Check `claude mcp list` shows *selfcoach ✔ Connected*. |
| Not available on claude.ai, the mobile app or in Connectors | Expected: this is a **local** server, it runs on your computer. Only clients on that computer (Claude Code, Claude Desktop, Cursor…) can use it. Each client has its own config: adding it to Claude Code does not add it to Claude Desktop. |
| Server not showing in Claude Desktop | Use the absolute path to `uvx`, check the JSON is valid, fully quit and reopen the app. Logs: `~/Library/Logs/Claude/mcp*.log` (macOS) or `%APPDATA%\Claude\logs` (Windows). |
| Workout not on the watch | Sync the watch with Garmin Connect. Check that the watch model supports structured workouts for that sport. |
| Exercise name rejected | The error lists the closest names in Garmin's catalog. The model retries with one of them. |
| Everything broke after a Garmin change | Update (see [section 10](#10-updating-moving-uninstalling)). Fixes usually land quickly in python-garminconnect. If not, open an issue. |
