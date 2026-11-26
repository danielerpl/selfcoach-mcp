# Changelog

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
