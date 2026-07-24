from sqlalchemy import Column, Integer, String, Float, CheckConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class ApiKey(Base):
    __tablename__ = 'api_keys'
    key_name = Column(String, primary_key=True)
    key_value = Column(String)

class ChatHistory(Base):
    __tablename__ = 'chat_history'
    id = Column(Integer, primary_key=True, autoincrement=True)
    sender = Column(String)
    content = Column(String)
    timestamp = Column(String)
    channel = Column(String)

class CodexAuditLog(Base):
    __tablename__ = 'codex_audit_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(String)
    tool = Column(String)
    command = Column(String)
    exit_code = Column(Integer)
    detail = Column(String)

class LearnedKnowledge(Base):
    __tablename__ = 'learned_knowledge'
    id = Column(Integer, primary_key=True, autoincrement=True)
    topic = Column(String, unique=True)
    summary = Column(String)
    detailed_notes = Column(String)
    sources = Column(String)
    timestamp = Column(String)

class GarminHealth(Base):
    __tablename__ = 'garmin_health'
    date = Column(String, primary_key=True)
    steps = Column(Integer)
    sleep_hours = Column(Float)
    resting_hr = Column(Integer)
    active_calories = Column(Integer)
    workout_type = Column(String)
    workout_duration = Column(Integer)
    body_battery = Column(Integer)
    hrv = Column(Integer)
    recovery_time = Column(Integer)
    training_status = Column(String)
    # Stress (Garmin all-day stress; Garmin returns -1/-2 for "no reading", stored as NULL)
    stress_avg = Column(Integer)
    stress_max = Column(Integer)
    # Sleep stages (hours), from the same nightly sleep record as sleep_hours
    sleep_deep_hours = Column(Float)
    sleep_light_hours = Column(Float)
    sleep_rem_hours = Column(Float)
    sleep_awake_hours = Column(Float)
    # Fitness / activity extras
    vo2max = Column(Float)                 # latest VO2max estimate
    intensity_minutes = Column(Integer)    # daily intensity minutes (vigorous weighted x2, Garmin style)
    sleep_score = Column(Integer)          # Garmin overall sleep score (0-100)
    # Garmin's own training load (see #179). TSB ("form") is deliberately NOT stored - it is
    # always chronic - acute, computed on read, so it can never drift out of sync with them.
    training_load_acute = Column(Float)    # ATL - acute/short-term load ("fatigue")
    training_load_chronic = Column(Float)  # CTL - chronic/long-term load ("fitness")
    acwr = Column(Float)                   # acute:chronic workload ratio - injury-risk signal
    acwr_status = Column(String)           # Garmin's own classification of the ratio
    load_aerobic_low = Column(Float)       # monthly low-aerobic load vs Garmin's target band
    load_aerobic_high = Column(Float)      # monthly high-aerobic load vs Garmin's target band
    load_anaerobic = Column(Float)         # monthly anaerobic load vs Garmin's target band

class GarminActivity(Base):
    """One Garmin Connect activity, keyed on Garmin's own activity_id so a re-synced window
    (recent sync + backfill chunk can overlap) upserts instead of duplicating (see #177).
    garmin_health.workout_type/workout_duration stays a same-day rollup derived from these
    rows rather than being migrated away, since existing HUD reads and seed data depend on it.
    """
    __tablename__ = 'garmin_activities'
    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(String, unique=True, index=True)
    date = Column(String, index=True)
    start_time_local = Column(String)
    type = Column(String)
    name = Column(String)
    duration_minutes = Column(Float)
    distance_m = Column(Float)
    avg_hr = Column(Float)
    max_hr = Column(Float)
    calories = Column(Float)
    training_load = Column(Float)
    aerobic_te = Column(Float)
    anaerobic_te = Column(Float)

