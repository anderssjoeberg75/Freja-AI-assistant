"""Database schema initialization and migrations."""

import datetime
import secrets
import sqlite3
from contextlib import contextmanager

from backend.config import DB_FILE
from backend.crypto_utils import encrypt_value, decrypt_value


@contextmanager
def get_db_connection():
    """Context manager for SQLite database connections, enabling WAL mode and timeout."""
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


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
    'google_calendar_client_id': 'freja_google_calendar_client_id',
    'google_calendar_client_secret': 'freja_google_calendar_client_secret',
    'google_calendar_refresh_token': 'freja_google_calendar_refresh_token',
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


def get_all_api_keys() -> dict:
    """Returns every stored key, decrypted, keyed by key_name (used by the settings endpoint)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key_name, key_value FROM api_keys")
        rows = cursor.fetchall()
    return {name: (decrypt_value(value).strip() if value else "") for name, value in rows}


def init_db():
    """Initializes the SQLite database and creates the keys and garmin_health tables if they don't exist."""
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()
    cursor.execute('\n        CREATE TABLE IF NOT EXISTS api_keys (\n            key_name TEXT PRIMARY KEY,\n            key_value TEXT\n        )\n    ')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            content TEXT,
            timestamp TEXT,
            channel TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS learned_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT UNIQUE,
            summary TEXT,
            detailed_notes TEXT,
            sources TEXT,
            timestamp TEXT
        )
    ''')
    cursor.execute('\n        CREATE TABLE IF NOT EXISTS garmin_health (\n            date TEXT PRIMARY KEY,\n            steps INTEGER,\n            sleep_hours REAL,\n            resting_hr INTEGER,\n            active_calories INTEGER,\n            workout_type TEXT,\n            workout_duration INTEGER,\n            body_battery INTEGER,\n            hrv INTEGER,\n            recovery_time INTEGER,\n            training_status TEXT\n        )\n    ')
    cursor.execute('\n        CREATE TABLE IF NOT EXISTS strava_activities (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            name TEXT,\n            type TEXT,\n            date TEXT,\n            distance REAL,\n            moving_time INTEGER,\n            elapsed_time INTEGER,\n            total_elevation_gain REAL,\n            average_speed REAL,\n            max_speed REAL,\n            average_heartrate REAL,\n            max_heartrate REAL,\n            calories REAL\n        )\n    ')
    cursor.execute('\n        CREATE TABLE IF NOT EXISTS withings_measurements (\n            date TEXT PRIMARY KEY,\n            weight REAL,\n            fat_ratio REAL,\n            bone_mass REAL,\n            heart_pulse REAL,\n            sleep_duration INTEGER,\n            sleep_deep INTEGER,\n            sleep_rem INTEGER,\n            steps INTEGER,\n            distance REAL,\n            calories REAL,\n            elevation REAL,\n            sleep_score INTEGER\n        )\n    ')
    cursor.execute('\n        CREATE TABLE IF NOT EXISTS google_calendar_events (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            google_event_id TEXT UNIQUE,\n            summary TEXT,\n            description TEXT,\n            start_time TEXT,\n            end_time TEXT,\n            location TEXT\n        )\n    ')
    cursor.execute('\n        CREATE TABLE IF NOT EXISTS trainer_plans (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            date TEXT,\n            goal TEXT,\n            advice_text TEXT,\n            limitations TEXT\n        )\n    ')
    try:
        cursor.execute('ALTER TABLE trainer_plans ADD COLUMN limitations TEXT')
    except sqlite3.OperationalError:
        pass
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trainer_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            event TEXT,
            event_date TEXT,
            fitness_level TEXT,
            availability TEXT,
            goals TEXT,
            limitations TEXT,
            location TEXT,
            baseline_resting_hr REAL,
            baseline_sleep_hours REAL,
            baseline_hrv REAL,
            updated_at TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trainer_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER,
            event_id INTEGER,
            workout_date TEXT,
            week INTEGER DEFAULT 0
        )
    ''')
    try:
        cursor.execute('ALTER TABLE trainer_profile ADD COLUMN auto_adjust INTEGER DEFAULT 1')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE garmin_health ADD COLUMN body_battery INTEGER')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE garmin_health ADD COLUMN hrv INTEGER')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE garmin_health ADD COLUMN recovery_time INTEGER')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE garmin_health ADD COLUMN training_status TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE withings_measurements ADD COLUMN sleep_duration INTEGER')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE withings_measurements ADD COLUMN sleep_deep INTEGER')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE withings_measurements ADD COLUMN sleep_rem INTEGER')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE withings_measurements ADD COLUMN steps INTEGER')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE withings_measurements ADD COLUMN distance REAL')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE withings_measurements ADD COLUMN calories REAL')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE withings_measurements ADD COLUMN elevation REAL')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE withings_measurements ADD COLUMN sleep_score INTEGER')
    except sqlite3.OperationalError:
        pass
    cursor.execute('SELECT COUNT(*) FROM garmin_health')
    if cursor.fetchone()[0] == 0:
        today = datetime.date.today()
        seed_data = [(today - datetime.timedelta(days=1), 10450, 7.5, 58, 450, 'Löpning', 45, 80, 65, 12, 'Productive'), (today - datetime.timedelta(days=2), 8200, 6.8, 60, 200, None, 0, 75, 62, 0, 'Maintaining'), (today - datetime.timedelta(days=3), 12100, 8.2, 57, 600, 'Cykling', 60, 85, 66, 18, 'Productive'), (today - datetime.timedelta(days=4), 9300, 7.0, 59, 350, 'Styrketräning', 40, 70, 60, 8, 'Maintaining'), (today - datetime.timedelta(days=5), 11000, 7.8, 58, 400, 'Löpning', 50, 78, 64, 15, 'Productive'), (today - datetime.timedelta(days=6), 7100, 6.5, 61, 150, None, 0, 65, 58, 0, 'Maintaining'), (today - datetime.timedelta(days=7), 8900, 7.2, 60, 300, 'Yoga', 30, 72, 61, 2, 'Maintaining')]
        cursor.executemany('\n            INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status)\n            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n        ', [(d.strftime('%Y-%m-%d'), s, sl, r, c, wt, wd, bb, h, rt, ts) for d, s, sl, r, c, wt, wd, bb, h, rt, ts in seed_data])
    cursor.execute('SELECT COUNT(*) FROM strava_activities')
    if cursor.fetchone()[0] == 0:
        today = datetime.date.today()
        strava_seed = [('Morgonlöpning i skogen', 'Löpning', (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d'), 8200.0, 2700, 2850, 45.0, 3.04, 4.2, 145.0, 165.0, 450.0), ('Distanscykling landsväg', 'Cykling', (today - datetime.timedelta(days=3)).strftime('%Y-%m-%d'), 22500.0, 3600, 3800, 180.0, 6.25, 9.5, 135.0, 155.0, 600.0), ('Intervallpass bana', 'Löpning', (today - datetime.timedelta(days=5)).strftime('%Y-%m-%d'), 9100.0, 3000, 3200, 50.0, 3.03, 5.1, 148.0, 170.0, 400.0)]
        cursor.executemany('\n            INSERT INTO strava_activities (name, type, date, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories)\n            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n        ', strava_seed)
    cursor.execute('SELECT COUNT(*) FROM withings_measurements')
    if cursor.fetchone()[0] == 0:
        today = datetime.date.today()
        withings_seed = [((today - datetime.timedelta(days=1)).strftime('%Y-%m-%d'), 78.5, 18.2, 3.4, 56.0, 27600, 7200, 3600, 8500, 6200.0, 450.0, 15.0, 85), ((today - datetime.timedelta(days=2)).strftime('%Y-%m-%d'), 78.6, 18.3, 3.4, 58.0, 28200, 7500, 3900, 9200, 6800.0, 480.0, 20.0, 88), ((today - datetime.timedelta(days=3)).strftime('%Y-%m-%d'), 78.3, 18.1, 3.4, 55.0, 25800, 6600, 3300, 7800, 5600.0, 410.0, 10.0, 80), ((today - datetime.timedelta(days=4)).strftime('%Y-%m-%d'), 78.8, 18.4, 3.4, 57.0, 26400, 6900, 3500, 8900, 6400.0, 460.0, 12.0, 83), ((today - datetime.timedelta(days=5)).strftime('%Y-%m-%d'), 78.4, 18.2, 3.4, 56.0, 28800, 7800, 4000, 10200, 7500.0, 520.0, 25.0, 90), ((today - datetime.timedelta(days=6)).strftime('%Y-%m-%d'), 78.2, 18.0, 3.4, 54.0, 27000, 7000, 3800, 6400, 4500.0, 320.0, 5.0, 82), ((today - datetime.timedelta(days=7)).strftime('%Y-%m-%d'), 78.5, 18.3, 3.4, 55.0, 26100, 6800, 3400, 8000, 5800.0, 420.0, 10.0, 81)]
        cursor.executemany('\n            INSERT INTO withings_measurements (date, weight, fat_ratio, bone_mass, heart_pulse, sleep_duration, sleep_deep, sleep_rem, steps, distance, calories, elevation, sleep_score)\n            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n        ', withings_seed)
    cursor.execute('SELECT COUNT(*) FROM google_calendar_events')
    if cursor.fetchone()[0] == 0:
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        tomorrow_str = (datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        in_three_days_str = (datetime.date.today() + datetime.timedelta(days=3)).strftime('%Y-%m-%d')
        calendar_seed = [
            ("Möte med Sven", "Gå igenom kvartalsrapporten och planera nästa sprint.", f"{today_str}T10:00:00", f"{today_str}T11:00:00", "Konferensrum A"),
            ("Lunch med Maria", "Diskutera det nya designförslaget för gränssnittet.", f"{today_str}T12:00:00", f"{today_str}T13:00:00", "Gondolen"),
            ("Designgenomgång", "Gå igenom feedback från användartester.", f"{tomorrow_str}T14:00:00", f"{tomorrow_str}T15:30:00", "Teams-möte"),
            ("Läkarbesök", "Årlig hälsokontroll.", f"{in_three_days_str}T08:30:00", f"{in_three_days_str}T09:15:00", "Vårdcentralen City")
        ]
        cursor.executemany('\n            INSERT INTO google_calendar_events (summary, description, start_time, end_time, location)\n            VALUES (?, ?, ?, ?, ?)\n        ', calendar_seed)
    # Seed a strong random access token on first start, and rotate away from known weak/legacy defaults.
    LEGACY_WEAK_TOKENS = ('freja_secret', 'freja1234')
    cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_access_token'")
    row = cursor.fetchone()
    if row is None:
        new_token = secrets.token_urlsafe(32)
        cursor.execute("INSERT INTO api_keys (key_name, key_value) VALUES ('freja_access_token', ?)", (encrypt_value(new_token),))
        print(f"[FREJA] Genererade ny åtkomsttoken: {new_token}")
    elif row[0] in LEGACY_WEAK_TOKENS:
        new_token = secrets.token_urlsafe(32)
        cursor.execute("UPDATE api_keys SET key_value = ? WHERE key_name = 'freja_access_token'", (encrypt_value(new_token),))
        print(f"[FREJA] Roterade svag standardtoken till ny slumpmässig åtkomsttoken: {new_token}")
    conn.commit()
    conn.close()
