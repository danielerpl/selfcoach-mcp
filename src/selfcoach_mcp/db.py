"""Database schema and access (local SQLite or Postgres/Supabase).

Every table carries `athlete_id`, so one database can hold several athletes.
"""

from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine

from .config import DB_URL, HOME

metadata = MetaData()

athletes = Table(
    "athletes", metadata,
    Column("athlete_id", BigInteger, primary_key=True),
    Column("display_name", String),
    Column("full_name", String),
    Column("role", String, default="athlete"),  # athlete | coach
)

activities = Table(
    "activities", metadata,
    Column("activity_id", BigInteger, primary_key=True),
    Column("athlete_id", BigInteger, index=True, nullable=False),
    Column("date", Date, index=True),
    Column("start_local", String),
    Column("type_key", String),
    Column("name", String),
    Column("duration_s", Float),
    Column("avg_hr", Float),
    Column("max_hr", Float),
    Column("training_load", Float),
    Column("aerobic_te", Float),
    Column("anaerobic_te", Float),
    Column("te_label", String),
    Column("calories", Float),
    Column("lap_count", Integer),
    Column("workout_id", BigInteger),
    Column("hr_z1_s", Float), Column("hr_z2_s", Float), Column("hr_z3_s", Float),
    Column("hr_z4_s", Float), Column("hr_z5_s", Float),
    Column("raw", JSON),
)

workouts = Table(
    "workouts", metadata,
    Column("workout_id", BigInteger, primary_key=True),
    Column("athlete_id", BigInteger, index=True, nullable=False),
    Column("name", String),
    Column("sport", String),
    Column("provider", String),  # "Garmin Clipboard" = creato dal trainer
    Column("description", Text),
    Column("steps", JSON),  # step semplificati
)

planned = Table(
    "planned", metadata,
    Column("calendar_item_id", BigInteger, primary_key=True),
    Column("athlete_id", BigInteger, index=True, nullable=False),
    Column("date", Date, index=True),
    Column("workout_id", BigInteger),
    Column("title", String),
    Column("sport", String),
)

daily = Table(
    "daily", metadata,
    Column("athlete_id", BigInteger, primary_key=True),
    Column("date", Date, primary_key=True),
    Column("rhr", Float),
    Column("rhr_7d", Float),
    Column("stress_avg", Float),
    Column("stress_max", Float),
    Column("bb_high", Float),
    Column("bb_low", Float),
    Column("bb_charged", Float),
    Column("bb_drained", Float),
    Column("bb_latest", Float),
    Column("steps", Integer),
    Column("intensity_min", Integer),
    Column("sleep_s", Float),
    Column("sleep_score", Float),
    Column("hrv_avg", Float),
    Column("hrv_status", String),
    Column("readiness_morning", Float),
    Column("readiness_latest", Float),
    Column("readiness_level", String),
    Column("recovery_time_h", Float),
    Column("acute_load", Float),
    Column("chronic_load", Float),
    Column("acwr", Float),
    Column("acwr_status", String),
    Column("training_status", String),
    Column("load_balance", String),
    Column("vo2max", Float),
)

notes = Table(
    "notes", metadata,
    Column("note_id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", BigInteger, index=True, nullable=False),
    Column("date", Date, index=True),
    Column("activity_id", BigInteger),
    Column("rpe", Integer),  # perceived effort 1-10
    Column("tags", String),  # e.g. "legs:heavy,track:sand"
    Column("text", Text),
    Column("created_at", DateTime(timezone=True)),
)

goals = Table(
    "goals", metadata,
    Column("goal_id", Integer, primary_key=True, autoincrement=True),
    Column("athlete_id", BigInteger, index=True, nullable=False),
    Column("title", String),  # e.g. "Half marathon under 1h45"
    Column("sport", String),
    Column("event_date", Date),
    Column("details", Text),  # target, constraints, available days, equipment...
    Column("active", Boolean, default=True),
    Column("created_at", DateTime(timezone=True)),
)

sync_state = Table(
    "sync_state", metadata,
    Column("athlete_id", BigInteger, primary_key=True),
    Column("key", String, primary_key=True),
    Column("value", String),
)

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        if DB_URL.startswith("sqlite"):
            HOME.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(DB_URL, future=True, pool_pre_ping=True)
        metadata.create_all(_engine)
    return _engine


def upsert(table: Table, rows: Iterable[dict]) -> int:
    """Insert or update by primary key (SQLite and Postgres)."""
    rows = list(rows)
    if not rows:
        return 0
    eng = engine()
    if eng.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    pk = [c.name for c in table.primary_key.columns]
    with eng.begin() as conn:
        for row in rows:
            stmt = insert(table).values(**row)
            updates = {k: stmt.excluded[k] for k in row if k not in pk}
            stmt = stmt.on_conflict_do_update(index_elements=pk, set_=updates) if updates \
                else stmt.on_conflict_do_nothing(index_elements=pk)
            conn.execute(stmt)
    return len(rows)


def query(stmt) -> list[dict]:
    with engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(stmt)]


def insert_row(table: Table, row: dict) -> None:
    with engine().begin() as conn:
        conn.execute(table.insert().values(**row))


def execute(stmt) -> None:
    with engine().begin() as conn:
        conn.execute(stmt)


def get_state(athlete_id: int, key: str) -> str | None:
    rows = query(select(sync_state.c.value).where(
        sync_state.c.athlete_id == athlete_id, sync_state.c.key == key))
    return rows[0]["value"] if rows else None


def set_state(athlete_id: int, key: str, value: str) -> None:
    upsert(sync_state, [{"athlete_id": athlete_id, "key": key, "value": value}])


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
