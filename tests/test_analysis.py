from datetime import date, timedelta

from conftest import AID, activity, daily, fill_baseline
from selfcoach_mcp import analysis, db
from selfcoach_mcp.sync import simplify_steps

TODAY = date(2026, 6, 15)


def test_match_plan_prefers_workout_id():
    planned = [{"date": TODAY, "workout_id": 7, "title": "Intervals"}]
    acts = [activity(1, TODAY, name="Morning run"), activity(2, TODAY + timedelta(days=1), workout_id=7)]
    pairs, free = analysis.match_plan(planned, acts)
    assert pairs[0][1]["activity_id"] == 2
    assert [a["activity_id"] for a in free] == [1]


def test_simplify_steps_multiplies_repeats():
    steps = [{"stepType": {"stepTypeKey": "repeat"}, "numberOfIterations": 3, "workoutSteps": [
        {"stepType": {"stepTypeKey": "interval"}, "endCondition": {"conditionTypeKey": "reps"},
         "endConditionValue": 10, "category": "SQUAT"},
        {"stepType": {"stepTypeKey": "rest"}},
    ]}]
    out = simplify_steps(steps)
    assert out == [{"kind": "interval", "exercise": "SQUAT", "category": "SQUAT", "sets": 3, "reps": 10,
                    "time_s": None, "weight_kg": None, "target": None, "note": None}]


def test_verdict_green_when_recovered():
    verdict, reasons = analysis.readiness_verdict(daily(TODAY), [daily(TODAY - timedelta(days=1))], [], TODAY)
    assert verdict.startswith("🟢") and not reasons


def test_verdict_rest_with_two_red_flags():
    today = daily(TODAY, rhr=58, readiness_latest=20)
    base = [daily(TODAY - timedelta(days=i)) for i in range(1, 15)]
    verdict, reasons = analysis.readiness_verdict(today, base, [], TODAY)
    assert verdict.startswith("🔴")
    assert any("resting HR" in r for r in reasons)


def test_verdict_counts_hard_sessions_and_streak():
    recent = [activity(i, TODAY - timedelta(days=i), hr_z4_s=900) for i in range(1, 8)]
    verdict, reasons = analysis.readiness_verdict(daily(TODAY), [], recent, TODAY)
    assert any("hard sessions" in r for r in reasons)
    assert any("in a row" in r for r in reasons)
    assert not verdict.startswith("🟢")


def test_phase():
    assert analysis.phase(TODAY + timedelta(days=5), TODAY).startswith("race week")
    assert analysis.phase(TODAY + timedelta(days=60), TODAY).startswith("build")
    assert analysis.phase(TODAY + timedelta(days=200), TODAY).startswith("base")


def test_today_status_end_to_end():
    fill_baseline(TODAY)
    db.upsert(db.daily, [daily(TODAY)])
    db.upsert(db.activities, [activity(1, TODAY - timedelta(days=1))])
    analysis.set_goal(AID, "10k under 45'", TODAY + timedelta(days=50), "running", None, False)
    out = analysis.today_status(AID, TODAY)
    assert "Suggestion: 🟢" in out
    assert "10k under 45'" in out and "build" in out


def test_insights_flag_grey_zone_and_monotony():
    end = date.today()
    db.upsert(db.activities, [activity(i, end - timedelta(days=i), hr_z1_s=0, hr_z2_s=600, hr_z3_s=3000)
                              for i in range(0, 7)])
    text = "\n".join(analysis.insights(AID, end))
    assert "grey zone" in text
    assert "Rest days (14 d):** 7" in text


def test_chart_data_is_json():
    import json
    db.upsert(db.activities, [activity(1, TODAY)])
    out = json.loads(analysis.chart_data(AID, TODAY - timedelta(days=7), TODAY))
    assert out["activities"][0]["sport"] == "Running"


def test_verdict_after_long_session_today_says_done():
    done = [activity(1, TODAY, type_key="strength_training", duration_s=95 * 60)]
    verdict, reasons = analysis.readiness_verdict(daily(TODAY, acwr=0.5), [], done, TODAY)
    assert verdict.startswith("✅ DONE")
    assert any("already trained today" in r for r in reasons)


def test_verdict_after_short_easy_session_caps_intensity():
    done = [activity(1, TODAY, duration_s=30 * 60)]
    verdict, _ = analysis.readiness_verdict(daily(TODAY), [], done, TODAY)
    assert verdict.startswith("Already trained today") and "HARD" not in verdict
