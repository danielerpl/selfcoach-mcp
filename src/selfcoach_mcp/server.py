"""MCP server: a few high-level tools that return analysis, not raw JSON."""

import os
from datetime import date, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from . import analysis, prompts, sync, workouts
from .auth import AuthError
from .config import ALLOW_WRITE

INSTRUCTIONS = """\
Garmin Connect training companion. You act as the athlete's coach: most users
train without a human coach. Data is synced to a local database and tools return
compact Markdown summaries.

Daily loop: today_status gives a readiness-based suggestion (rest / easy /
moderate / hard) -> agree on today's session -> schedule_workout puts it on the
Garmin calendar so it runs step by step on the watch -> after training,
analyze_session and add_note (RPE, feelings) -> weekly_review on Sunday to plan
the next week. Use set_goal once to record the target event: phase-aware advice
depends on it. Slash commands (MCP prompts) wrap these flows: start, today, plan,
week, log, progress, goal, coach_report. trends shows 4-52 week patterns plus insights Garmin does not
compute (intensity distribution, monotony/strain, ramp rate, rest days).
chart_data returns JSON for visual reports.

Rules: never schedule or remove workouts without the athlete agreeing first. If a
workout comes from a human coach (source "coach", e.g. Garmin Clipboard) never
change it: suggest adjustments for the athlete to discuss with the coach.
Suggestions are training guidance, not medical advice: for pain, illness or
unusual symptoms tell the athlete to see a professional. Laps in motocross are
motos. Reply in the user's language."""

_remote = os.getenv("SELFCOACH_HTTP") == "1"
if _remote:
    from . import remote

    _provider = remote.Provider()
    mcp = MCPServer("selfcoach", instructions=INSTRUCTIONS,
                    auth_server_provider=_provider, auth=remote.auth_settings())
    remote.install(mcp, _provider)
else:
    mcp = MCPServer("selfcoach", instructions=INSTRUCTIONS)
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE_LOCAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
WRITE_GARMIN = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DELETE_GARMIN = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)


