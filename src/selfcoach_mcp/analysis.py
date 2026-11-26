"""Analysis on synced data. Every function returns compact Markdown: a few lines
with the numbers that matter, not Garmin's raw JSON. The model translates and
explains it in the user's language."""

import json
from datetime import date, timedelta
from statistics import mean, pstdev

from sqlalchemy import select, update

from . import db
from .auth import get_client

COACH_PROVIDER = "Garmin Clipboard"
SELF_PROVIDER = "selfcoach-mcp"

SPORT_NAMES = {
    "strength_training": "Strength", "indoor_cycling": "Indoor cycling", "cycling": "Cycling",
    "road_biking": "Road cycling", "mountain_biking": "MTB", "indoor_rowing": "Rowing",
    "hiit": "HIIT", "running": "Running", "treadmill_running": "Treadmill", "trail_running": "Trail run",
    "walking": "Walking", "hiking": "Hiking", "multi_sport": "Multisport", "indoor_cardio": "Cardio",
    "lap_swimming": "Pool swim", "open_water_swimming": "Open water", "yoga": "Yoga",
    "motocross": "Motocross", "motorcycling": "Motorcycling",
}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# --- Helpers ----------------------------------------------------------------

def sport(key: str | None) -> str:
    if not key:
        return "?"
    if "moto" in key:
        return "Motocross" if "cross" in key else SPORT_NAMES.get(key, "Motorcycling")
    return SPORT_NAMES.get(key, key.replace("_", " ").capitalize())


def dd(d: date, month: bool = False) -> str:
    """'Mon 12' or 'Mon 12/10'."""
    return f"{DAYS[d.weekday()]} {d:%d/%m}" if month else f"{DAYS[d.weekday()]} {d:%d}"


def hm(seconds: float | None) -> str:
    if not seconds:
        return "-"
    m = round(seconds / 60)
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m}'"


def num(v, digits: int = 0) -> str:
    if v is None:
        return "-"
    return f"{v:.{digits}f}" if digits else str(round(v))


def week_bounds(ref: date) -> tuple[date, date]:
    start = ref - timedelta(days=ref.weekday())
    return start, start + timedelta(days=6)


def source(provider: str | None) -> str:
    if provider == COACH_PROVIDER:
        return "coach"
    if provider == SELF_PROVIDER:
        return "AI plan"
    return "self"


