"""Shared app-local date/time helpers.

Split out of backend/routes/trainer.py so both the trainer routes and the calendar routes
resolve "today" the same way - the backend runs on a server whose OS timezone isn't
guaranteed to be Europe/Stockholm, and a bare `datetime.date.today()` drifting a day out of
sync with the app's Stockholm-anchored date math caused edge-of-window bugs around midnight.
"""

import datetime

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Stockholm")
except Exception:  # pragma: no cover - tzdata may be missing on some hosts
    LOCAL_TZ = None


def today_local() -> datetime.date:
    """Today's date in the app's configured timezone (falls back to server time)."""
    if LOCAL_TZ is not None:
        return datetime.datetime.now(LOCAL_TZ).date()
    return datetime.date.today()
