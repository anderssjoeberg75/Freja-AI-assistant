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

from backend.config import DB_FILE, DATABASE_URL
from backend.crypto_utils import encrypt_value, decrypt_value
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

active_db_url = DATABASE_URL
if active_db_url.startswith("postgresql://") or active_db_url.startswith("postgres://"):
    try:
        temp_engine = create_engine(active_db_url, pool_pre_ping=True)
        with temp_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine = temp_engine
    except Exception as _db_err:
        import sys
        print(f"[FREJA DB WARNING] Could not connect to PostgreSQL ({_db_err}). Falling back to SQLite.", file=sys.stderr)
        active_db_url = f"sqlite:///{DB_FILE}"
        engine = create_engine(active_db_url, connect_args={"timeout": 30}, pool_pre_ping=True)
else:
    engine = create_engine(active_db_url, connect_args={"timeout": 30}, pool_pre_ping=True)

DATABASE_URL = active_db_url
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@contextmanager
def get_db_session():
    """Context manager for SQLAlchemy database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class _QmarkCompatCursor:
    """Wraps a psycopg2 cursor so raw SQL written for SQLite's '?' placeholder style keeps
    working unmodified against PostgreSQL. Every `cursor.execute(...)` call site reached
    through `get_db_connection()` (database.py + every `backend/routes/**` module) uses
    '?' purely as a bind-parameter placeholder, never as literal query text - verified by
    inspecting every occurrence before adding this - so a blind '?' -> '%s' substitution on
    the query string is safe. Centralizing the translation here means none of those ~30
    call sites need to know or care which database backend is actually active, instead of
    hand-converting each one (and every future one) to psycopg2's paramstyle.
    """
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=None):
        translated = query.replace("?", "%s")
        if params is None:
            return self._cursor.execute(translated)
        return self._cursor.execute(translated, params)

    def executemany(self, query, seq_of_params):
        return self._cursor.executemany(query.replace("?", "%s"), seq_of_params)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _QmarkCompatConnection:
    """Wraps a raw PostgreSQL connection so `.cursor()` returns a `_QmarkCompatCursor`;
    every other attribute (commit, rollback, close, ...) passes straight through."""
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return _QmarkCompatCursor(self._conn.cursor(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._conn, name)


@contextmanager
def get_db_connection():
    """Context manager for database connections, enabling WAL mode for SQLite or raw connection for PostgreSQL."""
    if DATABASE_URL.startswith("sqlite"):
        conn = sqlite3.connect(DB_FILE, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()
    else:
        conn = engine.raw_connection()
        try:
            yield _QmarkCompatConnection(conn)
        finally:
            conn.close()


def get_db_info():
    """Returns database engine type and status label."""
    if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://"):
        return {
            "type": "postgresql",
            "label": "PostgreSQL",
            "is_sqlite": False
        }
    return {
        "type": "sqlite",
        "label": "WAL Mode (SQLite)",
        "is_sqlite": True
    }


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
    'rouvy_email': 'freja_rouvy_email',
    'rouvy_password': 'freja_rouvy_password',
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


def set_api_key(key_name: str, value: str, user_id: int = 1):
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
                INSERT INTO api_keys (user_id, key_name, key_value)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, key_name) DO UPDATE SET key_value = excluded.key_value
                """,
                (user_id, k, encrypted),
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
        cursor.execute("SELECT key_name, key_value FROM api_keys WHERE user_id = 1 OR user_id IS NULL")
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


def _ensure_columns():
    """Adds any missing columns (e.g. user_id) to existing database tables idempotently."""
    from sqlalchemy import inspect
    try:
        inspector = inspect(engine)
        tables_to_check = [
            'garmin_health', 'garmin_activities', 'garmin_activity_detail', 'garmin_activity_zones',
            'garmin_activity_laps', 'garmin_benchmarks', 'garmin_pushed_workouts', 'strava_activities',
            'withings_measurements', 'fitbit_health', 'google_calendar_events', 'trainer_plans',
            'trainer_profile', 'trainer_bookings', 'trainer_injury_logs', 'trainer_strength_logs',
            'chat_history', 'api_keys'
        ]
        with engine.begin() as conn:
            for table_name in tables_to_check:
                if inspector.has_table(table_name):
                    existing_cols = {c['name'] for c in inspector.get_columns(table_name)}
                    if 'user_id' not in existing_cols:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN user_id BIGINT DEFAULT 1"))
                        print(f"[FREJA] Added missing column {table_name}.user_id.")
    except Exception as err:
        print(f"[FREJA DB WARNING] Column check skipped/failed: {err}")


def init_db():
    """Initializes the database schema and creates tables if they don't exist."""
    from backend.models import Base
    Base.metadata.create_all(bind=engine)
    _ensure_columns()

    with get_db_session() as db:
        # Seed a strong random access token on first start, and rotate away from known weak/legacy defaults.
        LEGACY_WEAK_TOKENS = ('freja_secret', 'freja1234')
        row = db.execute(text("SELECT key_value FROM api_keys WHERE key_name = 'freja_access_token' AND (user_id = 1 OR user_id IS NULL)")).fetchone()
        if row is None or not row[0]:
            new_token = secrets.token_urlsafe(32)
            db.execute(
                text("INSERT INTO api_keys (user_id, key_name, key_value) VALUES (1, 'freja_access_token', :val) ON CONFLICT(user_id, key_name) DO UPDATE SET key_value = EXCLUDED.key_value"),
                {"val": encrypt_value(new_token)}
            )
            db.commit()
            print("[FREJA] Generated a new secure access token.")
        else:
            decrypted_token = decrypt_value(row[0]).strip()
            if decrypted_token in LEGACY_WEAK_TOKENS or row[0] in LEGACY_WEAK_TOKENS:
                new_token = secrets.token_urlsafe(32)
                db.execute(
                    text("UPDATE api_keys SET key_value = :val WHERE key_name = 'freja_access_token'"),
                    {"val": encrypt_value(new_token)}
                )
                db.commit()
                print("[FREJA] Rotated a weak default token to a new random access token.")