def _avg(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return mean(vals) if vals else None


def _daily(aid: int, start: date, end: date) -> list[dict]:
    t = db.daily
    return db.query(select(t).where(t.c.athlete_id == aid, t.c.date >= start, t.c.date <= end)
                    .order_by(t.c.date))


def _activities(aid: int, start: date, end: date) -> list[dict]:
    t = db.activities
    return db.query(select(t).where(t.c.athlete_id == aid, t.c.date >= start, t.c.date <= end)
                    .order_by(t.c.start_local))


def _planned(aid: int, start: date, end: date) -> list[dict]:
    p, w = db.planned, db.workouts
    stmt = (select(p, w.c.provider, w.c.steps, w.c.name.label("workout_name"))
            .select_from(p.outerjoin(w, p.c.workout_id == w.c.workout_id))
            .where(p.c.athlete_id == aid, p.c.date >= start, p.c.date <= end)
            .order_by(p.c.date))
    return db.query(stmt)


def _notes(aid: int, start: date, end: date) -> list[dict]:
    t = db.notes
    return db.query(select(t).where(t.c.athlete_id == aid, t.c.date >= start, t.c.date <= end)
                    .order_by(t.c.date))


def _goals(aid: int) -> list[dict]:
    t = db.goals
    return db.query(select(t).where(t.c.athlete_id == aid, t.c.active.is_(True)).order_by(t.c.event_date))


def _planned_sets(steps: list[dict] | None) -> int:
    return sum(s["sets"] for s in steps or [] if s.get("kind") == "interval")


def _z45_min(a: dict) -> float:
    return ((a.get("hr_z4_s") or 0) + (a.get("hr_z5_s") or 0)) / 60


def is_hard(a: dict) -> bool:
    """A session that needs recovery: lots of Z4-5 or a high training effect."""
    return (_z45_min(a) >= 10 or (a.get("aerobic_te") or 0) >= 4.0
            or (a.get("anaerobic_te") or 0) >= 3.0)


# --- Plan vs done -------------------------------------------------------------

def match_plan(planned: list[dict], acts: list[dict]) -> tuple[list[tuple[dict, dict | None]], list[dict]]:
    """Pair each planned workout with the activity that executed it.

    Priority: same workout_id (the watch ran exactly that workout) within ±1 day,
    then same day with the same title.
    """
    free = list(acts)
    pairs = []
    for p in planned:
        hit = next((a for a in free if a.get("workout_id") and a["workout_id"] == p["workout_id"]
                    and abs((a["date"] - p["date"]).days) <= 1), None)
        if hit is None:
            title = (p["title"] or "").casefold().replace(" ", "")
            hit = next((a for a in free if a["date"] == p["date"]
                        and (a["name"] or "").casefold().replace(" ", "") == title), None)
        if hit:
            free.remove(hit)
        pairs.append((p, hit))
    return pairs, free


def plan_vs_done(aid: int, ref: date) -> str:
    ws, we = week_bounds(ref)
    today = date.today()
    planned = _planned(aid, ws, we)
    acts = _activities(aid, ws, we)
    pairs, extra = match_plan(planned, acts)

    lines = [f"## Week {ws:%d/%m} – {we:%d/%m}", ""]
    due = done = 0
    if pairs:
        lines += ["| Day | Planned | Source | Status | Done | Duration | Avg HR | Z4+Z5 | Sets | Item id |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for p, a in pairs:
            if a:
                state = "✅ done"
            elif p["date"] < today:
                state = "❌ missed"
            elif p["date"] == today:
                state = "⏳ today"
            else:
                state = "📅 upcoming"
            if p["date"] <= today:
                due += 1
                done += bool(a)
            sets = "-"
            if a and p.get("steps") and p["sport"] == "strength_training":
                sets = f"{(a['raw'] or {}).get('totalSets') or '-'}/{_planned_sets(p['steps'])}"
            lines.append(
                f"| {dd(p['date'])} | {p['title']} | {source(p.get('provider'))} | {state} | "
                f"{(a['name'] if a else '-')} | {hm(a['duration_s']) if a else '-'} | "
                f"{num(a['avg_hr']) if a else '-'} | {f'{_z45_min(a):.0f}' + chr(39) if a else '-'} | "
                f"{sets} | {p['calendar_item_id']} |")
    else:
        lines.append("No planned workouts this week (use schedule_workout to plan some).")

    if extra:
        lines += ["", "**Unplanned sessions:**" if pairs else "**Sessions:**"]
        lines += [f"- {dd(a['date'])} {sport(a['type_key'])}: {a['name']} · {hm(a['duration_s'])} · "
                  f"HR {num(a['avg_hr'])} · load {num(a['training_load'])}"
                  f"{' · hard' if is_hard(a) else ''}" for a in extra]

    tot_load = sum(a["training_load"] or 0 for a in acts)
    tot_time = sum(a["duration_s"] or 0 for a in acts)
    compliance = f"{done}/{due} ({round(100 * done / due)}%)" if due else "n/a"
    lines += ["", f"**Plan adherence:** {compliance} · **Volume:** {hm(tot_time)} in "
                  f"{len(acts)} sessions ({sum(is_hard(a) for a in acts)} hard) · "
                  f"**Total load:** {num(tot_load)} · **Z4+Z5:** {sum(_z45_min(a) for a in acts):.0f}'"]
    return "\n".join(lines)


# --- Goals ----------------------------------------------------------------------

def phase(event: date | None, day: date) -> str | None:
    if not event:
        return None
    days = (event - day).days
    if days < 0:
        return "event passed"
    if days <= 7:
        return "race week: keep it short and sharp, arrive fresh"
    if days <= 14:
        return "taper: cut volume 30-50%, keep some intensity"
    if days <= 28:
        return "peak: race-specific work, volume stable"
    if days <= 84:
        return "build: progressive overload, 1-2 key sessions a week"
    return "base: aerobic volume and strength, little high intensity"


def goal_lines(aid: int, day: date) -> list[str]:
    out = []
    for g in _goals(aid):
        when = ""
        if g["event_date"]:
            weeks = (g["event_date"] - day).days / 7
            when = f" — {g['event_date']:%d/%m/%Y}, in {weeks:.0f} weeks · phase: {phase(g['event_date'], day)}"
        sport_txt = f" ({g['sport']})" if g["sport"] else ""
        details = f" · {g['details']}" if g["details"] else ""
        out.append(f"🎯 {g['title']}{sport_txt}{when}{details}")
    return out


def set_goal(aid: int, title: str | None, event_date: date | None, sport_name: str | None,
             details: str | None, clear: bool) -> str:
    if clear:
        db.execute(update(db.goals).where(db.goals.c.athlete_id == aid).values(active=False))
        if not title:
            return "All goals cleared."
    if not title:
        return "Give the goal a title (e.g. 'Half marathon under 1h45')."
    db.insert_row(db.goals, {"athlete_id": aid, "title": title, "sport": sport_name,
                             "event_date": event_date, "details": details, "active": True,
                             "created_at": db.now_utc()})
    return "Goal saved.\n" + "\n".join(goal_lines(aid, date.today()))


# --- Today -------------------------------------------------------------------------

def readiness_verdict(today: dict, base: list[dict], recent: list[dict], day: date) -> tuple[str, list[str]]:
    """Combine recovery and load signals into a suggestion for today.

    Rules of thumb, not medical advice: two strong warnings mean rest, one
    strong warning or several mild ones mean easy, a couple of mild ones mean
    moderate, otherwise hard training is fine.
    """
    red: list[str] = []
    amber: list[str] = []
    rhr_base = _avg(base, "rhr")
    if today.get("rhr") and rhr_base and today.get("sleep_s"):
        delta = today["rhr"] - rhr_base
        if delta >= 5:
            red.append(f"resting HR +{delta:.0f} bpm")
        elif delta >= 3:
            amber.append(f"resting HR +{delta:.0f} bpm")
    r = today.get("readiness_latest")
    if r is not None:
        (red if r < 35 else amber if r < 60 else []).append(f"readiness {r:.0f}")
    bb = today.get("bb_high")
    if bb is not None:
        (red if bb < 35 else amber if bb < 60 else []).append(f"Body Battery {bb:.0f}")
    a = today.get("acwr")
    if a is not None:
        if a > 1.5:
            red.append(f"load spike (ACWR {a:.2f})")
        elif a > 1.3:
            amber.append(f"load rising fast (ACWR {a:.2f})")
    hrv = (today.get("hrv_status") or "").upper()
    if hrv in ("LOW", "POOR"):
        red.append("HRV low")
    elif hrv == "UNBALANCED":
        amber.append("HRV unbalanced")
    if today.get("sleep_s"):
        h = today["sleep_s"] / 3600
        if h < 6:
            red.append(f"short sleep ({h:.1f}h)")
        elif h < 7:
            amber.append(f"sleep {h:.1f}h")
    rec = today.get("recovery_time_h")
    if rec is not None and rec > 36:
        amber.append(f"recovery time {rec:.0f}h")
    past = [x for x in recent if x["date"] < day]
    hard_3d = sum(is_hard(x) for x in past if (day - x["date"]).days <= 3)
    if hard_3d >= 2:
        amber.append(f"{hard_3d} hard sessions in the last 3 days")
    streak = 0
    d = day - timedelta(days=1)
    trained = {x["date"] for x in past}
    while d in trained:
        streak += 1
        d -= timedelta(days=1)
    if streak >= 6:
        amber.append(f"{streak} days in a row without rest")

    if not any(today.get(k) is not None for k in ("readiness_latest", "bb_high", "acwr", "rhr")):
        return "❔ Not enough data", ["wear the watch day and night for a few days"]
    if len(red) >= 2:
        verdict = "🔴 REST or very easy (≤30' in Z1, mobility, walk)"
    elif red or len(amber) >= 3:
        verdict = "🟠 EASY: aerobic Z1-Z2 up to 45-60', technique, no max efforts"
    elif amber:
        verdict = "🟡 MODERATE: steady Z2-Z3 or controlled strength, skip all-out work"
    else:
        verdict = "🟢 HARD is fine: intervals Z4-Z5 or heavy strength"
        if a is not None and a < 0.8:
            verdict += " (load is below your usual: room to build)"

    # The signals above describe readiness for training. If a session is already done today,
    # the question becomes whether to add another one.
    done = [x for x in recent if x["date"] == day]
    if done:
        label = ", ".join(f"{sport(x['type_key'])} {hm(x['duration_s'])}" for x in done)
        minutes = sum(x["duration_s"] or 0 for x in done) / 60
        if any(is_hard(x) for x in done) or minutes >= 60:
            room = " Readiness leaves room: use it in the next 1-2 days." if verdict[0] in "🟢🟡" else ""
            verdict = f"✅ DONE for today ({label}): recover, mobility or an easy walk at most.{room}"
        else:
            if verdict.startswith("🟢"):
                verdict = "🟡 MODERATE: steady Z2-Z3 or controlled strength, skip all-out work"
            verdict = f"Already trained today ({label}). A second session only if short: {verdict}"
        amber.append(f"already trained today ({label})")
    return verdict, red + amber


def today_status(aid: int, day: date) -> str:
    rows = _daily(aid, day - timedelta(days=28), day)
    today = next((r for r in rows if r["date"] == day), None)
    if today is None:
        return f"No daily data for {day}. Try sync_garmin."
    base = [r for r in rows if r["date"] < day]
    signals = []

    rhr_base = _avg(base, "rhr")
    if today["rhr"] and rhr_base:
        delta = today["rhr"] - rhr_base
        flag = "🔴" if delta >= 5 else "🟡" if delta >= 3 else "🟢"
        note = ""
        if not today["sleep_s"]:
            # Without recorded sleep Garmin estimates resting HR during the day: unstable.
            flag, note = "⚪", " — daytime estimate without recorded sleep, unreliable"
        signals.append(f"{flag} Resting HR {num(today['rhr'])} bpm ({delta:+.0f} vs 4-week avg {rhr_base:.0f}){note}")
    if today["readiness_latest"] is not None:
        r = today["readiness_latest"]
        flag = "🟢" if r >= 60 else "🟡" if r >= 35 else "🔴"
        signals.append(f"{flag} Training Readiness {num(r)} ({today['readiness_level'] or '-'}), "
                       f"recovery left {num(today['recovery_time_h'])}h")
    if today["bb_high"] is not None:
        flag = "🟢" if today["bb_high"] >= 60 else "🟡" if today["bb_high"] >= 35 else "🔴"
        signals.append(f"{flag} Body Battery max {num(today['bb_high'])}, now {num(today['bb_latest'])}")
    if today["acwr"] is not None:
        a = today["acwr"]
        flag = "🔴" if a > 1.5 else "🟡" if a > 1.3 or a < 0.8 else "🟢"
        signals.append(f"{flag} Acute/chronic load {a:.2f} ({today['acwr_status'] or '-'}): "
                       f"acute {num(today['acute_load'])}, chronic {num(today['chronic_load'])}")
    stress_base = _avg(base, "stress_avg")
    if today["stress_avg"] is not None and today["stress_avg"] >= 0:
        signals.append(f"• Avg stress {num(today['stress_avg'])}"
                       + (f" (usual {stress_base:.0f})" if stress_base else ""))
    if today["sleep_s"]:
        signals.append(f"• Sleep {hm(today['sleep_s'])}, score {num(today['sleep_score'])}, "
                       f"HRV {num(today['hrv_avg'])} ({today['hrv_status'] or '-'})")

    recent = _activities(aid, day - timedelta(days=13), day)
    verdict, reasons = readiness_verdict(today, base, recent, day)
    lines = [f"## Status {day:%a %d/%m/%Y}", "", f"**Suggestion: {verdict}**"]
    if reasons:
        lines.append("Because: " + ", ".join(reasons) + ".")
    lines += ["", *signals]

    if not any(r["sleep_s"] for r in rows[-7:]):
        lines.append("\n⚠️ No sleep/HRV data in the last 7 days (watch not worn at night?): "
                     "readiness and Body Battery are less reliable.")

    goals = goal_lines(aid, day)
    if goals:
        lines += ["", *goals]

    plan = _planned(aid, day, day)
    pairs, _ = match_plan(plan, _activities(aid, day, day))
    if pairs:
        lines.append("\n**Planned today:**")
        for p, a in pairs:
            lines.append(f"- {p['title']} ({sport(p['sport'])}, {source(p.get('provider'))}) — "
                         f"{'✅ done' if a else 'to do'}")
    else:
        lines.append("\nNothing planned today: schedule_workout can put a session on the watch.")
    nxt = _planned(aid, day + timedelta(days=1), day + timedelta(days=7))
    if nxt:
        lines.append(f"**Next:** {dd(nxt[0]['date'], True)} {nxt[0]['title']}")

    last3 = [a for a in recent if (day - a["date"]).days <= 3]
    if last3:
        lines.append("\n**Last 3 days and today:** " + "; ".join(
            f"{DAYS[a['date'].weekday()]} {sport(a['type_key'])} {hm(a['duration_s'])} "
            f"(load {num(a['training_load'])}{', hard' if is_hard(a) else ''})" for a in last3))
    return "\n".join(lines)


# --- One session -----------------------------------------------------------------

def analyze_session(aid: int, activity_id: int | None, day: date | None) -> str:
    t = db.activities
    stmt = select(t).where(t.c.athlete_id == aid)
    if activity_id:
        stmt = stmt.where(t.c.activity_id == activity_id)
    elif day:
        stmt = stmt.where(t.c.date == day)
    rows = db.query(stmt.order_by(t.c.start_local.desc()).limit(1))
    if not rows:
        return "Activity not found. Use list_activities to see ids."
    a = rows[0]
    raw = a["raw"] or {}

    lines = [f"## {a['name']} — {sport(a['type_key'])}, {a['start_local'][:16].replace('T', ' ')}",
             f"Duration {hm(a['duration_s'])} · HR avg/max {num(a['avg_hr'])}/{num(a['max_hr'])} · "
             f"load {num(a['training_load'])} · Training Effect aerobic {num(a['aerobic_te'], 1)} / "
             f"anaerobic {num(a['anaerobic_te'], 1)} ({a['te_label'] or '-'}) · "
             f"Body Battery {num(raw.get('differenceBodyBattery'))} · id {a['activity_id']}"]
    if raw.get("distance"):
        lines[-1] += f" · {raw['distance'] / 1000:.2f} km"

    zones = [a.get(f"hr_z{i}_s") or 0 for i in range(1, 6)]
    if sum(zones):
        tot = sum(zones)
        lines.append("HR zones: " + " · ".join(f"Z{i + 1} {z / 60:.0f}' ({100 * z / tot:.0f}%)"
                                               for i, z in enumerate(zones)))

    # Laps: motos in motocross, reps elsewhere.
    if (a["lap_count"] or 0) > 1:
        try:
            laps = get_client().get_activity_splits(str(a["activity_id"])).get("lapDTOs", [])
        except Exception:  # noqa: BLE001
            laps = []
        work = [x for x in laps if (x.get("duration") or 0) >= 60]
        if work:
            label = "Moto" if "moto" in (a["type_key"] or "") else "Lap"
            lines += ["", f"| {label} | Duration | Distance | Avg HR | Max HR |", "|---|---|---|---|---|"]
            for i, x in enumerate(work, 1):
                dist = f"{x['distance'] / 1000:.2f} km" if x.get("distance") else "-"
                lines.append(f"| {i} | {hm(x.get('duration'))} | {dist} | "
                             f"{num(x.get('averageHR'))} | {num(x.get('maxHR'))} |")
            hrs = [x.get("averageHR") for x in work if x.get("averageHR")]
            if len(hrs) >= 2:
                lines.append(f"Cardiac drift first→last: {hrs[-1] - hrs[0]:+.0f} bpm "
                             "(rising HR at the same effort is a fatigue signal)")

    sets = raw.get("summarizedExerciseSets") or []
    if sets:
        lines += ["", "| Exercise | Sets | Reps | Max load |", "|---|---|---|---|"]
        for s in sorted(sets, key=lambda s: -(s.get("sets") or 0)):
            name = (s.get("subCategory") or s.get("category") or "?").replace("_", " ").strip().lower()
            w = (s.get("maxWeight") or 0) / 1000
            lines.append(f"| {name} | {s.get('sets')} | {s.get('reps')} | {f'{w:g} kg' if w else '-'} |")

    if a["workout_id"]:
        w = db.query(select(db.workouts).where(db.workouts.c.workout_id == a["workout_id"]))
        if w and w[0]["steps"]:
            w = w[0]
            lines += ["", f"**Plan ({source(w['provider'])}): {w['name']}**"]
            if w["sport"] == "strength_training":
                lines[-1] += (f" — sets done {raw.get('totalSets') or '-'} of "
                              f"{_planned_sets(w['steps'])} planned")
            else:
                lines += [f"- {s['exercise'] or s['kind']}: {s['sets']}× "
                          f"{hm(s['time_s']) if s['time_s'] else str(s['reps'] or '') + ' reps'}"
                          f"{' · ' + s['note'] if s['note'] else ''}"
                          for s in w["steps"] if s["kind"] in ("interval", "warmup", "cooldown")]

    notes = db.query(select(db.notes).where(db.notes.c.athlete_id == aid,
                                            db.notes.c.activity_id == a["activity_id"]))
    for note in notes:
        lines.append(f"📝 RPE {note['rpe'] or '-'}: {note['text']}")
        if note["rpe"] and a["aerobic_te"]:
            # Effort felt much harder than what the heart says: fatigue, heat, illness.
            if note["rpe"] >= 8 and a["aerobic_te"] < 3 and _z45_min(a) < 5:
                lines.append("⚠️ Felt very hard but HR load was low: possible fatigue, heat or "
                             "illness. Watch the next days.")
    return "\n".join(lines)


# --- Trends and insights -------------------------------------------------------------

def insights(aid: int, end: date) -> list[str]:
    """Training patterns Garmin does not show: intensity distribution,
    monotony/strain, ramp rate, rest days, resting HR drift."""
    out = []
    acts28 = _activities(aid, end - timedelta(days=27), end)
    z = [sum(a.get(f"hr_z{i}_s") or 0 for a in acts28) for i in range(1, 6)]
    tot = sum(z)
    if tot > 0:
        low, mid, high = (z[0] + z[1]) / tot * 100, z[2] / tot * 100, (z[3] + z[4]) / tot * 100
        if mid > 30:
            comment = "a lot of 'grey zone' Z3: make easy days easier and hard days harder"
        elif low >= 75:
            comment = "well polarised (≈80/20)"
        elif high > 25:
            comment = "very intense mix: watch recovery"
        else:
            comment = "balanced"
        out.append(f"- **Intensity (28 d):** easy Z1-2 {low:.0f}% · Z3 {mid:.0f}% · hard Z4-5 {high:.0f}% — {comment}")

    loads = {}
    for a in _activities(aid, end - timedelta(days=6), end):
        loads[a["date"]] = loads.get(a["date"], 0) + (a["training_load"] or 0)
    week = [loads.get(end - timedelta(days=i), 0) for i in range(7)]
    if sum(week) > 0:
        sd = pstdev(week)
        monotony = mean(week) / sd if sd else float("inf")
        strain = sum(week) * (monotony if sd else 0)
        flag = " — too uniform: alternate hard and easy days" if monotony > 2 else ""
        out.append(f"- **Monotony (7 d):** {monotony:.1f} · strain {strain:.0f}{flag}")

    by_week = []
    for i in range(4):
        we = end - timedelta(days=7 * i)
        ws = we - timedelta(days=6)
        by_week.append(sum(a["training_load"] or 0 for a in acts28 if ws <= a["date"] <= we))
    prev = mean(by_week[1:])
    if prev > 0:
        ramp = (by_week[0] - prev) / prev * 100
        flag = " — big jump, injury risk rises above +30%" if ramp > 30 else \
            " — deload or break" if ramp < -40 else ""
        out.append(f"- **Ramp:** last 7 d load {by_week[0]:.0f} vs {prev:.0f} avg of previous 3 weeks "
                   f"({ramp:+.0f}%){flag}")

    trained = {a["date"] for a in acts28 if (end - a["date"]).days < 14}
    rest = 14 - len(trained)
    out.append(f"- **Rest days (14 d):** {rest}" + (" — add at least one per week" if rest < 2 else ""))

    days = _daily(aid, end - timedelta(days=27), end)
    reliable = [d for d in days if d["rhr"] and d["sleep_s"]]
    last7 = [d["rhr"] for d in reliable if (end - d["date"]).days < 7]
    prev21 = [d["rhr"] for d in reliable if (end - d["date"]).days >= 7]
    if len(last7) >= 3 and len(prev21) >= 5:
        diff = mean(last7) - mean(prev21)
        flag = " — accumulated fatigue or illness?" if diff >= 3 else " — adapting well" if diff <= -2 else ""
        out.append(f"- **Resting HR:** last 7 d {mean(last7):.0f} vs {mean(prev21):.0f} before ({diff:+.1f}){flag}")

    rpe = [n for n in _notes(aid, end - timedelta(days=13), end) if n["rpe"]]
    if rpe:
        out.append(f"- **Perceived effort (14 d):** avg RPE {mean(n['rpe'] for n in rpe):.1f} "
                   f"over {len(rpe)} notes")
    return out


def trends(aid: int, weeks: int) -> str:
    end = date.today()
    start, _ = week_bounds(end - timedelta(weeks=weeks - 1))
    days = _daily(aid, start, end)
    acts = _activities(aid, start, end)
    lines = ["| Week | Sessions | Hard | Volume | Load | Z4+Z5 | Rest HR | Stress | BB max | Readiness | ACWR |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    ws = start
    while ws <= end:
        we = ws + timedelta(days=6)
        d = [r for r in days if ws <= r["date"] <= we]
        a = [x for x in acts if ws <= x["date"] <= we]
        stress = [r for r in d if (r["stress_avg"] or -1) >= 0]
        lines.append(
            f"| {ws:%d/%m} | {len(a)} | {sum(is_hard(x) for x in a)} | "
            f"{hm(sum(x['duration_s'] or 0 for x in a))} | "
            f"{num(sum(x['training_load'] or 0 for x in a))} | {sum(_z45_min(x) for x in a):.0f}' | "
            f"{num(_avg(d, 'rhr'), 1)} | {num(_avg(stress, 'stress_avg'))} | {num(_avg(d, 'bb_high'))} | "
            f"{num(_avg(d, 'readiness_morning'))} | {num(_avg(d, 'acwr'), 2)} |")
        ws += timedelta(days=7)

    mix: dict[str, float] = {}
    for x in acts:
        mix[sport(x["type_key"])] = mix.get(sport(x["type_key"]), 0) + (x["duration_s"] or 0)
    if mix:
        lines.append("\n**Sport mix:** " + ", ".join(
            f"{k} {hm(v)}" for k, v in sorted(mix.items(), key=lambda kv: -kv[1])))
    vo2 = [r["vo2max"] for r in days if r["vo2max"]]
    if vo2:
        lines.append(f"**VO2max:** {vo2[0]:.1f} → {vo2[-1]:.1f}")
    ins = insights(aid, end)
    if ins:
        lines += ["", "**Insights**", *ins]
    return "\n".join(lines)


# --- Weekly review ---------------------------------------------------------------------

def weekly_review(aid: int, ref: date, for_coach: bool) -> str:
    ws, we = week_bounds(ref)
    athlete = db.query(select(db.athletes).where(db.athletes.c.athlete_id == aid))
    name = athlete[0]["full_name"] if athlete and for_coach else ""
    title = "Weekly report" + (f" — {name}" if name else "")
    parts = [f"# {title}: {ws:%d/%m} – {we:%d/%m/%Y}", ""]
    goals = goal_lines(aid, we)
    if goals:
        parts += goals + [""]
    parts += [plan_vs_done(aid, ref), "", "## Last 4 weeks", trends(aid, 4)]
    notes = _notes(aid, ws, we)
    if notes:
        parts += ["", "## Athlete notes"]
        parts += [f"- {dd(n['date'])}: RPE {n['rpe'] or '-'} — {n['text']}" for n in notes]
    if not for_coach:
        parts += ["", "_Next step: agree on next week's focus and put the key sessions on the watch "
                      "with schedule_workout._"]
    return "\n".join(parts)


# --- Activity list and notes -----------------------------------------------------------

def list_activities(aid: int, days: int, sport_filter: str | None) -> str:
    acts = _activities(aid, date.today() - timedelta(days=days), date.today())
    if sport_filter:
        f = sport_filter.casefold()
        acts = [a for a in acts if f in (a["type_key"] or "") or f in sport(a["type_key"]).casefold()]
    if not acts:
        return "No activities in this period."
    lines = ["| Date | Sport | Name | Duration | HR | Load | id |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {a['date']:%d/%m} | {sport(a['type_key'])} | {a['name']} | {hm(a['duration_s'])} | "
              f"{num(a['avg_hr'])} | {num(a['training_load'])} | {a['activity_id']} |"
              for a in reversed(acts)]
    return "\n".join(lines)


def add_note(aid: int, text: str, day: date, rpe: int | None, activity_id: int | None, tags: str | None) -> str:
    if activity_id is None:
        same_day = _activities(aid, day, day)
        if len(same_day) == 1:
            activity_id = same_day[0]["activity_id"]
    db.insert_row(db.notes, {"athlete_id": aid, "date": day, "activity_id": activity_id,
                             "rpe": rpe, "tags": tags, "text": text, "created_at": db.now_utc()})
    linked = f" (linked to activity {activity_id})" if activity_id else ""
    return f"Note saved for {day:%d/%m}{linked}."


# --- Chart data -----------------------------------------------------------------------------

def chart_data(aid: int, start: date, end: date) -> str:
    """Compact JSON for charts: daily series, activities with zones and strength
    sets, planned workouts matched to what was done, notes, goals."""
    days = _daily(aid, start, end)
    acts = _activities(aid, start, end)
    pairs, _ = match_plan(_planned(aid, start, end), acts)
    keys = ("rhr", "stress_avg", "bb_high", "bb_low", "readiness_morning", "readiness_latest",
            "acute_load", "chronic_load", "acwr", "vo2max", "sleep_s", "hrv_avg", "steps")
    out = {
        "range": [start.isoformat(), end.isoformat()],
        # Garmin uses -1 for "no data" on stress.
        "daily": [{"date": r["date"].isoformat(),
                   **{k: (None if k == "stress_avg" and (r[k] or 0) < 0 else r[k]) for k in keys},
                   "rhr_reliable": bool(r["sleep_s"])} for r in days],
        "activities": [{
            "id": a["activity_id"], "date": a["date"].isoformat(), "start": a["start_local"],
            "sport": sport(a["type_key"]), "type_key": a["type_key"], "name": a["name"],
            "duration_min": round((a["duration_s"] or 0) / 60, 1), "avg_hr": a["avg_hr"],
            "max_hr": a["max_hr"], "load": a["training_load"], "aerobic_te": a["aerobic_te"],
            "anaerobic_te": a["anaerobic_te"], "laps": a["lap_count"], "hard": is_hard(a),
            "zones_min": [round((a.get(f"hr_z{i}_s") or 0) / 60, 1) for i in range(1, 6)],
            "total_sets": (a["raw"] or {}).get("totalSets"),
            "exercises": [{
                "name": (s.get("subCategory") or s.get("category") or "?").replace("_", " ").strip().lower(),
                "sets": s.get("sets"), "reps": s.get("reps"),
                "max_kg": round((s.get("maxWeight") or 0) / 1000, 1) or None,
            } for s in (a["raw"] or {}).get("summarizedExerciseSets") or []],
        } for a in acts],
        "plan": [{
            "date": p["date"].isoformat(), "title": p["title"], "sport": sport(p["sport"]),
            "source": source(p.get("provider")),
            "by_trainer": p.get("provider") == COACH_PROVIDER,
            "planned_sets": _planned_sets(p.get("steps")) or None,
            "done_activity_id": a["activity_id"] if a else None,
        } for p, a in pairs],
        "notes": [{"date": n["date"].isoformat(), "activity_id": n["activity_id"], "rpe": n["rpe"],
                   "tags": n["tags"], "text": n["text"]} for n in _notes(aid, start, end)],
        "goals": [{"title": g["title"], "sport": g["sport"],
                   "event_date": g["event_date"].isoformat() if g["event_date"] else None,
                   "details": g["details"]} for g in _goals(aid)],
    }
    return json.dumps(out, ensure_ascii=False, default=str)