class StravaActivity(Base):
    __tablename__ = 'strava_activities'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    type = Column(String)
    date = Column(String, index=True)
    distance = Column(Float)
    moving_time = Column(Integer)
    elapsed_time = Column(Integer)
    total_elevation_gain = Column(Float)
    average_speed = Column(Float)
    max_speed = Column(Float)
    average_heartrate = Column(Float)
    max_heartrate = Column(Float)
    calories = Column(Float)

class WithingsMeasurement(Base):
    __tablename__ = 'withings_measurements'
    date = Column(String, primary_key=True)
    weight = Column(Float)
    fat_ratio = Column(Float)
    bone_mass = Column(Float)
    heart_pulse = Column(Float)
    sleep_duration = Column(Integer)
    sleep_deep = Column(Integer)
    sleep_rem = Column(Integer)
    steps = Column(Integer)
    distance = Column(Float)
    calories = Column(Float)
    elevation = Column(Float)
    sleep_score = Column(Integer)

class GoogleCalendarEvent(Base):
    __tablename__ = 'google_calendar_events'
    id = Column(Integer, primary_key=True, autoincrement=True)
    google_event_id = Column(String, unique=True)
    summary = Column(String)
    description = Column(String)
    start_time = Column(String)
    end_time = Column(String)
    location = Column(String)

class TrainerPlan(Base):
    __tablename__ = 'trainer_plans'
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String, index=True)
    goal = Column(String)
    advice_text = Column(String)
    limitations = Column(String)

class TrainerProfile(Base):
    __tablename__ = 'trainer_profile'
    id = Column(Integer, primary_key=True)
    event = Column(String)
    event_date = Column(String)
    fitness_level = Column(String)
    availability = Column(String)
    goals = Column(String)
    limitations = Column(String)
    location = Column(String)
    baseline_resting_hr = Column(Float)
    baseline_sleep_hours = Column(Float)
    baseline_hrv = Column(Float)
    updated_at = Column(String)
    auto_adjust = Column(Integer, default=1)
    # Timestamp of the last automatic baseline recompute (see recompute_health_baselines).
    # Kept separate from updated_at, which any profile save touches, so the weekly
    # cadence is driven only by actual baseline refreshes.
    baselines_updated_at = Column(String)

    __table_args__ = (
        CheckConstraint('id = 1', name='chk_single_row'),
    )

class TrainerBooking(Base):
    __tablename__ = 'trainer_bookings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, index=True)
    event_id = Column(Integer)
    workout_date = Column(String, index=True)
    week = Column(Integer, default=0)

class TrainerInjuryLog(Base):
    """One injury / pain entry, tracked over time so recurring niggles are visible.

    The profile's free-text `limitations` field only says what is true today; this table
    keeps a dated history (see Issue #38). Rows with status='active' are fed into plan
    generation and the recovery optimizer so affected sessions get eased or swapped.
    """
    __tablename__ = 'trainer_injury_logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String, index=True)          # YYYY-MM-DD the problem was noted
    area = Column(String)          # body area, e.g. "Höger knä"
    severity = Column(Integer)     # 1-10, how limiting it is right now
    note = Column(String)
    status = Column(String, default='active')   # 'active' or 'resolved'
    resolved_date = Column(String)              # YYYY-MM-DD, set when status='resolved'
    created_at = Column(String)


class TrainerStrengthLog(Base):
    """One logged strength set/exercise result, used to drive progressive overload.

    The coach reads recent rows to progress load week to week (see Issue #34). Each
    row is one exercise performed on a given date; `plan_id` links it back to the plan
    the session came from when known (nullable for ad-hoc logs)."""
    __tablename__ = 'trainer_strength_logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String, index=True)          # YYYY-MM-DD the set was performed
    exercise_name = Column(String)
    sets = Column(Integer)
    reps = Column(Integer)
    weight = Column(Float)         # load in kg (0/None for bodyweight)
    rpe = Column(Float)            # rate of perceived exertion (1-10), optional
    notes = Column(String)
    plan_id = Column(Integer)      # source plan, nullable
    created_at = Column(String)
