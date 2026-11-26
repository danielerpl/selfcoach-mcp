import os
import tempfile

# Point the package at a throwaway home before it is imported.
_tmp = tempfile.mkdtemp(prefix="selfcoach-mcp-test-")
os.environ["SELFCOACH_HOME"] = _tmp
os.environ["SELFCOACH_DB_URL"] = f"sqlite:///{_tmp}/test.db"

from datetime import date, timedelta  # noqa: E402

import pytest  # noqa: E402

from selfcoach_mcp import db  # noqa: E402

AID = 1


@pytest.fixture(autouse=True)
def clean_db():
    eng = db.engine()
    db.metadata.drop_all(eng)
    db.metadata.create_all(eng)
    yield


def activity(i: int, day: date, **kw) -> dict:
    row = {
        "activity_id": i, "athlete_id": AID, "date": day, "start_local": f"{day}T08:00:00",
        "type_key": "running", "name": f"Run {i}", "duration_s": 3600, "avg_hr": 140, "max_hr": 170,
        "training_load": 100, "aerobic_te": 3.0, "anaerobic_te": 1.0, "te_label": "BASE",
        "calories": 600, "lap_count": 1, "workout_id": None,
        "hr_z1_s": 600, "hr_z2_s": 2400, "hr_z3_s": 600, "hr_z4_s": 0, "hr_z5_s": 0, "raw": {},
    }
    row.update(kw)
    return row


def daily(day: date, **kw) -> dict:
    row = {"athlete_id": AID, "date": day, "rhr": 50, "sleep_s": 8 * 3600, "readiness_latest": 75,
           "readiness_morning": 75, "bb_high": 85, "acwr": 1.0, "hrv_status": "BALANCED",
           "stress_avg": 25, "recovery_time_h": 0}
    row.update(kw)
    return row


def fill_baseline(end: date, days: int = 28, **kw) -> None:
    db.upsert(db.daily, [daily(end - timedelta(days=i), **kw) for i in range(1, days + 1)])
