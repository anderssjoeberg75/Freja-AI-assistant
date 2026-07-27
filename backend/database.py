"""Database schema initialization and migrations.

Everything lives in one SQLite file (`keys.db`). Two things are worth knowing before
debugging anything here:

  * WAL journal mode is enabled per connection, so concurrent readers never block the
    single writer. That is what lets a background sync task write while an HTTP request reads.
  * Values in `api_keys` are encrypted at rest (see `backend/crypto_utils.py`). Reading a row
    directly with the `sqlite3` CLI therefore shows ciphertext, not the key - use
    `get_api_key()` instead.
"""

import datetime
import secrets
import sqlite3
from contextlib import contextmanager

from backend.config import DB_FILE
from backend.crypto_utils import encrypt_value, decrypt_value
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine(f"sqlite:///{DB_FILE}", connect_args={"timeout": 30})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@contextmanager
def get_db_session():
    """Context manager for SQLAlchemy database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_connection():
    """Context manager for SQLite database connections, enabling WAL mode and timeout."""
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


# Legacy setting names kept working after the `freja_`-prefixed rename. `get_api_key()`
# falls back through this map in both directions, so an old database and a new one both
# resolve the same value without a migration.
KEY_ALIASES = {
    'telegram_bot_token': 'freja_telegram_bot_token',
    'telegram_chat_id': 'freja_telegram_chat_id',
    'gemini_api_key': 'freja_gemini_apikey',
    'elevenlabs_api_key': 'freja_eleven_apikey',
    'mem0_api_key': 'freja_mem0_apikey',
    'garmin_email': 'freja_garmin_email',
    'garmin_password': 'freja_garmin_password',
    'strava_client_id': 'freja_strava_client_id',
    'strava_client_secret': 'freja_strava_client_secret',
    'strava_refresh_token': 'freja_strava_refresh_token',
    'withings_client_id': 'freja_withings_client_id',
    'withings_client_secret': 'freja_withings_client_secret',
    'withings_refresh_token': 'freja_withings_refresh_token',
    'fitbit_client_id': 'freja_fitbit_client_id',
    'fitbit_client_secret': 'freja_fitbit_client_secret',
    'fitbit_refresh_token': 'freja_fitbit_refresh_token',
    'google_calendar_client_id': 'freja_google_calendar_client_id',
    'google_calendar_client_secret': 'freja_google_calendar_client_secret',
    'google_calendar_refresh_token': 'freja_google_calendar_refresh_token',
    'claude_api_key': 'freja_claude_apikey',
    'ollama_base_url': 'freja_ollama_base_url',
    'ollama_model': 'freja_ollama_model',
}

REVERSE_KEY_ALIASES = {v: k for k, v in KEY_ALIASES.items()}

def get_api_key(key_name: str):
    """Fetches and decrypts a single value from the api_keys table. Checks aliases if absent."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key_value FROM api_keys WHERE key_name = ?", (key_name,))
        row = cursor.fetchone()
        if not row or row[0] is None:
            alt_key = KEY_ALIASES.get(key_name) or REVERSE_KEY_ALIASES.get(key_name)
            if alt_key:
                cursor.execute("SELECT key_value FROM api_keys WHERE key_name = ?", (alt_key,))
                row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    return decrypt_value(row[0]).strip()


def set_api_key(key_name: str, value: str):
    """Encrypts and upserts a value into api_keys table. Also saves alias key for backward compatibility."""
    encrypted = encrypt_value(value)
    keys_to_set = [key_name]
    alt_key = KEY_ALIASES.get(key_name) or REVERSE_KEY_ALIASES.get(key_name)
    if alt_key:
        keys_to_set.append(alt_key)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for k in keys_to_set:
            cursor.execute(
                """
                INSERT INTO api_keys (key_name, key_value)
                VALUES (?, ?)
                ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
                """,
                (k, encrypted),
            )
        conn.commit()


def get_all_api_keys(unmask: bool = False) -> dict:
    """Returns every stored key, keyed by key_name (used by the settings endpoint).
    Sensitive values (API keys, client secrets, passwords, and tokens) are masked automatically
    unless unmask is set to True.
    """
    sensitive_keywords = {"secret", "token", "password", "apikey", "api_key", "email"}
    non_sensitive_keys = {
        "freja_instagram_business_account_id",
        "freja_instagram_username",
        "last_sync_garmin",
        "last_sync_google_calendar"
    }

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key_name, key_value FROM api_keys")
        rows = cursor.fetchall()
    
    result = {}
    for name, value in rows:
        decrypted = decrypt_value(value).strip() if value else ""
        
        name_lower = name.lower()
        is_sensitive = (
            any(kw in name_lower for kw in sensitive_keywords)
            and name not in non_sensitive_keys
        )
        
        if decrypted and is_sensitive and not unmask:
            result[name] = "••••••••"
        else:
            result[name] = decrypted
    return result


def _ensure_columns(cursor, table: str, columns: list):
    """Adds any missing columns to an existing table (SQLite ALTER ADD COLUMN).

    `Base.metadata.create_all` creates missing tables but never alters existing ones, so
    columns added to a model after a database already exists would otherwise be missing.
    This backfills them idempotently. `columns` is a list of (name, sql_type) tuples."""
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    for name, sql_type in columns:
        if name not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
            print(f"[FREJA] Added missing column {table}.{name} ({sql_type}).")


