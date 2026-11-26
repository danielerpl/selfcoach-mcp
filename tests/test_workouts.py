import pytest

from selfcoach_mcp import workouts


def test_interval_running_workout():
    payload, summary = workouts.build("5x3' Z4", "running", [
        {"type": "warmup", "minutes": 15, "hr_zone": 2},
        {"type": "repeat", "times": 5, "steps": [
            {"type": "interval", "minutes": 3, "hr_zone": 4},
            {"type": "recovery", "minutes": 2, "hr_zone": 1},
        ]},
        {"type": "cooldown", "minutes": 10},
    ])
    assert payload["sportType"]["sportTypeKey"] == "running"
    steps = payload["workoutSegments"][0]["workoutSteps"]
    assert [s["stepType"]["stepTypeKey"] for s in steps] == ["warmup", "repeat", "cooldown"]
    assert steps[1]["numberOfIterations"] == 5
    assert steps[1]["workoutSteps"][0]["zoneNumber"] == 4
    orders = [steps[0]["stepOrder"], steps[1]["stepOrder"], *[s["stepOrder"] for s in steps[1]["workoutSteps"]],
              steps[2]["stepOrder"]]
    assert len(set(orders)) == len(orders)
    assert payload["estimatedDurationInSecs"] == (15 + 25 + 10) * 60
    assert workouts.MARKER in payload["description"]
    assert "5×" in summary


def test_pace_target_is_ordered_in_mps():
    payload, _ = workouts.build("Tempo", "run", [{"type": "interval", "km": 5, "pace_per_km": ["5:00", "5:20"]}])
    s = payload["workoutSegments"][0]["workoutSteps"][0]
    assert s["targetValueOne"] < s["targetValueTwo"]
    assert s["endCondition"]["conditionTypeKey"] == "distance"


def test_strength_resolves_catalog_names():
    payload, summary = workouts.build("Legs", "strength", [
        {"type": "exercise", "name": "barbell back squat", "sets": 4, "reps": 8, "kg": 60, "rest_seconds": 90},
    ])
    group = payload["workoutSegments"][0]["workoutSteps"][0]
    work = group["workoutSteps"][0]
    assert group["numberOfIterations"] == 4
    assert (work["category"], work["exerciseName"]) == ("SQUAT", "BARBELL_BACK_SQUAT")
    assert work["weightValue"] == 60000
    assert "Barbell Back Squat: 4× 8 reps @ 60 kg" in summary


def test_unknown_exercise_suggests_matches():
    with pytest.raises(workouts.WorkoutError, match="Closest"):
        workouts.build("x", "strength", [{"type": "exercise", "name": "squat jumpz"}])


def test_unknown_sport_is_explained():
    with pytest.raises(workouts.WorkoutError, match="other"):
        workouts.build("x", "motocross", [{"type": "interval", "minutes": 20}])


def test_remove_never_touches_coach_workouts():
    from datetime import date

    from conftest import AID
    from selfcoach_mcp import db

    db.upsert(db.workouts, [{"workout_id": 9, "athlete_id": AID, "name": "Coach intervals",
                             "provider": "Garmin Clipboard", "steps": []}])
    db.upsert(db.planned, [{"calendar_item_id": 99, "athlete_id": AID, "date": date(2026, 6, 1),
                            "workout_id": 9, "title": "Coach intervals"}])
    out = workouts.remove(AID, 99)  # returns before any Garmin call
    assert "left untouched" in out
