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
            last_sync_times[provider] = datetime.datetime.now().strftime("%H:%M:%S")

def get_sync_states():
    """Returns the current state dictionary for all sync jobs."""
    return {
        "states": sync_states,
        "errors": sync_errors,
        "last_sync": last_sync_times
    }