def init_db():
    """Initializes the SQLite database and creates the keys and other tables if they don't exist."""
    from backend.models import Base
    Base.metadata.create_all(bind=engine)

    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()

    # Backfill columns added to garmin_health after the initial schema (stress + sleep stages).
    _ensure_columns(cursor, "garmin_health", [
        ("stress_avg", "INTEGER"),
        ("stress_max", "INTEGER"),
        ("sleep_deep_hours", "REAL"),
        ("sleep_light_hours", "REAL"),
        ("sleep_rem_hours", "REAL"),
        ("sleep_awake_hours", "REAL"),
        ("vo2max", "REAL"),
        ("intensity_minutes", "INTEGER"),
        ("sleep_score", "INTEGER"),
        ("training_load_acute", "REAL"),
        ("training_load_chronic", "REAL"),
        ("acwr", "REAL"),
        ("acwr_status", "TEXT"),
        ("load_aerobic_low", "REAL"),
        ("load_aerobic_high", "REAL"),
        ("load_anaerobic", "REAL"),
        ("training_readiness", "INTEGER"),
        ("training_readiness_level", "TEXT"),
        ("training_readiness_feedback", "TEXT"),
    ])

    # Backfill the detail-fetch marker, raw type key and lap count added to garmin_activities
    # (#182/#183/#185).
    _ensure_columns(cursor, "garmin_activities", [
        ("detail_fetched_at", "TEXT"),
        ("raw_type_key", "TEXT"),
        ("lap_count", "INTEGER"),
    ])

    # Backfill source/activity_id added to trainer_strength_logs for Garmin auto-import
    # (Issue #183). Existing rows have no source recorded - default them to 'manual' so
    # nothing already logged is mistaken for a Garmin import.
    _ensure_columns(cursor, "trainer_strength_logs", [
        ("source", "TEXT"),
        ("activity_id", "TEXT"),
    ])
    cursor.execute("UPDATE trainer_strength_logs SET source = 'manual' WHERE source IS NULL")

    # Backfill the baselines_updated_at column added to trainer_profile for the
    # weekly baseline auto-update (Issue #35).
    _ensure_columns(cursor, "trainer_profile", [
        ("baselines_updated_at", "TEXT"),
    ])

    # Backfill indexes for existing databases (issue #65, #162, #163)
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_trainer_bookings_workout_date ON trainer_bookings (workout_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_trainer_bookings_plan_id ON trainer_bookings (plan_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_strava_activities_date ON strava_activities (date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_garmin_activities_date ON garmin_activities (date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_trainer_plans_date ON trainer_plans (date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_trainer_injury_logs_date ON trainer_injury_logs (date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_trainer_strength_logs_date ON trainer_strength_logs (date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_fitbit_health_date ON fitbit_health (date)")

    # Clean up legacy demo/seed rows if present in the database so fake test data
    # (e.g. "Lunch med Maria", "Läkarbesök", dummy Strava activities) never persists.
    cursor.execute("DELETE FROM google_calendar_events WHERE summary IN ('Möte med Sven', 'Lunch med Maria', 'Designgenomgång', 'Läkarbesök')")
    cursor.execute("""
        DELETE FROM strava_activities
        WHERE id < 0 OR name IN (
            'Morgonlöpning i skogen', 'Distanscykling landsväg', 'Intervallpass bana',
            'Morgonlöpning i parken', 'Kvällscykling', 'Styrkepass - Ben & Bål',
            'Snabbdistans Löpning', 'Aktiv återhämtning Promenad'
        )
    """)
    cursor.execute("DELETE FROM withings_measurements WHERE date BETWEEN '2026-06-13' AND '2026-06-19' AND weight = 78.5 AND fat_ratio = 18.2")
    cursor.execute("DELETE FROM garmin_health WHERE date BETWEEN '2026-06-13' AND '2026-06-19' AND steps IN (10450, 8200, 12100, 9300, 11000, 7100, 8900)")
    # Seed a strong random access token on first start, and rotate away from known weak/legacy defaults.
    LEGACY_WEAK_TOKENS = ('freja_secret', 'freja1234')
    cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_access_token'")
    row = cursor.fetchone()
    if row is None or not row[0]:
        new_token = secrets.token_urlsafe(32)
        cursor.execute("INSERT INTO api_keys (key_name, key_value) VALUES ('freja_access_token', ?)", (encrypt_value(new_token),))
        print(f"[FREJA] Generated a new access token: {new_token}")
    else:
        decrypted_token = decrypt_value(row[0]).strip()
        if decrypted_token in LEGACY_WEAK_TOKENS or row[0] in LEGACY_WEAK_TOKENS:
            new_token = secrets.token_urlsafe(32)
            cursor.execute("UPDATE api_keys SET key_value = ? WHERE key_name = 'freja_access_token'", (encrypt_value(new_token),))
            print(f"[FREJA] Rotated a weak default token to a new random access token: {new_token}")
    conn.commit()
    conn.close()
