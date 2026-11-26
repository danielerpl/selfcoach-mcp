# selfcoach-mcp — self-coaching for Garmin athletes

**An AI coach on top of your Garmin data.** An [MCP](https://modelcontextprotocol.io) server that
lets Claude (or any MCP client) read your Garmin Connect data, tell you what kind of session
suits today, **put structured workouts on your watch**, and review your week with insights
Garmin doesn't compute.

It is built for people who **train without a coach** and want a daily routine with their
assistant. If you do have a coach who plans in Garmin Connect, it works with that too and never
touches their workouts.

> ⚠️ Unofficial project, not affiliated with or endorsed by Garmin. It uses the same private
> API as the Garmin Connect website (through
> [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)), which Garmin
> can change at any time. Use at your own risk, and see [Disclaimer](#disclaimer).

## What it does that other Garmin MCPs don't

| | Typical Garmin MCP | selfcoach-mcp |
|---|---|---|
| Tools | 50–100+ raw endpoints | 12 high-level tools + 8 ready-made slash commands (`/today`, `/week`, `/log`…) |
| Output | Raw Garmin JSON | Compact Markdown with the numbers that matter |
| "Should I train hard today?" | You interpret the data | Rest / easy / moderate / hard suggestion, with the reasons |
| Workouts | Read only | **Builds structured workouts and schedules them on your watch** (intervals with HR/pace/power targets, strength sets with reps and weight) |
| Insights | Garmin's metrics only | Intensity distribution vs 80/20, training monotony and strain, weekly ramp rate, rest days, resting-HR drift, RPE vs heart-rate checks |
| Goals | None | Target event, with phase-aware advice (base, build, peak, taper, race week) |
| Journal | None | Notes with perceived effort (RPE) linked to activities |
| Speed and reliability | Calls Garmin on every question | Local database with incremental sync: fast, works when Garmin is down, fewer rate limits |
| Sports | Running-centric | Any sport: strength with sets and loads, laps as motos in motocross, cycling, etc. |

## How you use it

A daily loop. Use the slash commands, or just ask in plain language:

1. **Morning:** `today` or "How am I today?" → suggests the type of session and why.
2. **Plan:** `plan` or "Give me 5×3' in Z4 for tonight" → the workout goes on your Garmin
   calendar and the watch guides each step.
3. **After training:** `log` or "Felt heavy, RPE 8" → analyses the session and saves your feedback.
4. **Sunday:** `week` or "Review my week and plan the next one" → review, then next week's
   sessions go on the watch after your OK.

Start with `start`: it checks your data and records your goal ("half marathon on 12 April, 4 days
a week, max 1h on weekdays"), so the advice follows your training phase.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), a Garmin Connect account
with an email and password, and an MCP client such as Claude Desktop or Claude Code.

```bash
# 1. Log in once: email, password and MFA code if enabled. Only tokens are saved, never the password.
uvx --from git+https://github.com/danielerpl/selfcoach-mcp selfcoach-mcp login

# 2. First download: 6 months of history, takes a few minutes.
uvx --from git+https://github.com/danielerpl/selfcoach-mcp selfcoach-mcp sync --days 180

# 3. Connect it to Claude Code…
claude mcp add selfcoach -s user -- uvx --from git+https://github.com/danielerpl/selfcoach-mcp selfcoach-mcp
```

…or to **Claude Desktop** (Settings → Developer → Edit Config), using the full path that
`which uvx` prints:

```json
{
  "mcpServers": {
    "selfcoach": {
      "command": "/Users/you/.local/bin/uvx",
      "args": ["--from", "git+https://github.com/danielerpl/selfcoach-mcp", "selfcoach-mcp"]
    }
  }
}
```

Open a **new** session (servers are loaded at start-up), type `/` and run the `start` command,
or just ask: *"How am I today?"*

**The [setup guide](docs/setup.md) explains everything in detail.** It covers how the Garmin
connection works, MFA, Google/Apple accounts, what data is downloaded and where it goes, other
clients, running from a clone, and troubleshooting.

## Commands

Ready-made routines, shipped as MCP prompts. Your client lists them as slash commands: in
**Claude Code** type `/` and look for `/mcp__selfcoach__today` etc.; in **Claude Desktop** they
are in the **+** menu under *selfcoach*. Arguments are optional.

| Command | When | What it does |
|---|---|---|
| `start` | first time | Checks your data, asks for your goal and constraints, explains the routine |
| `today` | every morning | Readiness suggestion + one session that fits it, sent to the watch if you agree |
| `plan [request] [day]` | any time | Builds one structured workout, shows a preview, schedules it after your OK |
| `log [notes]` | after training | Analyses the latest session and saves RPE and feelings |
| `week [week_of]` | Sunday | Reviews the week and plans the next one, day by day, on your watch |
| `progress [weeks]` | every few weeks | Are you improving? Fitness, fatigue, consistency, what to change |
| `goal [description]` | when it changes | Sets the target event and constraints → training phase |
| `coach_report [week_of]` | if you have a coach | Weekly report ready to paste into a message |

Commands reply in your language. To force one, set `SELFCOACH_LANGUAGE` (e.g. `Italian`).

## Tools

The building blocks the commands (and free-form questions) use:

| Tool | What it's for |
|---|---|
| `today_status` | Daily start: readiness-based suggestion (rest/easy/moderate/hard) with the reasons, goal phase, today's plan |
| `schedule_workout` | Build a structured workout and send it to the Garmin calendar and watch (`preview=True` to check first) |
| `remove_workout` | Remove a workout this server scheduled (coach workouts are protected) |
| `plan_vs_done` | The week: planned vs executed, adherence, load, Z4+Z5, unplanned sessions |
| `analyze_session` | One session: HR zones, laps with cardiac drift, strength sets, comparison with the plan, RPE check |
| `trends` | 1–52 week table plus insights (intensity mix, monotony/strain, ramp, rest days, resting-HR drift) |
| `weekly_review` | Weekly review to plan the next week; `for_coach=True` formats it as a report for a coach |
| `set_goal` | Target event, constraints (days, hours, equipment, injuries) → phase-aware advice |
| `add_note` | Journal: RPE 1–10, feelings, pain, conditions |
| `list_activities` | Recent activities with ids, filterable by sport |
| `chart_data` | Compact JSON for charts and visual reports |
| `sync_garmin` | Backfill up to 2 years or force a refresh |

The suggestions are rules of thumb built from your watch's data. They are training guidance,
**not medical advice**.

## Configuration

All optional, set as environment variables (in Claude Desktop add an `"env": {…}` block next to
`"args"`).

| Variable | Default | Meaning |
|---|---|---|
| `SELFCOACH_HOME` | `~/.selfcoach` | Where tokens and the database live |
| `SELFCOACH_TOKENS` | `$SELFCOACH_HOME/tokens` | Token directory |
| `SELFCOACH_DB_URL` | `sqlite:///$SELFCOACH_HOME/garmin.db` | Any SQLAlchemy URL; Postgres needs the `postgres` extra |
| `SELFCOACH_AUTO_SYNC_MINUTES` | `30` | Re-sync recent days when data is older than this |
| `SELFCOACH_INITIAL_SYNC_DAYS` | `60` | History downloaded on first use if you skipped `sync` |
| `SELFCOACH_ALLOW_WRITE` | `1` | `0` = read-only: never write to Garmin Connect |
| `SELFCOACH_CHINA` | `0` | `1` for accounts on garmin.cn |
| `SELFCOACH_LANGUAGE` | *(empty)* | Language for the slash commands, e.g. `Italian`; empty = the language you write in |

## Privacy

- Your Garmin **password is never stored**. Only OAuth tokens are kept, in
  `~/.selfcoach/tokens` with `0700`/`0600` permissions.
- Data is stored **only on your machine**, in a SQLite file. The server talks to nobody but Garmin.
- Whatever a tool returns is read by the AI model of your MCP client (e.g. Anthropic for Claude),
  like anything else you paste into a chat.
- The server writes to your Garmin account only through `schedule_workout` and `remove_workout`,
  and the system prompt asks the model to get your consent first. Set `SELFCOACH_ALLOW_WRITE=0`
  to rule it out entirely.

## Development

```bash
git clone https://github.com/danielerpl/selfcoach-mcp && cd selfcoach-mcp
uv sync
uv run pytest            # tests use synthetic data, no Garmin account needed
uv run selfcoach-mcp check  # with your own account
```

Code map: `sync.py` (Garmin → database), `analysis.py` (all Markdown summaries and insights),
`workouts.py` (structured workout builder), `server.py` (MCP tools), `cli.py` (commands).

Contributions are welcome, especially insights for more sports and testing on more watch models.

## Disclaimer

This project is not affiliated with, endorsed by or connected to Garmin Ltd. "Garmin" and
"Garmin Connect" are trademarks of Garmin Ltd. or its subsidiaries. The software uses an
unofficial interface that may stop working, and automated access may conflict with Garmin's
terms of use. Excessive requests can get your account temporarily rate-limited. Training
suggestions are generated from heuristics and are not medical advice. Consult a professional
for pain, illness or before starting a new training programme.

## License

[MIT](LICENSE)
