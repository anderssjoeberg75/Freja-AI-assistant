"""In-memory status manager for asynchronous device synchronizations."""

import datetime

sync_states = {
    "garmin": "idle",
    "strava": "idle",
    "withings": "idle",
    "google_calendar": "idle"
}

sync_errors = {
    "garmin": "",
    "strava": "",
    "withings": "",
    "google_calendar": ""
}

last_sync_times = {
    "garmin": "",
    "strava": "",
    "withings": "",
    "google_calendar": ""
}

def set_sync_state(provider: str, state: str, error: str = ""):
    """Updates the state, error message, and timestamps for the given provider sync job."""
    if provider in sync_states:
        sync_states[provider] = state
        sync_errors[provider] = error
        if state == "success" or (state == "idle" and not error):
            now_dt = datetime.datetime.now()
            last_sync_times[provider] = now_dt.strftime("%H:%M:%S")
            try:
                from backend.database import get_db_connection
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO api_keys (key_name, key_value)
                        VALUES (?, ?)
                        ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
                    ''', (f"last_sync_{provider}", now_dt.strftime("%Y-%m-%d %H:%M:%S")))
                    conn.commit()
            except Exception as e:
                print(f"[sync_status] Failed to persist sync time for {provider}: {e}")

def get_sync_states():
    """Returns the current state dictionary for all sync jobs."""
    return {
        "states": sync_states,
        "errors": sync_errors,
        "last_sync": last_sync_times
    }
