"""Ready-made commands (MCP prompts). Clients show them as slash commands, e.g. in
Claude Code `/mcp__selfcoach__today`. Each one is a short brief that tells the
model which tools to use and how to coach, so the user does not have to."""

from .config import LANGUAGE


def _lang() -> str:
    if LANGUAGE:
        return f"Reply in {LANGUAGE}."
    return "Reply in the language the athlete writes in (if unclear, the language of their notes)."


CONSENT = ("Never schedule or remove a workout until the athlete has said yes: show it with "
           "schedule_workout(preview=True) first. Never change workouts whose source is 'coach'.")


def register(mcp) -> None:
    @mcp.prompt(title="Start: set up your coach",
                description="First run: checks your data, records your goal and explains the daily routine.")
    def start() -> str:
        return f"""I'm starting to use selfcoach. Onboard me:
1. Call today_status and trends(weeks=8) to see how much data there is and what the watch records
   (sleep, HRV, readiness). Tell me in 3-4 lines what you see and any gaps (e.g. watch not worn at night).
   If there is little history, offer to run sync_garmin(days=180).
2. Ask me, in one message, about my goal: target event and date (if any), sport, current level,
   days per week and time available, equipment, injuries or limits. Then save it with set_goal.
3. Explain the routine in 5 short lines: /today in the morning, /log after training,
   /week on Sunday, /plan for a single session, /progress every few weeks.
{_lang()}"""

    @mcp.prompt(title="Today: what should I do?",
                description="Morning check: readiness, suggested session for today, option to send it to the watch.")
    def today() -> str:
        return f"""Morning check.
1. Call today_status. Lead with the suggestion (rest / easy / moderate / hard) and the main reasons in
   2-3 lines, then the key numbers only.
2. If something is already planned today, say whether it fits today's state; if it is a coach workout,
   only suggest how to adapt it and to talk to the coach.
3. If nothing is planned, propose ONE concrete session that fits the suggestion and my goal phase
   (sport, duration, structure with HR zones or sets/reps). Ask if I want it on my watch.
4. If I say yes, schedule it with schedule_workout. {CONSENT}
Keep it short: this is read on a phone before training. {_lang()}"""

    @mcp.prompt(title="Plan a session",
                description="Builds one structured workout and puts it on your Garmin watch after your OK.")
    def plan(request: str = "", day: str = "") -> str:
        when = f"for {day}" if day else "for the day I mention, or today if I don't"
        what = f'My request: "{request}".' if request else "Ask me what I want to train if it's not clear."
        return f"""Plan a training session {when}. {what}
1. Call today_status for that day (readiness, goal phase, recent load) and adjust intensity if needed,
   explaining why in one line.
2. Build the workout with clear steps: warm-up, main set (repeats with HR zones, pace or power targets; or
   strength exercises with sets, reps, kg and rest), cool-down. Prefer HR zones unless I give paces/watts.
3. Show it with schedule_workout(preview=True) and ask for my OK; then send it. {CONSENT}
{_lang()}"""

    @mcp.prompt(title="Week: review and plan the next one",
                description="Sunday routine: reviews the week and plans next week's sessions on your watch.")
    def week(week_of: str = "") -> str:
        ref = f"the week of {week_of}" if week_of else "this week"
        return f"""Weekly review and planning for {ref}.
1. Call weekly_review. Summarise in max 6 bullets: what went well, what was missed, load and intensity
   balance, warning signs from the insights (ramp, monotony, grey zone, resting HR), notes/RPE.
2. Propose next week's plan as a table (day, session, purpose, duration, intensity) that respects my goal
   phase, my available days/time from the goal details, at least one rest day, no hard days back to back,
   and a load change between -40% (deload) and +10-20% vs this week. Say which sessions are key.
3. Ask for my OK or changes, then schedule the agreed sessions one by one with schedule_workout.
   {CONSENT}
{_lang()}"""

    @mcp.prompt(title="Log: how did training go?",
                description="After training: analyses the session and saves your RPE and feelings.")
    def log(notes: str = "") -> str:
        given = f'What I say about it: "{notes}".' if notes else \
            "Ask me in one line for RPE (1-10) and how it felt, unless I already said it."
        return f"""Log today's training. {given}
1. Call analyze_session for the latest activity. Give 3-5 lines: did it match the plan, time in zones or
   sets done, cardiac drift or anything unusual.
2. Save my feedback with add_note (rpe + text, short tags like legs:heavy, sleep:bad).
3. If my RPE does not match the heart-rate data, or I mention pain, say what to watch tomorrow
   (and to see a professional for pain that persists).
{_lang()}"""

    @mcp.prompt(title="Progress: am I improving?",
                description="Trends over several weeks: fitness, fatigue, consistency and what to change.")
    def progress(weeks: str = "8") -> str:
        return f"""Show my progress over the last {weeks} weeks.
1. Call trends(weeks={weeks}). Read the table and the insights.
2. Answer in plain words: am I getting fitter (VO2max, resting HR, load tolerance), am I consistent,
   is there accumulated fatigue, is the intensity mix right for my goal.
3. Finish with the 2-3 changes that would help most. If a chart would help, use chart_data.
{_lang()}"""

    @mcp.prompt(title="Goal: set or change your goal",
                description="Records your target event and constraints so advice follows the right phase.")
    def goal(description: str = "") -> str:
        given = f'My goal: "{description}".' if description else "Ask me about my goal."
        return f"""Set my training goal. {given}
Make sure you know: target (event or outcome), date if any, sport, days per week and hours available,
equipment, injuries or limits. Ask only for what is missing, in one message. Then call
set_goal(clear=True, ...) with a clear title, the event date and the constraints in `details`, and tell
me which training phase I'm in and what it means for the next weeks. {_lang()}"""

    @mcp.prompt(title="Coach report",
                description="Weekly report ready to send to a human coach.")
    def coach_report(week_of: str = "") -> str:
        ref = f"the week of {week_of}" if week_of else "this week"
        return f"""Prepare my weekly report for my coach for {ref}.
Call weekly_review(for_coach=True{f', week_of="{week_of}"' if week_of else ''}) and turn it into a concise
message I can paste into a chat or email: adherence, key sessions with numbers, recovery signals, my notes,
and 1-2 questions for the coach. No advice that overrides the coach's plan. {_lang()}"""
