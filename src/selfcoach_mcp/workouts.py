"""Build structured workouts and put them on the Garmin calendar.

A workout scheduled here syncs to the watch like any Garmin Connect workout:
the athlete starts it from the watch and gets step-by-step guidance (time,
distance, HR/power/pace targets, sets and reps). This is what lets the
assistant act as a coach instead of only commenting on past data.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from garminconnect import exercises
from garminconnect.workout import (
    BaseWorkout,
    ConditionType,
    ExecutableStep,
    RepeatGroup,
    StepType,
    TargetType,
    WorkoutSegment,
)
from sqlalchemy import select

from . import db
from .auth import get_client

PROVIDER = "selfcoach-mcp"
MARKER = "Planned with selfcoach-mcp"

# sport alias -> (sportTypeId, sportTypeKey)
SPORTS: dict[str, tuple[int, str]] = {
    "running": (1, "running"), "run": (1, "running"),
    "cycling": (2, "cycling"), "bike": (2, "cycling"), "mtb": (2, "cycling"), "indoor_cycling": (2, "cycling"),
    "swimming": (4, "swimming"),
    "strength": (5, "strength_training"), "strength_training": (5, "strength_training"),
    "cardio": (6, "cardio_training"), "cardio_training": (6, "cardio_training"),
    "yoga": (7, "yoga"), "pilates": (8, "pilates"), "hiit": (9, "hiit"), "mobility": (11, "mobility"),
    "walking": (17, "walking"), "hiking": (18, "hiking"),
    "other": (3, "other"),
}

_STEP_TYPES = {
    "warmup": (StepType.WARMUP, 1), "cooldown": (StepType.COOLDOWN, 2),
    "interval": (StepType.INTERVAL, 3), "recovery": (StepType.RECOVERY, 4),
    "rest": (StepType.REST, 5), "other": (StepType.OTHER, 7),
}


class WorkoutError(ValueError):
    """Invalid workout definition, with a message the model can fix."""


def _pace_to_mps(pace: str) -> float:
    """'5:30' min/km -> metres per second."""
    try:
        m, s = pace.split(":")
        return 1000 / (int(m) * 60 + int(s))
    except Exception as e:  # noqa: BLE001
        raise WorkoutError(f"Pace '{pace}' must look like '5:30' (min:sec per km).") from e


def _end_condition(step: dict) -> tuple[dict, float, float]:
    """Returns (endCondition, value, estimated seconds)."""
    def cond(cid: int, key: str, order: int) -> dict:
        return {"conditionTypeId": cid, "conditionTypeKey": key, "displayOrder": order, "displayable": True}

    if step.get("minutes") is not None:
        s = float(step["minutes"]) * 60
        return cond(ConditionType.TIME, "time", 2), s, s
    if step.get("seconds") is not None:
        s = float(step["seconds"])
        return cond(ConditionType.TIME, "time", 2), s, s
    if step.get("km") is not None:
        m = float(step["km"]) * 1000
        return cond(ConditionType.DISTANCE, "distance", 3), m, m / 3  # ~11 km/h estimate
    if step.get("meters") is not None:
        m = float(step["meters"])
        return cond(ConditionType.DISTANCE, "distance", 3), m, m / 3
    # No duration: the athlete presses lap to move on.
    return cond(ConditionType.LAP_BUTTON, "lap.button", 1), 0, 300


def _target(step: dict) -> dict[str, Any]:
    def ttype(tid: int, key: str) -> dict:
        return {"workoutTargetTypeId": tid, "workoutTargetTypeKey": key, "displayOrder": 1}

    if step.get("hr_zone") is not None:
        z = int(step["hr_zone"])
        if not 1 <= z <= 5:
            raise WorkoutError("hr_zone must be 1-5.")
        return {"targetType": ttype(TargetType.HEART_RATE_ZONE, "heart.rate.zone"), "zoneNumber": z}
    if step.get("hr_bpm"):
        lo, hi = sorted(step["hr_bpm"])
        return {"targetType": ttype(TargetType.HEART_RATE_ZONE, "heart.rate.zone"),
                "targetValueOne": lo, "targetValueTwo": hi}
    if step.get("power_zone") is not None:
        return {"targetType": ttype(TargetType.POWER_ZONE, "power.zone"), "zoneNumber": int(step["power_zone"])}
    if step.get("watts"):
        lo, hi = sorted(step["watts"])
        return {"targetType": ttype(TargetType.POWER_ZONE, "power.zone"),
                "targetValueOne": lo, "targetValueTwo": hi}
    if step.get("pace_per_km"):
        a, b = (_pace_to_mps(p) for p in step["pace_per_km"])
        return {"targetType": ttype(TargetType.PACE_ZONE, "pace.zone"),
                "targetValueOne": min(a, b), "targetValueTwo": max(a, b)}
    if step.get("cadence"):
        lo, hi = sorted(step["cadence"])
        return {"targetType": ttype(TargetType.CADENCE, "cadence"),
                "targetValueOne": lo, "targetValueTwo": hi}
    return {"targetType": ttype(TargetType.NO_TARGET, "no.target")}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def resolve_exercise(name: str) -> dict[str, str]:
    """Map a free-text exercise name to Garmin's catalog (category + exercise).

    Matching ignores case, spaces, hyphens and a plural 's': "push ups" finds
    "Push-up". Ambiguous names return the closest options instead of guessing.
    """
    hit = exercises.resolve(name)
    if hit:
        return hit
    needle = _norm(name)
    if not needle:
        raise WorkoutError("Each exercise step needs a 'name'.")
    variants = {needle, needle.rstrip("s")}
    for e in exercises.EXERCISES:
        n = _norm(e["name"])
        if n in variants or n.rstrip("s") in variants:
            return e
    found = sorted((e for e in exercises.EXERCISES if needle.rstrip("s") in _norm(e["name"])),
                   key=lambda e: len(e["name"]))
    if len(found) == 1:
        return found[0]
    if not found:
        words = [w for w in re.split(r"[\s-]+", name.casefold()) if len(w) > 2]
        found = sorted(exercises.EXERCISES, key=lambda e: (-sum(w in e["name"].casefold() for w in words),
                                                           len(e["name"])))
    best = [e["name"] for e in found[:8]]
    raise WorkoutError(f"Exercise '{name}' is ambiguous or not in Garmin's catalog. Closest: "
                       f"{', '.join(best)}. Use one of these exact names.")


class _Builder:
    def __init__(self) -> None:
        self.order = 0
        self.seconds = 0.0
        self.lines: list[str] = []

    def next(self) -> int:
        self.order += 1
        return self.order

    def steps(self, steps: list[dict], mult: int = 1, indent: str = "") -> list:
        out = []
        for st in steps:
            kind = (st.get("type") or "interval").lower()
            if kind == "repeat":
                times = int(st.get("times") or 0)
                if times < 1 or not st.get("steps"):
                    raise WorkoutError("A 'repeat' step needs 'times' >= 1 and a non-empty 'steps' list.")
                order = self.next()
                self.lines.append(f"{indent}- {times}×")
                inner = self.steps(st["steps"], mult * times, indent + "  ")
                out.append(RepeatGroup(
                    stepOrder=order,
                    stepType={"stepTypeId": StepType.REPEAT, "stepTypeKey": "repeat", "displayOrder": 6},
                    numberOfIterations=times, workoutSteps=inner,
                    endCondition={"conditionTypeId": ConditionType.ITERATIONS,
                                  "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False},
                    endConditionValue=float(times)))
            elif kind == "exercise":
                out.append(self.exercise(st, mult, indent))
            elif kind in _STEP_TYPES:
                out.append(self.executable(kind, st, mult, indent))
            else:
                raise WorkoutError(f"Unknown step type '{kind}'. Use warmup, interval, recovery, "
                                   "rest, cooldown, repeat or exercise.")
        return out

    def executable(self, kind: str, st: dict, mult: int, indent: str) -> ExecutableStep:
        tid, disp = _STEP_TYPES[kind]
        end, value, est = _end_condition(st)
        self.seconds += est * mult
        target = _target(st)
        desc = _describe(st)
        self.lines.append(f"{indent}- {kind} {desc}".rstrip())
        return ExecutableStep(
            stepOrder=self.next(),
            stepType={"stepTypeId": tid, "stepTypeKey": kind, "displayOrder": disp},
            endCondition=end, endConditionValue=value or None,
            description=st.get("note"), **target)

    def exercise(self, st: dict, mult: int, indent: str) -> RepeatGroup:
        ex = resolve_exercise(st.get("name") or "")
        sets = int(st.get("sets") or 1)
        rest = float(st.get("rest_seconds") or 60)
        extra: dict[str, Any] = {"category": ex["category"], "exerciseName": ex["exercise"]}
        if st.get("kg"):
            extra["weightValue"] = float(st["kg"]) * 1000
            extra["weightUnit"] = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
        if st.get("seconds"):
            end = {"conditionTypeId": ConditionType.TIME, "conditionTypeKey": "time",
                   "displayOrder": 2, "displayable": True}
            value, work = float(st["seconds"]), float(st["seconds"])
            dose = f"{int(value)}s"
        else:
            reps = int(st.get("reps") or 10)
            end = {"conditionTypeId": ConditionType.REPS, "conditionTypeKey": "reps",
                   "displayOrder": 10, "displayable": True}
            value, work = float(reps), reps * 4.0
            dose = f"{reps} reps"
        self.seconds += sets * (work + rest) * mult
        load = f" @ {st['kg']:g} kg" if st.get("kg") else ""
        self.lines.append(f"{indent}- {ex['name']}: {sets}× {dose}{load}, rest {int(rest)}s")
        group_order = self.next()
        work_step = ExecutableStep(
            stepOrder=self.next(),
            stepType={"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
            endCondition=end, endConditionValue=value,
            targetType={"workoutTargetTypeId": TargetType.NO_TARGET, "workoutTargetTypeKey": "no.target",
                        "displayOrder": 1},
            description=st.get("note"), **extra)
        rest_step = ExecutableStep(
            stepOrder=self.next(),
            stepType={"stepTypeId": StepType.REST, "stepTypeKey": "rest", "displayOrder": 5},
            endCondition={"conditionTypeId": ConditionType.TIME, "conditionTypeKey": "time",
                          "displayOrder": 2, "displayable": True},
            endConditionValue=rest,
            targetType={"workoutTargetTypeId": TargetType.NO_TARGET, "workoutTargetTypeKey": "no.target",
                        "displayOrder": 1})
        return RepeatGroup(
            stepOrder=group_order,
            stepType={"stepTypeId": StepType.REPEAT, "stepTypeKey": "repeat", "displayOrder": 6},
            numberOfIterations=sets, workoutSteps=[work_step, rest_step],
            endCondition={"conditionTypeId": ConditionType.ITERATIONS, "conditionTypeKey": "iterations",
                          "displayOrder": 7, "displayable": False},
            endConditionValue=float(sets))


def _describe(st: dict) -> str:
    parts = []
    if st.get("minutes") is not None:
        parts.append(f"{st['minutes']:g} min")
    elif st.get("seconds") is not None:
        parts.append(f"{st['seconds']:g} s")
    elif st.get("km") is not None:
        parts.append(f"{st['km']:g} km")
    elif st.get("meters") is not None:
        parts.append(f"{st['meters']:g} m")
    else:
        parts.append("until lap press")
    if st.get("hr_zone") is not None:
        parts.append(f"HR Z{st['hr_zone']}")
    elif st.get("hr_bpm"):
        parts.append(f"HR {min(st['hr_bpm'])}-{max(st['hr_bpm'])} bpm")
    elif st.get("power_zone") is not None:
        parts.append(f"power Z{st['power_zone']}")
    elif st.get("watts"):
        parts.append(f"{min(st['watts'])}-{max(st['watts'])} W")
    elif st.get("pace_per_km"):
        parts.append(f"pace {'-'.join(st['pace_per_km'])}/km")
    elif st.get("cadence"):
        parts.append(f"cadence {min(st['cadence'])}-{max(st['cadence'])}")
    if st.get("note"):
        parts.append(f"({st['note']})")
    return " · ".join(parts)


def build(name: str, sport: str, steps: list[dict], description: str | None = None) -> tuple[dict, str]:
    """Return (Garmin workout JSON, human-readable summary)."""
    key = (sport or "").strip().lower().replace(" ", "_")
    if key not in SPORTS:
        raise WorkoutError(f"Unknown sport '{sport}'. Use one of: {', '.join(sorted(set(SPORTS)))}. "
                           "For sports without a Garmin workout type (e.g. motocross, climbing) use 'other' "
                           "or 'cardio'.")
    if not steps:
        raise WorkoutError("A workout needs at least one step.")
    sid, skey = SPORTS[key]
    sport_type = {"sportTypeId": sid, "sportTypeKey": skey, "displayOrder": 1}
    b = _Builder()
    built = b.steps(steps)
    desc = f"{description}\n\n{MARKER}" if description else MARKER
    workout = BaseWorkout(
        workoutName=name[:80], sportType=sport_type, estimatedDurationInSecs=int(b.seconds),
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=sport_type, workoutSteps=built)],
        description=desc[:1000])
    summary = "\n".join([f"**{name}** ({skey}, ~{round(b.seconds / 60)} min)", *b.lines])
    return workout.to_dict(), summary


def schedule(name: str, sport: str, day: date, steps: list[dict],
             description: str | None, athlete_id: int) -> str:
    payload, summary = build(name, sport, steps, description)
    g = get_client()
    created = g.upload_workout(payload)
    wid = created.get("workoutId")
    if not wid:
        raise WorkoutError(f"Garmin did not return a workout id: {str(created)[:200]}")
    g.schedule_workout(wid, day.isoformat())

    from .sync import simplify_steps, sync_calendar  # local import avoids a cycle
    steps_simple = []
    for seg in payload.get("workoutSegments", []):
        steps_simple += simplify_steps(seg.get("workoutSteps", []))
    db.upsert(db.workouts, [{
        "workout_id": wid, "athlete_id": athlete_id, "name": name, "sport": payload["sportType"]["sportTypeKey"],
        "provider": PROVIDER, "description": description, "steps": steps_simple,
    }])
    # Read the calendar back so the local copy has Garmin's real calendar item id.
    sync_calendar(g, athlete_id, day, day)
    item = db.query(select(db.planned.c.calendar_item_id).where(
        db.planned.c.workout_id == wid, db.planned.c.date == day))
    sched_id = item[0]["calendar_item_id"] if item else None
    return (f"✅ Scheduled for {day:%a %d %b %Y} and sent to Garmin Connect "
            f"(workout {wid}, calendar item {sched_id or '?'}). It reaches the watch at its next sync; "
            "start it from Training > Workouts or the calendar on the watch.\n\n" + summary)


def remove(athlete_id: int, calendar_item_id: int) -> str:
    """Unschedule a workout created by this server; coach workouts are never touched."""
    p, w = db.planned, db.workouts
    rows = db.query(select(p, w.c.provider).select_from(p.outerjoin(w, p.c.workout_id == w.c.workout_id))
                    .where(p.c.athlete_id == athlete_id, p.c.calendar_item_id == calendar_item_id))
    if not rows:
        return "Calendar item not found. Use plan_vs_done to see planned workouts and their ids."
    row = rows[0]
    if row["provider"] != PROVIDER:
        return ("This workout was not created by selfcoach-mcp (it may come from your coach or another app), "
                "so it was left untouched. Remove it from Garmin Connect if you really want to.")
    g = get_client()
    g.unschedule_workout(calendar_item_id)
    db.execute(p.delete().where(p.c.calendar_item_id == calendar_item_id))
    # Drop the template too if nothing else on the calendar uses it.
    still_used = db.query(select(p.c.calendar_item_id).where(p.c.workout_id == row["workout_id"]))
    if row["workout_id"] and not still_used:
        try:
            g.delete_workout(row["workout_id"])
            db.execute(w.delete().where(w.c.workout_id == row["workout_id"]))
        except Exception:  # noqa: BLE001
            pass
    return f"🗑️ Removed '{row['title']}' planned for {row['date']:%d/%m}."
