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

class StravaActivity(Base):
    __tablename__ = 'strava_activities'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    type = Column(String)
    date = Column(String)
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
    date = Column(String)
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
    
    __table_args__ = (
        CheckConstraint('id = 1', name='chk_single_row'),
    )

class TrainerBooking(Base):
    __tablename__ = 'trainer_bookings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer)
    event_id = Column(Integer)
    workout_date = Column(String)
    week = Column(Integer, default=0)