def _day(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _run(fn, *args) -> str:
    try:
        aid, warning = sync.ensure_fresh()
        out = fn(aid, *args)
        return f"{warning}\n\n{out}" if warning else out
    except AuthError as e:
        return f"⚠️ {e}"
    except (ValueError, workouts.WorkoutError) as e:
        return f"⚠️ {e}"


@mcp.tool(annotations=READ)
def today_status(day: str | None = None) -> str:
    """Start here every day. Suggests what kind of session suits today (rest / easy /
    moderate / hard) with the reasons, from resting HR vs 4-week baseline, Training
    Readiness, Body Battery, acute/chronic load, HRV, sleep, recent hard sessions and
    days without rest. Also shows the goal and training phase, today's planned
    workout and the last 3 days. `day` is YYYY-MM-DD (default today)."""
    return _run(analysis.today_status, _day(day))


@mcp.tool(annotations=READ)
def plan_vs_done(week_of: str | None = None) -> str:
    """Planned vs executed for the Monday-Sunday week containing `week_of`
    (YYYY-MM-DD, default this week): each planned workout (source self, AI plan or
    coach) marked done/missed/upcoming with duration, HR, Z4+Z5 minutes and sets,
    plus unplanned sessions, adherence % and weekly load. Shows the calendar item id
    needed by remove_workout."""
    return _run(analysis.plan_vs_done, _day(week_of))


@mcp.tool(annotations=READ)
def analyze_session(activity_id: int | None = None, day: str | None = None) -> str:
    """Deep dive into one activity (by id, else the latest on `day`, else the latest
    overall): HR zones, per-lap table with cardiac drift, strength exercises with
    sets/reps/load, comparison with the planned workout, athlete notes and an RPE vs
    heart-rate consistency check."""
    return _run(analysis.analyze_session, activity_id, _day(day) if day else None)


@mcp.tool(annotations=READ)
def trends(weeks: int = 8) -> str:
    """Weekly table for the last `weeks` weeks (sessions, hard sessions, volume,
    load, Z4+Z5, resting HR, stress, Body Battery, readiness, ACWR), sport mix,
    VO2max change and insights: intensity distribution vs 80/20, training
    monotony and strain, ramp rate, rest days, resting HR drift, average RPE.
    Use for fatigue, overreaching, plateaus and progress."""
    return _run(analysis.trends, max(1, min(weeks, 52)))


@mcp.tool(annotations=READ)
def weekly_review(week_of: str | None = None, for_coach: bool = False) -> str:
    """Weekly review for the week containing `week_of`: goal and phase, plan
    adherence, sessions, 4-week trends with insights and athlete notes. Use it to
    plan next week. With for_coach=True it is formatted as a report to send to a
    human coach (includes the athlete's name)."""
    return _run(analysis.weekly_review, _day(week_of), for_coach)


@mcp.tool(annotations=READ)
def list_activities(days: int = 30, sport: str | None = None) -> str:
    """Activities of the last `days` days with id, optionally filtered by sport
    (e.g. 'running', 'strength', 'cycling', 'moto')."""
    return _run(analysis.list_activities, days, sport)


@mcp.tool(annotations=READ)
def chart_data(start: str | None = None, end: str | None = None) -> str:
    """Compact JSON for charts and visual reports: daily metrics (resting HR with
    `rhr_reliable` false when no sleep was recorded, readiness, Body Battery,
    acute/chronic load, stress, VO2max), activities with HR zones and strength sets,
    planned workouts matched to what was done, notes and goals. Defaults to the last
    28 days. Dates are YYYY-MM-DD."""
    e = _day(end)
    s = date.fromisoformat(start) if start else e - timedelta(days=27)
    return _run(analysis.chart_data, s, e)


@mcp.tool(annotations=WRITE_LOCAL)
def add_note(text: str, rpe: int | None = None, day: str | None = None,
             activity_id: int | None = None, tags: str | None = None) -> str:
    """Save a note in the training journal: perceived effort `rpe` (1-10), feelings,
    pain, sleep, conditions. Linked to the day's activity when there is only one.
    Notes appear in analyze_session, trends and weekly_review. Stored locally only."""
    if rpe is not None and not 1 <= rpe <= 10:
        return "RPE must be between 1 and 10."
    return _run(analysis.add_note, text, _day(day), rpe, activity_id, tags)


@mcp.tool(annotations=WRITE_LOCAL)
def set_goal(title: str | None = None, event_date: str | None = None, sport: str | None = None,
             details: str | None = None, clear: bool = False) -> str:
    """Record the athlete's goal so advice follows a training phase (base, build,
    peak, taper, race week). `title` e.g. 'Half marathon under 1h45'; `event_date`
    YYYY-MM-DD if there is a target event; `details` for constraints such as
    available days, hours per week, equipment, injuries. `clear=True` removes
    previous goals (combine with a title to replace them). Stored locally only."""
    ev = date.fromisoformat(event_date) if event_date else None
    return _run(analysis.set_goal, title, ev, sport, details, clear)


@mcp.tool(annotations=WRITE_GARMIN)
def schedule_workout(day: str, name: str, sport: str, steps: list[dict[str, Any]],
                     description: str | None = None, preview: bool = False) -> str:
    """Create a structured workout and put it on the Garmin Connect calendar for
    `day` (YYYY-MM-DD). It syncs to the watch, which then guides each step. Only call
    after the athlete agreed; use preview=True to show it without sending.

    `sport`: running, cycling, strength, cardio, hiit, walking, hiking, yoga,
    mobility, swimming or other (use other/cardio for sports Garmin has no workout
    type for, e.g. motocross).

    `steps` is a list of objects:
    - timed/distance step: {"type": "warmup"|"interval"|"recovery"|"rest"|"cooldown",
      one of "minutes" | "seconds" | "km" | "meters" (omit for 'press lap to continue'),
      optional target: "hr_zone" (1-5) | "hr_bpm" [low, high] | "power_zone" |
      "watts" [low, high] | "pace_per_km" ["5:40", "5:20"] | "cadence" [low, high],
      optional "note"}
    - repeat: {"type": "repeat", "times": 5, "steps": [...]}
    - strength exercise: {"type": "exercise", "name": "Barbell Back Squat",
      "sets": 4, "reps": 8 (or "seconds": 45 for holds), "kg": 60,
      "rest_seconds": 90, "note": "..."}. Names must match Garmin's exercise catalog;
      an unknown name returns the closest matches.

    Example (intervals): [{"type":"warmup","minutes":15,"hr_zone":2},
    {"type":"repeat","times":5,"steps":[{"type":"interval","minutes":3,"hr_zone":4},
    {"type":"recovery","minutes":2,"hr_zone":1}]},{"type":"cooldown","minutes":10}]"""
    if not ALLOW_WRITE and not preview:
        return "Writing to Garmin is disabled (SELFCOACH_ALLOW_WRITE=0). Use preview=True."
    try:
        d = date.fromisoformat(day)
        if preview:
            _, summary = workouts.build(name, sport, steps, description)
            return f"Preview for {d:%a %d/%m} (not sent):\n\n{summary}"
    except (ValueError, workouts.WorkoutError) as e:
        return f"⚠️ {e}"
    return _run(lambda aid: workouts.schedule(name, sport, d, steps, description, aid))


@mcp.tool(annotations=DELETE_GARMIN)
def remove_workout(calendar_item_id: int) -> str:
    """Remove from the Garmin calendar a workout that this server scheduled (ids are
    in plan_vs_done). Workouts from a coach or other apps are never removed. Only
    call after the athlete agreed."""
    if not ALLOW_WRITE:
        return "Writing to Garmin is disabled (SELFCOACH_ALLOW_WRITE=0)."
    return _run(workouts.remove, calendar_item_id)


@mcp.tool()
def sync_garmin(days: int = 30, force: bool = False) -> str:
    """Download the last `days` days from Garmin Connect (activities, calendar,
    daily metrics). Tools already auto-sync recent days; use this for a deeper
    backfill (up to 730 days) or with force=True to refresh days already stored."""
    days = max(1, min(days, 730))
    try:
        r = sync.sync(days, force=force)
    except AuthError as e:
        return f"⚠️ {e}"
    return (f"Sync done ({days} days): {r['activities']} activities, "
            f"{r['planned']} planned workouts, {r['daily']} days of metrics.")


prompts.register(mcp)


def main() -> None:
    mcp.run()


def main_http(host: str, port: int) -> None:
    """Remote mode. Needs SELFCOACH_HTTP=1 set before this module is imported (the CLI does it)."""
    if not _remote:
        raise SystemExit("Remote mode must be started with: selfcoach-mcp serve --http")
    from urllib.parse import urlparse

    from mcp.server.transport_security import TransportSecuritySettings

    from .config import PUBLIC_URL

    # Keep DNS-rebinding protection on, but accept the public hostname (requests arrive
    # through a reverse proxy or tunnel with that Host header) as well as local access.
    public = urlparse(PUBLIC_URL)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[public.netloc, "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[PUBLIC_URL, "https://claude.ai", "https://claude.com",
                         "http://127.0.0.1:*", "http://localhost:*"],
    )
    mcp.run("streamable-http", host=host, port=port, transport_security=security)


if __name__ == "__main__":
    main()
