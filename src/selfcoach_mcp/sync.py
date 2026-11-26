"""Garmin Connect -> database sync.

Tools never hit Garmin on every question: they read the database, and this
module refreshes only recent days, pausing between calls to stay clear of
Garmin's anti-bot rate limiting (HTTP 429).
"""

import time
from datetime import date, datetime, timedelta

from garminconnect import Garmin
from sqlalchemy import select

from . import db
from .auth import AuthError, explain, get_client
from .config import AUTO_SYNC_MINUTES, INITIAL_SYNC_DAYS

PAUSE_S = 0.3


def _call(fn, *args):
    """Tolerant Garmin call: one failing endpoint does not stop the sync."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return None
    finally:
        time.sleep(PAUSE_S)


def athlete_id(g: Garmin) -> int:
    aid = g.profile_id
    db.upsert(db.athletes, [{
        "athlete_id": aid, "display_name": g.display_name, "full_name": g.full_name,
    }])
    return aid


# --- Activities -----------------------------------------------------------

def _activity_row(aid: int, a: dict) -> dict:
    start = a.get("startTimeLocal") or ""
    return {
        "activity_id": a["activityId"],
        "athlete_id": aid,
        "date": date.fromisoformat(start[:10]) if start else None,
        "start_local": start,
        "type_key": (a.get("activityType") or {}).get("typeKey"),
        "name": (a.get("activityName") or "").strip(),
        "duration_s": a.get("duration"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "training_load": a.get("activityTrainingLoad"),
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "anaerobic_te": a.get("anaerobicTrainingEffect"),
        "te_label": a.get("trainingEffectLabel"),
        "calories": a.get("calories"),
        "lap_count": a.get("lapCount"),
        "workout_id": a.get("workoutId"),
        **{f"hr_z{i}_s": a.get(f"hrTimeInZone_{i}") for i in range(1, 6)},
        "raw": a,
    }


def sync_activities(g: Garmin, aid: int, start: date, end: date) -> int:
    acts = _call(g.get_activities_by_date, start.isoformat(), end.isoformat()) or []
    return db.upsert(db.activities, [_activity_row(aid, a) for a in acts])


# --- Calendar and planned workouts --------------------------------------

def simplify_steps(steps: list, repeat: int = 1) -> list[dict]:
    """Flatten Garmin workout steps into readable rows (exercise, sets, target)."""
    out = []
    for s in steps:
        kind = (s.get("stepType") or {}).get("stepTypeKey")
        if s.get("workoutSteps"):
            out += simplify_steps(s["workoutSteps"], repeat * int(s.get("numberOfIterations") or 1))
            continue
        if kind == "rest":
            continue
        end_key = (s.get("endCondition") or {}).get("conditionTypeKey")
        val = s.get("endConditionValue")
        weight = s.get("weightValue")
        out.append({
            "kind": kind,
            "exercise": s.get("exerciseName") or s.get("category"),
            "category": s.get("category"),
            "sets": repeat,
            "reps": int(val) if end_key == "reps" and val else None,
            "time_s": int(val) if end_key == "time" and val else None,
            "weight_kg": weight if weight and weight > 0 else None,
            "target": (s.get("targetType") or {}).get("workoutTargetTypeKey"),
            "note": s.get("description"),
        })
    return out


def _sync_workout(g: Garmin, aid: int, workout_id: int) -> None:
    exists = db.query(select(db.workouts.c.workout_id).where(db.workouts.c.workout_id == workout_id))
    if exists:
        return
    w = _call(g.get_workout_by_id, workout_id)
    if not w:
        return
    steps = []
    for seg in w.get("workoutSegments", []):
        steps += simplify_steps(seg.get("workoutSteps", []))
    db.upsert(db.workouts, [{
        "workout_id": workout_id,
        "athlete_id": aid,
        "name": (w.get("workoutName") or "").strip(),
        "sport": (w.get("sportType") or {}).get("sportTypeKey"),
        "provider": w.get("workoutProvider"),
        "description": w.get("description"),
        "steps": steps,
    }])


def sync_calendar(g: Garmin, aid: int, start: date, end: date) -> int:
    n = 0
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        cal = _call(g.get_scheduled_workouts, y, m) or {}
        rows = []
        for it in cal.get("calendarItems", []):
            if it.get("itemType") != "workout":
                continue
            rows.append({
                "calendar_item_id": it["id"],
                "athlete_id": aid,
                "date": date.fromisoformat(it["date"]),
                "workout_id": it.get("workoutId"),
                "title": (it.get("title") or "").strip(),
                "sport": it.get("sportTypeKey"),
            })
            if it.get("workoutId"):
                _sync_workout(g, aid, it["workoutId"])
        n += db.upsert(db.planned, rows)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return n


# --- Daily metrics ------------------------------------------------------

def _first(d, *path):
    for p in path:
        if d is None:
            return None
        d = d.get(p) if isinstance(d, dict) else None
    return d


def _daily_row(g: Garmin, aid: int, day: date) -> dict:
    d = day.isoformat()
    row: dict = {"athlete_id": aid, "date": day}

    s = _call(g.get_stats, d) or {}
    row.update(
        rhr=s.get("restingHeartRate"), rhr_7d=s.get("lastSevenDaysAvgRestingHeartRate"),
        stress_avg=s.get("averageStressLevel"), stress_max=s.get("maxStressLevel"),
        bb_high=s.get("bodyBatteryHighestValue"), bb_low=s.get("bodyBatteryLowestValue"),
        bb_charged=s.get("bodyBatteryChargedValue"), bb_drained=s.get("bodyBatteryDrainedValue"),
        bb_latest=s.get("bodyBatteryMostRecentValue"), steps=s.get("totalSteps"),
        intensity_min=(s.get("moderateIntensityMinutes") or 0) + 2 * (s.get("vigorousIntensityMinutes") or 0),
    )

    sleep = _call(g.get_sleep_data, d) or {}
    secs = _first(sleep, "dailySleepDTO", "sleepTimeSeconds")
    row["sleep_s"] = secs or None
    row["sleep_score"] = _first(sleep, "dailySleepDTO", "sleepScores", "overall", "value")

    hrv = _call(g.get_hrv_data, d) or {}
    row["hrv_avg"] = _first(hrv, "hrvSummary", "lastNightAvg")
    row["hrv_status"] = _first(hrv, "hrvSummary", "status")

    tr = sorted(_call(g.get_training_readiness, d) or [], key=lambda x: x.get("timestamp") or "")
    if tr:
        row["readiness_morning"] = tr[0].get("score")
        row["readiness_latest"] = tr[-1].get("score")
        row["readiness_level"] = tr[-1].get("level")
        rt = tr[-1].get("recoveryTime")
        row["recovery_time_h"] = round(rt / 60, 1) if rt is not None else None

    ts = _call(g.get_training_status, d) or {}
    status = next(iter((_first(ts, "mostRecentTrainingStatus", "latestTrainingStatusData") or {}).values()), {})
    acute = status.get("acuteTrainingLoadDTO") or {}
    balance = next(iter((_first(ts, "mostRecentTrainingLoadBalance", "metricsTrainingLoadBalanceDTOMap") or {}).values()), {})
    row.update(
        acute_load=acute.get("dailyTrainingLoadAcute"), chronic_load=acute.get("dailyTrainingLoadChronic"),
        acwr=acute.get("dailyAcuteChronicWorkloadRatio"), acwr_status=acute.get("acwrStatus"),
        training_status=status.get("trainingStatusFeedbackPhrase"),
        load_balance=balance.get("trainingBalanceFeedbackPhrase"),
        vo2max=_first(ts, "mostRecentVO2Max", "generic", "vo2MaxPreciseValue"),
    )
    return row


def sync_daily(g: Garmin, aid: int, start: date, end: date, skip_existing: bool = True) -> int:
    existing = set()
    if skip_existing:
        existing = {r["date"] for r in db.query(select(db.daily.c.date).where(
            db.daily.c.athlete_id == aid, db.daily.c.date >= start, db.daily.c.date <= end))}
    rows = []
    day = start
    recent = date.today() - timedelta(days=2)  # the last days are still changing
    while day <= end:
        if day not in existing or day >= recent:
            rows.append(_daily_row(g, aid, day))
        day += timedelta(days=1)
    return db.upsert(db.daily, rows)


# --- Orchestration ------------------------------------------------------

def sync(days: int, force: bool = False) -> dict:
    g = get_client()
    aid = athlete_id(g)
    end = date.today()
    start = end - timedelta(days=days)
    result = {
        "activities": sync_activities(g, aid, start, end),
        "planned": sync_calendar(g, aid, start, end + timedelta(days=31)),
        "daily": sync_daily(g, aid, start, end, skip_existing=not force),
    }
    db.set_state(aid, "last_sync", db.now_utc().isoformat())
    return result


def ensure_fresh() -> tuple[int, str | None]:
    """Light automatic sync before every tool. Returns (athlete_id, warning).

    If Garmin is unreachable but the database already has data, tools keep
    working on cached data and the warning says so.
    """
    try:
        g = get_client()
        aid = athlete_id(g)
        last = db.get_state(aid, "last_sync")
        if last is None:
            sync(INITIAL_SYNC_DAYS)
        elif db.now_utc() - datetime.fromisoformat(last) > timedelta(minutes=AUTO_SYNC_MINUTES):
            sync(7)
        return aid, None
    except Exception as e:  # noqa: BLE001
        cached = db.query(select(db.athletes.c.athlete_id))
        if len(cached) == 1:
            reason = str(e) if isinstance(e, AuthError) else explain(e)
            return cached[0]["athlete_id"], f"⚠️ Could not sync with Garmin, showing cached data. {reason}"
        if isinstance(e, AuthError):
            raise
        raise AuthError(explain(e)) from e
