"""AI Personal Trainer routes using FastAPI.

The trainer turns raw health data into coaching text through Gemini. There are three
model-backed entry points, each with its own prompt and response schema:

  POST /api/trainer/advice    - generates a full weekly training plan from a goal.
  GET  /api/trainer/checkin   - the short morning briefing (yesterday, today, weather).
  POST /api/trainer/optimize  - reviews already-booked workouts against recovery data.

Language convention: prompts, comments and error messages are English, but every prompt
explicitly instructs the model to answer in Swedish, since the output is shown verbatim to
the user. Weekday names in generated plans stay Swedish for the same reason - they are data
the user reads, and `day_offsets` in `book_plan_to_calendar` parses them back.
"""

import asyncio
import datetime
import httpx
from backend.services.http_client import shared_client
import json
import urllib.parse
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from backend.database import get_db_connection, get_api_key
from backend.services.weather_codes import describe_weather_code
from backend.services import plan_export

router = APIRouter()

# --- Configuration constants -------------------------------------------------
from backend.services.gemini_client import get_gemini_model, build_generate_url
DEFAULT_LOCATION = "Stockholm"
RHR_ALERT_PCT = 5.0        # Resting HR increase that warrants an "ease off" nudge
HRV_ALERT_PCT = -10.0      # HRV drop that warrants an "ease off" nudge
DEFAULT_WORKOUT_HOUR = 8   # Preferred workout start hour (local time)
DAY_END_HOUR = 21          # Latest a workout may be auto-scheduled to end
MAX_WORKOUT_MINUTES = 180  # Sanity cap for a single booked session
MAX_INPUT_LEN = 2000       # Cap on free-text goal/limitations sent to the LLM
# Coaching prompts carry a month of training data and ask for a whole structured plan, so
# they routinely run past a minute. The old 30s cut the model off mid-generation and the
# user saw a gateway error rather than a plan.
GEMINI_TIMEOUT_SECONDS = 180.0

# Health-baseline auto-update (Issue #35): recompute the profile's resting-HR / sleep /
# HRV baselines from a rolling window, but no more often than once a week so the trend
# alerts in calculate_trends() have a stable reference that still tracks real fitness drift.
BASELINE_WINDOW_DAYS = 28   # Rolling window averaged into each baseline
BASELINE_REFRESH_DAYS = 7   # Minimum days between automatic recomputes
BASELINE_MIN_SAMPLES = 3    # Fewest data points a baseline needs to be trustworthy

# Marks a forecast string as a failure so the caller knows not to cache it.
WEATHER_ERROR_PREFIX = "[weather unavailable] "

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


def _dict_row(cursor, row):
    """sqlite row factory returning a plain dict."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _reading(value):
    """Normalises a stored health metric to a real reading, or None.

    Garmin and Withings write 0 (and occasionally a negative sentinel) on days the device
    recorded nothing. A resting heart rate or HRV of 0 is physiologically impossible, so
    treating those rows as data drags every average and every plotted line towards zero -
    which is what made the trend percentages read as a huge improvement after a gap in
    wear. Anything not strictly positive is therefore "no reading"."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return value if num > 0 else None


def get_trainer_profile() -> dict:
    """Returns the single training profile row as a dict, or {} if none is set."""
    with get_db_connection() as conn:
        conn.row_factory = _dict_row
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM trainer_profile WHERE id = 1")
            row = cursor.fetchone()
        except Exception:
            row = None
    return dict(row) if row else {}


# In-memory weather cache keyed by (location, date) so repeated check-ins/plan
# generations on the same day don't re-hit the forecast API.
_weather_cache: dict = {}


async def fetch_7day_weather_forecast(location: str = DEFAULT_LOCATION) -> str:
    """Cached wrapper around the 7-day forecast (cache lives for the current day)."""
    key = ((location or DEFAULT_LOCATION).strip().lower(), today_local().isoformat())
    cached = _weather_cache.get(key)
    if cached is not None:
        return cached
    result = await _fetch_7day_weather_forecast_raw(location)
    # Only cache successful lookups so a transient error can be retried. The failure paths
    # below all return a string starting with WEATHER_ERROR_PREFIX; keep them in sync.
    if result and not result.startswith(WEATHER_ERROR_PREFIX):
        _weather_cache[key] = result
    return result


async def _fetch_7day_weather_forecast_raw(location: str = DEFAULT_LOCATION) -> str:
    """Builds the plain-text 7-day forecast block that gets pasted into the coach prompts.

    Returns a human-readable error string (prefixed with WEATHER_ERROR_PREFIX) rather than
    raising, because a missing forecast should degrade the advice, not fail the request."""
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=sv&format=json"
        async with shared_client() as client:
            res = await client.get(geo_url, timeout=8.0)
            res.raise_for_status()
            geo_data = res.json()

        results = geo_data.get('results')
        if not results:
            return f"{WEATHER_ERROR_PREFIX}Could not find the location '{location}' for the weather forecast."

        first = results[0]
        lat = first['latitude']
        lon = first['longitude']
        name = first['name']
        country = first.get('country', '')

        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,precipitation_sum,relative_humidity_2m_max,relative_humidity_2m_min&timezone=auto"
        async with shared_client() as client:
            res = await client.get(weather_url, timeout=8.0)
            res.raise_for_status()
            weather_data = res.json()

        daily = weather_data.get('daily')
        if not daily:
            return f"{WEATHER_ERROR_PREFIX}No weather forecast was returned."

        lines = [f"Weather forecast for {name}, {country} over the next 7 days:"]
        times = daily.get('time', [])
        for i in range(len(times)):
            date_str = times[i]
            w_code = daily.get('weather_code', [0])[i]
            temp_max = daily.get('temperature_2m_max', [0.0])[i]
            temp_min = daily.get('temperature_2m_min', [0.0])[i]
            app_max = daily.get('apparent_temperature_max', [0.0])[i]
            app_min = daily.get('apparent_temperature_min', [0.0])[i]
            precip = daily.get('precipitation_sum', [0.0])[i]
            rh_max = daily.get('relative_humidity_2m_max', [0.0])[i]
            rh_min = daily.get('relative_humidity_2m_min', [0.0])[i]

            desc = describe_weather_code(w_code)
            lines.append(
                f"- {date_str}: {desc}, Temp: {temp_min}°C to {temp_max}°C "
                f"(Feels like: {app_min}°C to {app_max}°C), Precipitation: {precip}mm, "
                f"Humidity: {rh_min}% to {rh_max}%"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"{WEATHER_ERROR_PREFIX}Failed to fetch the weather forecast for {location}: {str(e)}"

def calculate_trends():
    """Compares the last 7 days vs the preceding 14 days for resting HR and HRV.

    Resting HR is read from a single consistent source (Garmin preferred, Withings
    fallback) so the recent and baseline averages are never mixed across devices,
    which would otherwise make the percentage change meaningless. HRV is Garmin-only.
    """
    garmin_rows = []
    withings_rows = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT resting_hr, hrv FROM garmin_health ORDER BY date DESC LIMIT 21')
            garmin_rows = cursor.fetchall()
        except Exception as e:
            print(f"Error fetching Garmin health data for trends: {e}")
        try:
            cursor.execute('SELECT heart_pulse FROM withings_measurements ORDER BY date DESC LIMIT 21')
            withings_rows = cursor.fetchall()
        except Exception as e:
            print(f"Error fetching Withings measurements for trends: {e}")

    def _avg(vals):
        return sum(vals) / len(vals) if vals else None

    # Resting HR: pick one source that has BOTH a recent and a baseline window.
    # `_reading` drops the 0s the devices write on days they recorded nothing.
    g_recent_rhr = [v for r in garmin_rows[:7] if (v := _reading(r[0])) is not None]
    g_base_rhr = [v for r in garmin_rows[7:] if (v := _reading(r[0])) is not None]
    w_recent_rhr = [v for r in withings_rows[:7] if (v := _reading(r[0])) is not None]
    w_base_rhr = [v for r in withings_rows[7:] if (v := _reading(r[0])) is not None]

    if g_recent_rhr and g_base_rhr:
        recent_rhrs, baseline_rhrs = g_recent_rhr, g_base_rhr
    elif w_recent_rhr and w_base_rhr:
        recent_rhrs, baseline_rhrs = w_recent_rhr, w_base_rhr
    else:
        # Not enough for a valid comparison from a single source; expose what exists.
        recent_rhrs = g_recent_rhr or w_recent_rhr
        baseline_rhrs = g_base_rhr or w_base_rhr

    # HRV: Garmin only (Withings does not provide it).
    recent_hrvs = [v for r in garmin_rows[:7] if (v := _reading(r[1])) is not None]
    baseline_hrvs = [v for r in garmin_rows[7:] if (v := _reading(r[1])) is not None]

    rhr_recent_avg = _avg(recent_rhrs)
    rhr_baseline_avg = _avg(baseline_rhrs)
    hrv_recent_avg = _avg(recent_hrvs)
    hrv_baseline_avg = _avg(baseline_hrvs)

    rhr_change_pct = None
    if rhr_recent_avg and rhr_baseline_avg:
        rhr_change_pct = ((rhr_recent_avg - rhr_baseline_avg) / rhr_baseline_avg) * 100

    hrv_change_pct = None
    if hrv_recent_avg and hrv_baseline_avg:
        hrv_change_pct = ((hrv_recent_avg - hrv_baseline_avg) / hrv_baseline_avg) * 100

    return {
        "rhr_recent_avg": rhr_recent_avg,
        "rhr_baseline_avg": rhr_baseline_avg,
        "rhr_change_pct": rhr_change_pct,
        "hrv_recent_avg": hrv_recent_avg,
        "hrv_baseline_avg": hrv_baseline_avg,
        "hrv_change_pct": hrv_change_pct
    }


def format_trends_summary(trends: dict) -> str:
    """Renders the RHR/HRV trend dict as the text block pasted into the LLM prompts."""
    lines = []
    if trends["rhr_recent_avg"] is not None:
        recent_str = f"{trends['rhr_recent_avg']:.1f}"
        baseline_str = f"{trends['rhr_baseline_avg']:.1f}" if trends["rhr_baseline_avg"] is not None else "N/A"
        change_str = f"{trends['rhr_change_pct']:.1f}%" if trends["rhr_change_pct"] is not None else "N/A"
        lines.append(f"Resting heart rate (RHR): last 7 days avg: {recent_str} BPM, baseline (preceding 14 days): {baseline_str} BPM (change: {change_str})")
    if trends["hrv_recent_avg"] is not None:
        recent_str = f"{trends['hrv_recent_avg']:.1f}"
        baseline_str = f"{trends['hrv_baseline_avg']:.1f}" if trends["hrv_baseline_avg"] is not None else "N/A"
        change_str = f"{trends['hrv_change_pct']:.1f}%" if trends["hrv_change_pct"] is not None else "N/A"
        lines.append(f"HRV: last 7 days avg: {recent_str} ms, baseline (preceding 14 days): {baseline_str} ms (change: {change_str})")
    return "\n".join(lines) if lines else "No sufficient trend data (RHR/HRV) available."


def recompute_health_baselines(force: bool = False) -> dict:
    """Recomputes the profile's RHR / sleep / HRV baselines from a rolling window.

    Averages the last ``BASELINE_WINDOW_DAYS`` of Garmin health data (resting HR,
    sleep hours, HRV), falling back to Withings for resting HR and sleep when Garmin
    has no value. Writes the results back to ``trainer_profile`` and stamps
    ``baselines_updated_at`` so the next call respects the weekly cadence.

    Unless ``force`` is set, this is a no-op when the baselines were refreshed within
    the last ``BASELINE_REFRESH_DAYS`` days. A metric is only written when at least
    ``BASELINE_MIN_SAMPLES`` data points back it, so a sparse window never overwrites a
    good baseline with noise. Returns a summary dict describing what happened.
    """
    profile = get_trainer_profile()

    # Respect the weekly cadence unless forced.
    if not force and profile.get("baselines_updated_at"):
        try:
            last = datetime.datetime.strptime(
                str(profile["baselines_updated_at"])[:19], "%Y-%m-%d %H:%M:%S"
            )
            if (datetime.datetime.now() - last).days < BASELINE_REFRESH_DAYS:
                return {"status": "skipped", "reason": "refreshed_recently", "updated": {}}
        except (ValueError, TypeError):
            pass  # Unparseable timestamp — treat as stale and recompute.

    cutoff = (today_local() - datetime.timedelta(days=BASELINE_WINDOW_DAYS)).strftime('%Y-%m-%d')

    garmin_rhr, garmin_sleep, garmin_hrv = [], [], []
    withings_rhr, withings_sleep = [], []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                'SELECT resting_hr, sleep_hours, hrv FROM garmin_health WHERE date >= ?',
                (cutoff,)
            )
            # `_reading` skips the 0s written on days the device recorded nothing, so a
            # spell of not wearing the watch cannot drag a baseline down towards zero.
            for r in cursor.fetchall():
                if _reading(r[0]) is not None:
                    garmin_rhr.append(r[0])
                if _reading(r[1]) is not None:
                    garmin_sleep.append(r[1])
                if _reading(r[2]) is not None:
                    garmin_hrv.append(r[2])
        except Exception as e:
            print(f"[TRAINER BASELINES] Error reading Garmin health: {e}")
        try:
            cursor.execute(
                'SELECT heart_pulse, sleep_duration FROM withings_measurements WHERE date >= ?',
                (cutoff,)
            )
            for r in cursor.fetchall():
                if _reading(r[0]) is not None:
                    withings_rhr.append(r[0])
                if r[1]:  # sleep_duration is stored in seconds
                    withings_sleep.append(r[1] / 3600.0)
        except Exception as e:
            print(f"[TRAINER BASELINES] Error reading Withings measurements: {e}")

    def _avg(vals):
        return sum(vals) / len(vals) if len(vals) >= BASELINE_MIN_SAMPLES else None

    # Prefer Garmin; fall back to Withings for RHR and sleep (Withings has no HRV).
    rhr = _avg(garmin_rhr)
    if rhr is None:
        rhr = _avg(withings_rhr)
    sleep = _avg(garmin_sleep)
    if sleep is None:
        sleep = _avg(withings_sleep)
    hrv = _avg(garmin_hrv)

    updated = {}
    if rhr is not None:
        updated["baseline_resting_hr"] = round(rhr, 1)
    if sleep is not None:
        updated["baseline_sleep_hours"] = round(sleep, 1)
    if hrv is not None:
        updated["baseline_hrv"] = round(hrv, 1)

    if not updated:
        return {"status": "no_data", "reason": "insufficient_samples", "updated": {}}

    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM trainer_profile WHERE id = 1")
            exists = cursor.fetchone() is not None
            cols_vals = dict(updated)
            cols_vals["baselines_updated_at"] = now_str
            cols_vals["updated_at"] = now_str
            if exists:
                set_clause = ", ".join(f"{k} = ?" for k in cols_vals)
                cursor.execute(
                    f"UPDATE trainer_profile SET {set_clause} WHERE id = 1",
                    list(cols_vals.values())
                )
            else:
                cols = ["id"] + list(cols_vals.keys())
                placeholders = ", ".join("?" for _ in cols)
                cursor.execute(
                    f"INSERT INTO trainer_profile ({', '.join(cols)}) VALUES ({placeholders})",
                    [1] + list(cols_vals.values())
                )
            conn.commit()
    except Exception as e:
        print(f"[TRAINER BASELINES] Could not persist baselines: {e}")
        return {"status": "error", "reason": str(e), "updated": {}}

    return {"status": "success", "updated": updated, "window_days": BASELINE_WINDOW_DAYS}


# --- Strength logging (Issue #34) -------------------------------------------
MAX_STRENGTH_LOGS = 200  # Hard cap on rows a single list request may return


def get_recent_strength_logs(limit: int = 40) -> list:
    """Returns the most recent logged strength sets, newest first, as dicts."""
    limit = max(1, min(int(limit or 40), MAX_STRENGTH_LOGS))
    with get_db_connection() as conn:
        conn.row_factory = _dict_row
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''SELECT id, date, exercise_name, sets, reps, weight, rpe, notes, plan_id, created_at
                   FROM trainer_strength_logs
                   ORDER BY date DESC, id DESC
                   LIMIT ?''',
                (limit,)
            )
            return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"[TRAINER STRENGTH] Error reading strength logs: {e}")
            return []


def format_recent_strength_logs(limit: int = 40) -> str:
    """Renders recent strength logs as the text block pasted into the coach prompt.

    The coach uses the most recent load per exercise to apply progressive overload,
    so the lines are grouped by exercise with the latest session first."""
    logs = get_recent_strength_logs(limit)
    if not logs:
        return "No strength-training loads have been logged yet."

    by_exercise: dict = {}
    for log in logs:
        name = (log.get("exercise_name") or "Okänd övning").strip()
        by_exercise.setdefault(name, []).append(log)

    lines = []
    for name, entries in by_exercise.items():
        # entries already newest-first; keep the three most recent per exercise.
        parts = []
        for e in entries[:3]:
            weight = e.get("weight")
            load = f"{weight}kg" if weight else "kroppsvikt"
            rpe = f", RPE {e['rpe']}" if e.get("rpe") else ""
            parts.append(f"{e.get('date')}: {e.get('sets')}x{e.get('reps')} @ {load}{rpe}")
        lines.append(f"- {name}: " + " | ".join(parts))
    return "\n".join(lines)


# --- Injury / pain log (Issue #38) ------------------------------------------
MAX_INJURY_LOGS = 200      # Hard cap on rows a single list request may return
MAX_INJURY_PROMPT_ROWS = 8  # Active entries pasted into a coach prompt


def get_injury_logs(status: str = None, limit: int = 50) -> list:
    """Returns injury/pain entries, newest first. `status` filters to 'active'/'resolved'."""
    limit = max(1, min(int(limit or 50), MAX_INJURY_LOGS))
    sql = ('SELECT id, date, area, severity, note, status, resolved_date, created_at '
           'FROM trainer_injury_logs')
    params = []
    if status in ("active", "resolved"):
        sql += ' WHERE status = ?'
        params.append(status)
    sql += ' ORDER BY date DESC, id DESC LIMIT ?'
    params.append(limit)

    with get_db_connection() as conn:
        conn.row_factory = _dict_row
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
            return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"[TRAINER INJURY] Error reading injury logs: {e}")
            return []


def format_active_injuries() -> str:
    """Renders the open injury/pain entries as the text block pasted into coach prompts.

    Unlike the profile's single free-text `limitations` field, this is a dated log, so the
    coach can see how long something has been bothering the user and how bad it is now."""
    logs = get_injury_logs(status="active", limit=MAX_INJURY_PROMPT_ROWS)
    if not logs:
        return "No active injuries or pain have been logged."

    today = today_local()
    lines = []
    for log in logs:
        area = (log.get("area") or "Ospecificerat område").strip()
        severity = log.get("severity")
        sev_str = f"severity {severity}/10" if severity else "severity not given"
        note = (log.get("note") or "").strip()
        try:
            started = datetime.datetime.strptime(str(log.get("date"))[:10], "%Y-%m-%d").date()
            age = f", ongoing for {(today - started).days} days"
        except (ValueError, TypeError):
            age = ""
        lines.append(f"- {log.get('date')}: {area} ({sev_str}{age})" + (f" - {note}" if note else ""))
    return "\n".join(lines)


def compute_adherence(days: int = 14) -> dict:
    """Compares booked workout dates against completed Strava activity dates."""
    today = today_local()
    start_str = (today - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    today_str = today.strftime('%Y-%m-%d')

    planned_dates = set()
    completed_dates = set()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                'SELECT DISTINCT workout_date FROM trainer_bookings WHERE workout_date >= ? AND workout_date <= ?',
                (start_str, today_str)
            )
            planned_dates = {r[0] for r in cursor.fetchall() if r[0]}
        except Exception as e:
            print(f"Error fetching bookings for adherence: {e}")
        try:
            cursor.execute(
                'SELECT DISTINCT SUBSTR(date, 1, 10) FROM strava_activities WHERE SUBSTR(date, 1, 10) >= ? AND SUBSTR(date, 1, 10) <= ?',
                (start_str, today_str)
            )
            completed_dates = {r[0] for r in cursor.fetchall() if r[0]}
        except Exception as e:
            print(f"Error fetching activities for adherence: {e}")

    planned = len(planned_dates)
    completed = len(planned_dates & completed_dates)
    missed = sorted(planned_dates - completed_dates)
    adherence_pct = round(completed / planned * 100, 1) if planned else None
    return {
        "window_days": days,
        "planned": planned,
        "completed": completed,
        "adherence_pct": adherence_pct,
        "planned_dates": sorted(planned_dates),
        "missed_dates": missed
    }


# --- Training-load history for safe progression ------------------------------
# A plan built only from the last 7 days has no idea what the user can actually sustain: a
# quiet week reads as "untrained" and the next plan jumps from a 20-minute jog to an hour.
# These figures give the coach a month of real, completed training to progress FROM, plus
# the hard ceilings below so a single session or a whole week cannot balloon.
TRAINING_LOAD_DAYS = 30       # Lookback window summarised into the coach prompt
MAX_SESSION_STEP_PCT = 20     # A session may exceed the recent longest one by at most this
MAX_WEEKLY_STEP_PCT = 10      # Weekly volume may rise by at most this vs the recent average


def build_training_load_summary(days: int = TRAINING_LOAD_DAYS) -> dict:
    """Summarises actually-completed training over the last `days` days.

    Reads Strava activities (the authoritative record of what was performed) and falls back
    to Garmin's logged workouts for sessions Strava never saw. Returns per-week volume, the
    longest single session, and per-activity typical/longest durations - the reference
    points a progression has to be built on."""
    days = max(7, min(int(days or TRAINING_LOAD_DAYS), 180))
    today = today_local()
    cutoff = today - datetime.timedelta(days=days)
    cutoff_str = cutoff.strftime('%Y-%m-%d')

    # date -> {type: minutes} so a Garmin row is only used when Strava has nothing that day.
    sessions = []       # (date, activity_type, minutes, distance_km, avg_hr, source)
    strava_dates = set()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''SELECT SUBSTR(date, 1, 10), type, moving_time, distance, average_heartrate
                   FROM strava_activities
                   WHERE SUBSTR(date, 1, 10) >= ?
                   ORDER BY date ASC''',
                (cutoff_str,)
            )
            for d, a_type, moving_time, distance, avg_hr in cursor.fetchall():
                if not d:
                    continue
                minutes = round((moving_time or 0) / 60.0, 1)
                if minutes <= 0:
                    continue
                strava_dates.add(d)
                sessions.append({
                    "date": d,
                    "type": (a_type or "Träning").strip(),
                    "minutes": minutes,
                    "distance_km": round((distance or 0) / 1000.0, 2),
                    "avg_hr": avg_hr,
                    "source": "Strava",
                })
        except Exception as e:
            print(f"[TRAINER LOAD] Error reading Strava activities: {e}")

        try:
            cursor.execute(
                '''SELECT date, workout_type, workout_duration
                   FROM garmin_health
                   WHERE date >= ?
                   ORDER BY date ASC''',
                (cutoff_str,)
            )
            for d, w_type, w_dur in cursor.fetchall():
                if not d or d[:10] in strava_dates:
                    continue  # Strava already has this day; don't double-count it.
                minutes = round(float(w_dur or 0), 1)
                if minutes <= 0 or not w_type:
                    continue
                sessions.append({
                    "date": d[:10],
                    "type": str(w_type).strip(),
                    "minutes": minutes,
                    "distance_km": None,
                    "avg_hr": None,
                    "source": "Garmin",
                })
        except Exception as e:
            print(f"[TRAINER LOAD] Error reading Garmin workouts: {e}")

    sessions.sort(key=lambda s: s["date"])

    # Weekly buckets, counted back from today so "this week" is the most recent one.
    weeks: dict = {}
    for s in sessions:
        try:
            d = datetime.datetime.strptime(s["date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        bucket = (today - d).days // 7      # 0 = last 7 days, 1 = the 7 before that, ...
        wk = weeks.setdefault(bucket, {"weeks_ago": bucket, "sessions": 0, "minutes": 0.0, "distance_km": 0.0})
        wk["sessions"] += 1
        wk["minutes"] += s["minutes"]
        wk["distance_km"] += s["distance_km"] or 0.0

    weekly = [
        {
            "weeks_ago": w["weeks_ago"],
            "sessions": w["sessions"],
            "minutes": round(w["minutes"], 1),
            "distance_km": round(w["distance_km"], 2),
        }
        for w in sorted(weeks.values(), key=lambda x: x["weeks_ago"])
    ]

    by_type: dict = {}
    for s in sessions:
        t = by_type.setdefault(s["type"], {"activity_type": s["type"], "sessions": 0, "total_minutes": 0.0,
                                           "longest_minutes": 0.0, "longest_distance_km": 0.0})
        t["sessions"] += 1
        t["total_minutes"] += s["minutes"]
        t["longest_minutes"] = max(t["longest_minutes"], s["minutes"])
        t["longest_distance_km"] = max(t["longest_distance_km"], s["distance_km"] or 0.0)
    for t in by_type.values():
        t["avg_minutes"] = round(t["total_minutes"] / t["sessions"], 1) if t["sessions"] else 0.0
        t["total_minutes"] = round(t["total_minutes"], 1)
        t["longest_minutes"] = round(t["longest_minutes"], 1)
        t["longest_distance_km"] = round(t["longest_distance_km"], 2)

    weekly_minutes = [w["minutes"] for w in weekly]
    avg_weekly_minutes = round(sum(weekly_minutes) / len(weekly_minutes), 1) if weekly_minutes else 0.0
    longest_session = max((s["minutes"] for s in sessions), default=0.0)

    return {
        "window_days": days,
        "session_count": len(sessions),
        "weekly": weekly,
        "by_activity": sorted(by_type.values(), key=lambda t: -t["total_minutes"]),
        "avg_weekly_minutes": avg_weekly_minutes,
        "longest_session_minutes": round(longest_session, 1),
        # The ceilings a new plan must respect, precomputed so the prompt states real numbers
        # instead of asking the model to do the arithmetic.
        "max_session_minutes": round(longest_session * (1 + MAX_SESSION_STEP_PCT / 100.0)) if longest_session else None,
        "max_weekly_minutes": round(avg_weekly_minutes * (1 + MAX_WEEKLY_STEP_PCT / 100.0)) if avg_weekly_minutes else None,
        "recent_sessions": sessions[-20:],
    }


def _format_progression_rules(load: dict) -> str:
    """The hard progression ceilings for a new plan, stated as concrete minute figures.

    Without these the model happily writes "60 min löpning" for someone whose longest run in
    a month was 20 minutes. Expressed as numbers rather than percentages because the model
    follows an explicit ceiling far more reliably than one it has to compute."""
    if not load or not load.get("session_count"):
        return (
            "- The user has no recorded training in the last month. Cap every session at 30 minutes "
            "in week 1 and increase by at most 10% per week."
        )

    rules = [
        f"- HARD CEILING: no single session may exceed {load['max_session_minutes']} minutes "
        f"(the longest session actually completed in the last {load['window_days']} days was "
        f"{load['longest_session_minutes']} minutes, and a step of more than "
        f"{MAX_SESSION_STEP_PCT}% is an injury risk).",
        f"- HARD CEILING: total planned minutes for the week may not exceed "
        f"{load['max_weekly_minutes']} minutes (recent average is {load['avg_weekly_minutes']} "
        f"min/week; weekly volume must not rise by more than {MAX_WEEKLY_STEP_PCT}%).",
        "- Progress FROM the durations listed per activity type above. If the user typically runs "
        "20 minutes, the next step is roughly 22-24 minutes - never 60.",
        "- If a requested goal would require breaking these ceilings, say so plainly in the summary "
        "and lay out the build-up over several weeks instead of jumping straight to the target.",
    ]
    return "\n".join(rules)


def format_training_load_summary(load: dict) -> str:
    """Renders the training-load summary as the text block pasted into the coach prompt."""
    if not load or not load.get("session_count"):
        return (f"No completed training was recorded in the last {load.get('window_days', TRAINING_LOAD_DAYS)} days. "
                "Treat the user as returning from a break: start conservatively and build up gradually.")

    lines = [
        f"Completed training over the last {load['window_days']} days: {load['session_count']} sessions, "
        f"averaging {load['avg_weekly_minutes']} minutes per week.",
        f"Longest single session in that period: {load['longest_session_minutes']} minutes.",
        "Weekly volume (0 = the last 7 days):",
    ]
    for w in load["weekly"]:
        lines.append(
            f"- {w['weeks_ago']} weeks ago: {w['sessions']} sessions, {w['minutes']} min"
            + (f", {w['distance_km']} km" if w["distance_km"] else "")
        )
    lines.append("Per activity type (typical vs longest session):")
    for t in load["by_activity"]:
        lines.append(
            f"- {t['activity_type']}: {t['sessions']} sessions, typically {t['avg_minutes']} min, "
            f"longest {t['longest_minutes']} min"
            + (f" / {t['longest_distance_km']} km" if t["longest_distance_km"] else "")
        )
    return "\n".join(lines)


# --- Trend series for the PT charts (Issue #36) ------------------------------
MAX_TREND_DAYS = 180  # Longest window the trend chart may request


def get_health_series(days: int = 28) -> list:
    """Returns a day-by-day RHR/HRV series for the trend charts, oldest first.

    `calculate_trends()` only yields aggregates, which cannot be plotted. This reads the
    same sources: Garmin per day, with Withings' pulse filling in resting HR on days
    Garmin has none. Days with no reading at all are omitted rather than zero-filled, so
    a gap in the data renders as a gap instead of a phantom dip to zero."""
    days = max(1, min(int(days or 28), MAX_TREND_DAYS))
    cutoff = (today_local() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')

    by_date: dict = {}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                'SELECT date, resting_hr, hrv FROM garmin_health WHERE date >= ? ORDER BY date ASC',
                (cutoff,)
            )
            for date_str, rhr, hrv in cursor.fetchall():
                if not date_str:
                    continue
                by_date[date_str[:10]] = {
                    "date": date_str[:10], "rhr": _reading(rhr), "hrv": _reading(hrv)
                }
        except Exception as e:
            print(f"[TRAINER TRENDS] Error reading Garmin health series: {e}")
        try:
            cursor.execute(
                'SELECT date, heart_pulse FROM withings_measurements WHERE date >= ? ORDER BY date ASC',
                (cutoff,)
            )
            for date_str, pulse in cursor.fetchall():
                if not date_str:
                    continue
                key = date_str[:10]
                entry = by_date.setdefault(key, {"date": key, "rhr": None, "hrv": None})
                if entry.get("rhr") is None:
                    entry["rhr"] = _reading(pulse)
        except Exception as e:
            print(f"[TRAINER TRENDS] Error reading Withings series: {e}")

    return [by_date[k] for k in sorted(by_date) if by_date[k].get("rhr") is not None or by_date[k].get("hrv") is not None]


@router.get("/api/trainer/trends")
async def get_trainer_trends(days: int = Query(28, description="Length of the trend window in days")):
    """Everything the PT panel's trend & adherence charts need, in one request (Issue #36).

    Bundles the plotted series, the recent-vs-baseline aggregates already used in the
    coach prompts, the profile's stored baselines (drawn as reference lines) and the
    adherence figures, so the panel renders from a single round trip."""
    try:
        days = max(1, min(int(days or 28), MAX_TREND_DAYS))
        profile = get_trainer_profile()
        return {
            "window_days": days,
            "series": get_health_series(days),
            "trends": calculate_trends(),
            "baselines": {
                "resting_hr": profile.get("baseline_resting_hr"),
                "hrv": profile.get("baseline_hrv"),
                "sleep_hours": profile.get("baseline_sleep_hours"),
                "updated_at": profile.get("baselines_updated_at"),
            },
            "adherence": compute_adherence(days),
            "alert_thresholds": {"rhr_pct": RHR_ALERT_PCT, "hrv_pct": HRV_ALERT_PCT},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/trainer/profile")
async def get_trainer_profile_endpoint():
    """Returns the stored training profile (empty object if not yet set)."""
    try:
        return get_trainer_profile()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/trainer/profile")
async def put_trainer_profile(request: Request):
    """Creates or updates the single training profile row."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    fields = [
        "event", "event_date", "fitness_level", "availability", "goals",
        "limitations", "location", "baseline_resting_hr", "baseline_sleep_hours",
        "baseline_hrv", "auto_adjust"
    ]
    text_fields = {"event", "event_date", "fitness_level", "availability", "goals", "limitations", "location"}

    values = {}
    for f in fields:
        if f in body and body[f] is not None:
            val = body[f]
            if f in text_fields:
                val = str(val).strip()[:MAX_INPUT_LEN]
            elif f == "auto_adjust":
                val = 1 if val in (True, 1, "1", "true", "True", "on") else 0
            values[f] = val

    try:
        return {"status": "success", "message": "Training profile saved.", "profile": _save_profile_values(values)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _save_profile_values(values: dict) -> dict:
    """Upserts the single training-profile row and returns it. Shared by the profile
    endpoint and onboarding so both write the row the same way."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM trainer_profile WHERE id = 1")
        exists = cursor.fetchone() is not None
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if exists:
            if values:
                set_clause = ", ".join(f"{k} = ?" for k in values)
                params = list(values.values()) + [now_str]
                cursor.execute(f"UPDATE trainer_profile SET {set_clause}, updated_at = ? WHERE id = 1", params)
            else:
                cursor.execute("UPDATE trainer_profile SET updated_at = ? WHERE id = 1", (now_str,))
        else:
            cols = ["id"] + list(values.keys()) + ["updated_at"]
            placeholders = ", ".join("?" for _ in cols)
            params = [1] + list(values.values()) + [now_str]
            cursor.execute(f"INSERT INTO trainer_profile ({', '.join(cols)}) VALUES ({placeholders})", params)
        conn.commit()
    return get_trainer_profile()


@router.get("/api/trainer/adherence")
async def get_trainer_adherence(days: int = Query(14, description="Lookback window in days")):
    """Returns planned vs completed workout adherence over the given window."""
    try:
        return compute_adherence(days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/trainer/baselines/refresh")
async def refresh_trainer_baselines(request: Request):
    """Recomputes the RHR/sleep/HRV baselines now (Issue #35).

    Normally the baselines refresh themselves at most weekly off the Garmin sync;
    this endpoint lets the user force an immediate recompute. Pass {"force": false}
    to honour the weekly cadence instead."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        force = body.get("force", True)
        force = force in (True, 1, "1", "true", "True", "on")
        return recompute_health_baselines(force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/trainer/strength/log")
async def get_strength_logs(limit: int = Query(40, description="Number of logged sets to return")):
    """Returns recent logged strength sets (Issue #34), newest first."""
    try:
        return {"logs": get_recent_strength_logs(limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/trainer/strength/log")
async def add_strength_log(request: Request):
    """Records one completed strength set (name, sets, reps, weight, RPE).

    These logs feed progressive overload: the coach reads the latest load per
    exercise when generating the next plan."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    name = str(body.get("exercise_name") or "").strip()[:120]
    if not name:
        raise HTTPException(status_code=400, detail="An exercise name is required.")

    def _to_int(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _to_float(v):
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    sets = max(0, _to_int(body.get("sets")))
    reps = max(0, _to_int(body.get("reps")))
    weight = _to_float(body.get("weight"))
    rpe = _to_float(body.get("rpe"))
    if rpe is not None:
        rpe = max(1.0, min(10.0, rpe))  # RPE is a 1-10 scale
    notes = str(body.get("notes") or "").strip()[:MAX_INPUT_LEN]
    plan_id = body.get("plan_id")
    try:
        plan_id = int(plan_id) if plan_id is not None else None
    except (TypeError, ValueError):
        plan_id = None

    date_str = str(body.get("date") or "").strip()[:10]
    if not date_str:
        date_str = today_local().strftime('%Y-%m-%d')
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO trainer_strength_logs
                   (date, exercise_name, sets, reps, weight, rpe, notes, plan_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (date_str, name, sets, reps, weight, rpe, notes, plan_id, now_str)
            )
            conn.commit()
            log_id = cursor.lastrowid
        return {"status": "success", "id": log_id, "message": "Strength set logged."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/trainer/strength/log")
async def delete_strength_log(log_id: int = Query(..., description="ID of the strength log to delete")):
    """Deletes a single logged strength set."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM trainer_strength_logs WHERE id = ?', (log_id,))
            conn.commit()
        return {"status": "success", "message": f"Strength log {log_id} deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/trainer/injuries")
async def get_injuries(
    status: str = Query(None, description="Filter by 'active' or 'resolved' (omit for all)"),
    limit: int = Query(50, description="Number of entries to return"),
):
    """Returns logged injury/pain entries (Issue #38), newest first."""
    try:
        return {"injuries": get_injury_logs(status=status, limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/trainer/injuries")
async def add_injury(request: Request):
    """Logs an injury or pain entry (area, severity, note).

    Active entries are fed into plan generation and the recovery optimizer, so the coach
    eases or swaps sessions that would load the affected area."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    area = str(body.get("area") or "").strip()[:120]
    if not area:
        raise HTTPException(status_code=400, detail="A body area is required.")

    try:
        severity = int(body.get("severity") or 0)
    except (TypeError, ValueError):
        severity = 0
    severity = max(0, min(10, severity)) or None  # 1-10 scale; 0/absent stores NULL

    note = str(body.get("note") or "").strip()[:MAX_INPUT_LEN]
    date_str = str(body.get("date") or "").strip()[:10] or today_local().strftime('%Y-%m-%d')
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO trainer_injury_logs
                   (date, area, severity, note, status, resolved_date, created_at)
                   VALUES (?, ?, ?, ?, 'active', NULL, ?)''',
                (date_str, area, severity, note, now_str)
            )
            conn.commit()
            injury_id = cursor.lastrowid
        return {"status": "success", "id": injury_id, "message": "Injury logged."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/trainer/injuries")
async def update_injury(request: Request):
    """Updates an injury entry - typically to mark it resolved once it stops hurting.

    Resolving stamps `resolved_date` and drops the entry out of the coach prompts, while
    keeping it in the log so a recurring niggle stays visible as history."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    try:
        injury_id = int(body.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="An injury ID is required.")

    values = {}
    if body.get("status") in ("active", "resolved"):
        values["status"] = body["status"]
        # Resolving stamps the date; reopening clears it again.
        values["resolved_date"] = today_local().strftime('%Y-%m-%d') if body["status"] == "resolved" else None
    if "severity" in body:
        try:
            values["severity"] = max(0, min(10, int(body.get("severity") or 0))) or None
        except (TypeError, ValueError):
            pass
    if "note" in body:
        values["note"] = str(body.get("note") or "").strip()[:MAX_INPUT_LEN]
    if "area" in body and str(body.get("area") or "").strip():
        values["area"] = str(body["area"]).strip()[:120]

    if not values:
        raise HTTPException(status_code=400, detail="No fields to update were supplied.")

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            set_clause = ", ".join(f"{k} = ?" for k in values)
            cursor.execute(
                f"UPDATE trainer_injury_logs SET {set_clause} WHERE id = ?",
                list(values.values()) + [injury_id]
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="The injury entry was not found.")
        return {"status": "success", "message": f"Injury {injury_id} updated."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/trainer/injuries")
async def delete_injury(injury_id: int = Query(..., description="ID of the injury entry to delete")):
    """Deletes a single injury/pain entry."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM trainer_injury_logs WHERE id = ?', (injury_id,))
            conn.commit()
        return {"status": "success", "message": f"Injury {injury_id} deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/trainer/plans")
async def get_trainer_plans(limit: int = Query(20, description="Number of plans to retrieve")):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, date, goal, advice_text, limitations 
                FROM trainer_plans 
                ORDER BY date DESC, id DESC
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
        
        results = []
        for row in rows:
            results.append({
                'id': row[0],
                'date': row[1],
                'goal': row[2],
                'advice_text': row[3],
                'limitations': row[4]
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/trainer/plans")
async def delete_trainer_plan(plan_id: int = Query(..., description="ID of the plan to delete")):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM trainer_plans WHERE id = ?', (plan_id,))
            conn.commit()
        return {'status': 'success', 'message': f"The training plan with ID {plan_id} has been deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _workout_end_time(date_str: str, duration_minutes: int) -> str:
    """End timestamp for a listed workout, derived from its duration (default start 08:00)."""
    try:
        minutes = max(1, min(int(duration_minutes or 0), MAX_WORKOUT_MINUTES))
    except (TypeError, ValueError):
        minutes = 60
    start = datetime.datetime.strptime(f"{date_str}T08:00", "%Y-%m-%dT%H:%M")
    return (start + datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


@router.get("/api/trainer/workouts")
async def get_trainer_workouts(days: int = Query(14, description="Lookback/lookahead window in days")):
    """Returns scheduled PT workouts directly from local SQLite database (trainer_bookings or latest trainer_plan).
    Works independently of Google Calendar."""
    try:
        today = today_local()
        current_dow = today.weekday() # 0 = Monday
        monday = today - datetime.timedelta(days=current_dow)
        days = max(1, min(int(days or 14), 365))
        window_start = today - datetime.timedelta(days=days)
        window_end = today + datetime.timedelta(days=days)

        results = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # `days` bounds the window in both directions. It used to be accepted and then
            # ignored, so the list grew without limit and every plan ever booked came back.
            cursor.execute('''
                SELECT b.id, b.plan_id, b.event_id, b.workout_date, b.week, p.advice_text
                FROM trainer_bookings b
                JOIN trainer_plans p ON b.plan_id = p.id
                WHERE b.workout_date >= ? AND b.workout_date <= ?
                ORDER BY b.workout_date ASC
            ''', (window_start.isoformat(), window_end.isoformat()))
            rows = cursor.fetchall()

        day_offsets = plan_export.SWEDISH_DAY_OFFSETS

        for row in rows:
            booking_id, plan_id, event_id, w_date_str, week_num, advice_text = row
            try:
                plan_data = json.loads(advice_text) if advice_text else {}
                workouts = plan_data.get("workouts", [])
                w_date = datetime.datetime.strptime(w_date_str, "%Y-%m-%d").date()

                matching_w = None
                for w in workouts:
                    day_name = str(w.get("day", "")).lower()
                    offset = day_offsets.get(day_name)
                    if offset is not None:
                        w_day_num = w_date.weekday()
                        if offset == w_day_num:
                            matching_w = w
                            break
                if not matching_w and workouts:
                    matching_w = workouts[0]

                if matching_w:
                    duration = int(matching_w.get("duration_minutes", 0) or 0)
                    summary = f"💪 {matching_w.get('activity_type', 'Träning')}: {matching_w.get('title', 'Pass')}"
                    exercises_block = _format_exercises_for_calendar(matching_w.get("exercises"))
                    description = (
                        f"Träningspass genererat av COACH AI.\n\nBeskrivning:\n{matching_w.get('description', '')}"
                        f"{exercises_block}\n\nTid: {duration} minuter."
                    )
                    results.append({
                        "id": booking_id,
                        "plan_id": plan_id,
                        "summary": summary,
                        "description": description,
                        "start_time": f"{w_date_str}T08:00:00",
                        # Derived from the session's own duration. A fixed 09:00 made every
                        # card read "(60 min)" regardless of what the plan actually said.
                        "end_time": _workout_end_time(w_date_str, duration),
                        "location": WORKOUT_LOCATION_MARKER,
                        "duration_minutes": duration,
                        "activity_type": matching_w.get("activity_type", "Träning"),
                        "title": matching_w.get("title", "Pass"),
                        "exercises": matching_w.get("exercises", [])
                    })
            except Exception as parse_err:
                print(f"[TRAINER WORKOUTS] Error parsing booking {booking_id}: {parse_err}")

        # Fallback to the most recent plan in trainer_plans if trainer_bookings is empty
        if not results:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, advice_text, date FROM trainer_plans
                    ORDER BY id DESC LIMIT 1
                ''')
                row = cursor.fetchone()

            if row:
                plan_id, advice_text, p_date_str = row
                try:
                    plan_data = json.loads(advice_text) if advice_text else {}
                    workouts = plan_data.get("workouts", [])
                    for w in workouts:
                        day_name = str(w.get("day", "")).lower()
                        offset = day_offsets.get(day_name)
                        if offset is None:
                            continue
                        duration = int(w.get("duration_minutes", 0) or 0)
                        if duration <= 0:
                            continue
                        w_date = monday + datetime.timedelta(days=offset)
                        w_date_str = w_date.isoformat()
                        summary = f"💪 {w.get('activity_type', 'Träning')}: {w.get('title', 'Pass')}"
                        exercises_block = _format_exercises_for_calendar(w.get("exercises"))
                        description = (
                            f"Träningspass genererat av COACH AI.\n\nBeskrivning:\n{w.get('description', '')}"
                            f"{exercises_block}\n\nTid: {duration} minuter."
                        )
                        results.append({
                            "id": f"plan_{plan_id}_{offset}",
                            "plan_id": plan_id,
                            "summary": summary,
                            "description": description,
                            "start_time": f"{w_date_str}T08:00:00",
                            "end_time": _workout_end_time(w_date_str, duration),
                            "location": WORKOUT_LOCATION_MARKER,
                            "duration_minutes": duration,
                            "activity_type": w.get("activity_type", "Träning"),
                            "title": w.get("title", "Pass"),
                            "exercises": w.get("exercises", [])
                        })
                except Exception as fb_err:
                    print(f"[TRAINER WORKOUTS] Fallback parse error: {fb_err}")

        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Chat context ------------------------------------------------------------
# Freja is the user's coach in ordinary conversation too, not only when a tool fires. The
# block below is injected into her system prompt on every turn so "hur ser dagens pass ut"
# is answered from the actual plan even if the model never decides to call a tool - which is
# what used to make her improvise a walk that was nowhere in the schedule.
SWEDISH_WEEKDAYS = ["Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag", "Lördag", "Söndag"]
CHAT_CONTEXT_MAX_WORKOUTS = 14   # Sessions listed before the block is truncated
CHAT_CONTEXT_DESC_LEN = 400      # Per-session description budget


def build_chat_context_block() -> str:
    """Renders the active plan, this week's schedule and today's session as prompt text.

    Swedish, because it is quoted more or less verbatim back to the user; everything else in
    this file is English. Returns "" when there is nothing to say, so the caller can skip the
    injection entirely rather than pasting an empty header."""
    profile = get_trainer_profile()
    today = today_local()
    today_str = today.isoformat()

    plan = None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT id, date, goal, advice_text, limitations FROM trainer_plans ORDER BY id DESC LIMIT 1"
            )
            plan = cursor.fetchone()
        except Exception as e:
            print(f"[TRAINER CONTEXT] Could not read the active plan: {e}")

    lines = [f"Dagens datum: {today_str} ({SWEDISH_WEEKDAYS[today.weekday()]})."]

    if profile:
        prof_bits = []
        for label, key in (("Mål", "goals"), ("Nivå", "fitness_level"), ("Tillgänglighet", "availability"),
                           ("Tävling/mål-event", "event"), ("Eventdatum", "event_date"),
                           ("Begränsningar", "limitations")):
            val = str(profile.get(key) or "").strip()
            if val:
                prof_bits.append(f"{label}: {val}")
        if prof_bits:
            lines.append("Träningsprofil - " + "; ".join(prof_bits) + ".")

    plan_data = {}
    if plan:
        plan_id, plan_date, goal, advice_text, limitations = plan
        try:
            plan_data = json.loads(str(advice_text or "").replace("```json", "").replace("```", "").strip())
        except Exception:
            plan_data = {}
        lines.append(f"Aktivt träningsprogram (skapat {plan_date}, mål: \"{goal}\").")
        if plan_data.get("weekly_focus"):
            lines.append(f"Veckans fokus: {plan_data['weekly_focus']}")
        if plan_data.get("summary"):
            lines.append(f"Coachens analys: {str(plan_data['summary'])[:CHAT_CONTEXT_DESC_LEN]}")

    # Booked sessions for the current week, newest plan first.
    workouts = []
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''SELECT b.workout_date, p.advice_text
                   FROM trainer_bookings b JOIN trainer_plans p ON b.plan_id = p.id
                   WHERE b.workout_date >= ? AND b.workout_date <= ?
                   ORDER BY b.workout_date ASC''',
                (monday.isoformat(), sunday.isoformat())
            )
            booking_rows = cursor.fetchall()
        except Exception as e:
            print(f"[TRAINER CONTEXT] Could not read bookings: {e}")
            booking_rows = []

    day_offsets = plan_export.SWEDISH_DAY_OFFSETS
    for w_date_str, advice_text in booking_rows:
        try:
            w_date = datetime.datetime.strptime(w_date_str, "%Y-%m-%d").date()
            booked_plan = json.loads(str(advice_text or "").replace("```json", "").replace("```", "").strip())
        except Exception:
            continue
        for w in booked_plan.get("workouts", []):
            if day_offsets.get(str(w.get("day", "")).lower()) == w_date.weekday():
                workouts.append((w_date, w))
                break

    # No bookings (e.g. calendar never connected): fall back to the plan's own weekdays,
    # mapped onto the current week, so the schedule is still known in conversation.
    if not workouts and plan_data.get("workouts"):
        for w in plan_data["workouts"]:
            offset = day_offsets.get(str(w.get("day", "")).lower())
            if offset is not None:
                workouts.append((monday + datetime.timedelta(days=offset), w))
        workouts.sort(key=lambda item: item[0])

    if workouts:
        lines.append("Inbokade träningspass denna vecka:")
        for w_date, w in workouts[:CHAT_CONTEXT_MAX_WORKOUTS]:
            marker = " <-- IDAG" if w_date == today else ""
            dur = w.get("duration_minutes", 0)
            desc = str(w.get("description", "") or "").strip()[:CHAT_CONTEXT_DESC_LEN]
            exercises = w.get("exercises") or []
            ex_str = ""
            if isinstance(exercises, list) and exercises:
                ex_str = " Övningar: " + "; ".join(
                    f"{e.get('name', 'Övning')} {e.get('sets', 0)}x{e.get('reps', 0)}"
                    + (f" @ {e.get('target_weight')} kg" if e.get("target_weight") else "")
                    for e in exercises[:8]
                )
            lines.append(
                f"- {w_date.isoformat()} ({SWEDISH_WEEKDAYS[w_date.weekday()]}): "
                f"{w.get('activity_type', 'Träning')} - {w.get('title', 'Pass')}, {dur} min.{marker}"
                f" {desc}{ex_str}".rstrip()
            )
        if not any(w_date == today for w_date, _ in workouts):
            lines.append(f"Inget pass är inbokat idag ({today_str}) - det är en vilodag enligt programmet.")
    else:
        lines.append("Inga inbokade träningspass hittades för den här veckan.")

    injuries = format_active_injuries()
    if injuries and not injuries.startswith("No active"):
        lines.append("Aktiva skador/besvär (senast loggade):\n" + injuries)

    load = build_training_load_summary(TRAINING_LOAD_DAYS)
    if load.get("session_count"):
        lines.append(
            f"Faktiskt genomförd träning senaste {load['window_days']} dagarna: {load['session_count']} pass, "
            f"i snitt {load['avg_weekly_minutes']} min/vecka, längsta pass {load['longest_session_minutes']} min."
        )

    return "\n".join(lines) if len(lines) > 1 else ""


@router.get("/api/trainer/context")
async def get_trainer_chat_context():
    """The PT context block injected into Freja's system prompt on every chat turn."""
    try:
        block = build_chat_context_block()
        return {"status": "success", "has_context": bool(block), "context": block}
    except Exception as e:
        # Never fail the chat over this: an empty context degrades to tool-call behaviour.
        print(f"[TRAINER CONTEXT] Build error: {e}")
        return {"status": "error", "has_context": False, "context": "", "detail": str(e)}


def _next_monday(from_date: datetime.date = None) -> datetime.date:
    """The next upcoming Monday - the default start date for an exported plan."""
    d = from_date or today_local()
    return d + datetime.timedelta(days=(7 - d.weekday()) or 7)


def _current_week_monday(from_date: datetime.date = None) -> datetime.date:
    """The Monday of the week `from_date` falls in.

    A plan's `day` field is a Swedish weekday name that book_plan_to_calendar turns into an
    absolute offset from the start date (Måndag = 0). The start date must therefore BE a
    Monday, or every session lands on the wrong weekday - generating a plan on a Wednesday
    used to book "Måndag" on Wednesday, "Tisdag" on Thursday and so on."""
    d = from_date or today_local()
    return d - datetime.timedelta(days=d.weekday())


# Swedish (and common accented) letters folded to ASCII so a filename built from a goal
# like "Träna inför Göteborgsvarvet" stays readable instead of becoming a row of dashes.
_FILENAME_TRANSLITERATIONS = str.maketrans({
    "å": "a", "ä": "a", "ö": "o", "é": "e", "è": "e", "ü": "u", "ø": "o", "æ": "ae",
})


def _safe_filename(text: str, fallback: str = "traningsplan") -> str:
    """ASCII-safe slug for a Content-Disposition filename.

    Must be strictly ASCII: header values are latin-1 encoded on the way out, so a goal
    containing Cyrillic or CJK would raise mid-response, and even latin-1-representable
    Swedish letters come back mojibake. `str.isalnum()` is Unicode-aware and would happily
    keep all of those, so the ASCII check below is what actually does the filtering."""
    folded = str(text or "").lower().translate(_FILENAME_TRANSLITERATIONS)
    slug = "".join(c if (c.isascii() and c.isalnum()) else "-" for c in folded)
    slug = "-".join(part for part in slug.split("-") if part)[:60]
    return slug or fallback


@router.get("/api/trainer/plans/export")
async def export_trainer_plan(
    plan_id: int = Query(..., description="ID of the plan to export"),
    format: str = Query("ics", description="Export format: 'ics' or 'pdf'"),
    start_date: str = Query(None, description="Date the plan's week starts (YYYY-MM-DD, default: next Monday)"),
):
    """Exports a saved plan as a calendar file or a printable PDF (Issue #39).

    The plan itself only names weekdays, so `start_date` is what turns it into dated
    sessions - it defaults to the next Monday, matching the booking widget's default."""
    fmt = (format or "ics").strip().lower()
    if fmt not in ("ics", "pdf"):
        raise HTTPException(status_code=400, detail="Unsupported format (use 'ics' or 'pdf').")

    if start_date:
        try:
            start = datetime.datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start date format (use YYYY-MM-DD).")
    else:
        start = _next_monday()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, date, goal, advice_text, limitations FROM trainer_plans WHERE id = ?", (plan_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="The training plan was not found.")

    plan = {"id": row[0], "date": row[1], "goal": row[2], "advice_text": row[3], "limitations": row[4]}
    plan_data = plan_export.parse_plan_text(plan["advice_text"])
    base_name = f"freja-{_safe_filename(plan['goal'])}-{plan['date'] or start.isoformat()}"

    try:
        if fmt == "ics":
            if not plan_data:
                raise HTTPException(
                    status_code=400,
                    detail="This training plan has no structured data and cannot be exported to a calendar."
                )
            body = plan_export.build_ics(plan, plan_data, start).encode("utf-8")
            media_type = "text/calendar; charset=utf-8"
            filename = f"{base_name}.ics"
        else:
            # A plan without structured JSON still exports as a PDF of its raw advice text.
            body = plan_export.build_pdf(plan, plan_data, start)
            media_type = "application/pdf"
            filename = f"{base_name}.pdf"
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not build the export: {e}")

    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/trainer/generate")
async def generate_trainer_plan(request: Request):
    try:
        body = await request.json()
        goal = body.get("goal", "").strip()[:MAX_INPUT_LEN]
        limitations = body.get("limitations", "").strip()[:MAX_INPUT_LEN]
        if not goal:
            raise HTTPException(status_code=400, detail="The goal is missing.")

        # Fall back to the stored training profile for limitations/location.
        profile = get_trainer_profile()
        if not limitations and profile.get("limitations"):
            limitations = str(profile["limitations"]).strip()[:MAX_INPUT_LEN]
        location = (body.get("location") or profile.get("location") or DEFAULT_LOCATION)
        location = str(location).strip() or DEFAULT_LOCATION

        # 1-3. Fetch Garmin / Strava / Withings logs (single connection).
        garmin_summary = []
        strava_summary = []
        withings_summary = []
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('''
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, sleep_score
                FROM garmin_health
                ORDER BY date DESC
                LIMIT 7
            ''')
            for r in cursor.fetchall():
                garmin_summary.append(
                    f"Date: {r[0]}, Steps: {r[1]}, Sleep: {r[2]}h (Deep: {r[11]}h, REM: {r[13]}h, Light: {r[12]}h, Awake: {r[14]}h, Score: {r[15]}), Resting HR: {r[3]}, Calories: {r[4]}kcal, Workout: {r[5]} ({r[6]} min), Body Battery: {r[7]}, HRV: {r[8]}ms, Recovery time: {r[9]}h, Status: {r[10]}"
                )

            cursor.execute('''
                SELECT name, type, date, distance, moving_time, total_elevation_gain, average_heartrate, max_heartrate, calories
                FROM strava_activities
                ORDER BY date DESC
                LIMIT 20
            ''')
            for r in cursor.fetchall():
                dist_km = round(r[3] / 1000.0, 2) if r[3] else 0
                dur_min = round(r[4] / 60.0, 1) if r[4] else 0
                strava_summary.append(
                    f"Activity: {r[0]}, Type: {r[1]}, Date: {r[2]}, Distance: {dist_km} km, Time: {dur_min} min, Elevation gain: {r[5]}m, Avg HR: {r[6]}, Max HR: {r[7]}, Calories: {r[8]}kcal"
                )

            cursor.execute('''
                SELECT date, weight, fat_ratio, bone_mass, heart_pulse, sleep_duration, steps, calories, sleep_score
                FROM withings_measurements
                ORDER BY date DESC
                LIMIT 7
            ''')
            for r in cursor.fetchall():
                sleep_h = round(r[5] / 3600.0, 1) if r[5] else 0
                withings_summary.append(
                    f"Date: {r[0]}, Weight: {r[1]} kg, Body fat: {r[2]}%, Bone mass: {r[3]} kg, Pulse: {r[4]} BPM, Sleep: {sleep_h}h (Score: {r[8]}), Steps: {r[6]}, Calories: {r[7]}kcal"
                )

        # 4. Fetch Gemini API key
        api_key = get_api_key('freja_gemini_apikey') or ""
        if not api_key:
            raise HTTPException(status_code=400, detail="The Gemini API key is not configured on the server.")

        # 5. Calculate trends
        trends = calculate_trends()
        trends_data_str = format_trends_summary(trends)

        # 5.4 Recent strength loads so the coach can apply progressive overload (Issue #34)
        strength_logs_str = format_recent_strength_logs()

        # 5.45 Open injuries so affected sessions get eased or swapped (Issue #38)
        injuries_str = format_active_injuries()

        # 5.47 A month of actually-completed training, so the plan progresses from what the
        # user has really been doing instead of jumping from a 20-minute jog to an hour.
        training_load = build_training_load_summary(TRAINING_LOAD_DAYS)
        training_load_str = format_training_load_summary(training_load)
        progression_rules = _format_progression_rules(training_load)

        # 5.5 Fetch 7-day weather forecast (for the profile's location)
        weather_forecast = await fetch_7day_weather_forecast(location)

        # 6. Compile Prompt
        garmin_data_str = "\n".join(garmin_summary) if garmin_summary else "No Garmin data available."
        strava_data_str = "\n".join(strava_summary) if strava_summary else "No Strava data available."
        # The 7-day blocks below stay short on purpose: they describe current recovery state.
        # The month of training volume the progression is built on comes from the
        # TRAINING LOAD block, which is aggregated rather than row-by-row.
        withings_data_str = "\n".join(withings_summary) if withings_summary else "No Withings data available."

        limitations_prompt = (
            f'\nINJURIES / ILLNESSES / LIMITATIONS:\n"{limitations}"\n'
            'Take particular account of these limitations, injuries or illnesses (e.g. exercise-induced '
            'asthma, knee injuries) and adapt the exercise selection and intensity accordingly.'
        ) if limitations else ""

        prompt_content = f"""
You are a professional personal trainer and health coach (COACH AI) integrated into the F.R.E.J.A. system.
Analyse the following health data, training data, trends and weather forecast for the user, and create a
tailored training plan or concrete training tips based on their stated goal.

GOAL: "{goal}"{limitations_prompt}

[CALCULATED HEALTH TRENDS (RHR & HRV)]:
{trends_data_str}

[WEATHER FORECAST FOR THE NEXT 7 DAYS]:
{weather_forecast}

[GARMIN HEALTH DATA (last 7 days)]:
{garmin_data_str}

[STRAVA WORKOUTS (most recent sessions)]:
{strava_data_str}

[WITHINGS MEASUREMENTS (last 7 measurements)]:
{withings_data_str}

[RECENTLY LOGGED STRENGTH LOADS (most recent per exercise)]:
{strength_logs_str}

[ACTIVE INJURY / PAIN LOG (dated entries the user is still bothered by)]:
{injuries_str}

[TRAINING LOAD - WHAT THE USER HAS ACTUALLY COMPLETED IN THE LAST {TRAINING_LOAD_DAYS} DAYS]:
{training_load_str}

[PROGRESSION LIMITS - THESE ARE NOT SUGGESTIONS]:
{progression_rules}

Instructions for the answer:
- Answer in Swedish.
- Write in an encouraging, professional and coaching tone (F.R.E.J.A. style: polite but extremely knowledgeable).
- Give concrete, practical advice on training intensity, recovery (look at sleep and HRV/recovery where available),
  and training modality based on the data.
- For strength sessions (Styrketräning), fill in the structured "exercises" list for that workout: name each
  exercise with target sets, reps and a target weight in kg (or an RPE if bodyweight/unloaded). Apply PROGRESSIVE
  OVERLOAD relative to the recently logged loads above - nudge weight or reps up slightly when recovery is good,
  and hold or reduce load when recovery is poor. Leave "exercises" empty for pure cardio/rest days.
- Take the coming week's weather forecast into account when planning the sessions:
  - If bad weather is expected (e.g. heavy rain, snowfall, thunderstorms or storms) on a planned training day,
    recommend indoor training or rest for that day.
  - If the user has asthma-related conditions (such as "astma" or "ansträngningsastma" in their limitations),
    pay extra attention to very cold days (e.g. apparent temperature below 0°C) combined with low humidity /
    dry air, and recommend indoor training or lower intensity to reduce the risk of asthma problems.
- Analyse the health trends. If the resting heart rate has risen sharply (>{RHR_ALERT_PCT}%) or HRV has dropped
  sharply (<{HRV_ALERT_PCT}%), add a clear recommendation for active rest or reduced intensity.
- Adapt the plan to the ACTIVE INJURY / PAIN LOG above: avoid or substitute exercises that load an
  affected area, lower the intensity of sessions that would aggravate it, and scale that caution to the
  logged severity (7-10 means avoid loading the area entirely). Mention the adaptation briefly in the
  session description so the user can see why it differs.
- Build the progression on the TRAINING LOAD block above and stay inside the PROGRESSION LIMITS.
  Every session duration you write must be justifiable against what the user has actually been
  doing over the past {TRAINING_LOAD_DAYS} days - a plan that doubles a session length is wrong
  even if the goal is ambitious. Briefly state in the summary which recent sessions you are
  progressing from.
- Create a simple weekly plan the user can follow right away.
"""

        # 7. Call Gemini
        google_url = build_generate_url(get_gemini_model(), api_key)
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt_content}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 4000,
                "responseMimeType": "application/json",
                # Every free-text field below is rendered straight into the HUD, so the schema
                # tells the model to fill them in Swedish. `day` must stay a Swedish weekday
                # name: book_plan_to_calendar() maps it back to a date via `day_offsets`.
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "summary": {
                            "type": "STRING",
                            "description": "A summarising analysis of the user's health status and training history, written in Swedish."
                        },
                        "resting_hr_trend": {
                            "type": "STRING",
                            "description": "Analysis of the resting heart rate trend (e.g. whether it has risen and indicates fatigue, or is stable). In Swedish."
                        },
                        "hrv_trend": {
                            "type": "STRING",
                            "description": "Analysis of the HRV trend (e.g. whether it has dropped and indicates under-recovery, or is good). In Swedish."
                        },
                        "weekly_focus": {
                            "type": "STRING",
                            "description": "The overall focus of this training week based on the goal and any limitations. In Swedish."
                        },
                        "workouts": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "day": {
                                        "type": "STRING",
                                        "description": "The day of the session. Must be one of the Swedish weekday names: Måndag, Tisdag, Onsdag, Torsdag, Fredag, Lördag, Söndag."
                                    },
                                    "activity_type": {
                                        "type": "STRING",
                                        "description": "Activity type in Swedish (e.g. Löpning, Styrketräning, Cykling, Yoga, Vila)."
                                    },
                                    "title": {
                                        "type": "STRING",
                                        "description": "Short descriptive title of the session, in Swedish."
                                    },
                                    "description": {
                                        "type": "STRING",
                                        "description": "Detailed instructions for the training session, in Swedish."
                                    },
                                    "duration_minutes": {
                                        "type": "INTEGER",
                                        "description": "Estimated time in minutes (0 for rest)."
                                    },
                                    "exercises": {
                                        "type": "ARRAY",
                                        "description": "Structured strength exercises for this session (empty for pure cardio/rest). Apply progressive overload against the logged loads.",
                                        "items": {
                                            "type": "OBJECT",
                                            "properties": {
                                                "name": {"type": "STRING", "description": "Exercise name in Swedish (e.g. Knäböj, Marklyft, Bänkpress)."},
                                                "sets": {"type": "INTEGER", "description": "Number of sets."},
                                                "reps": {"type": "INTEGER", "description": "Target reps per set."},
                                                "target_weight": {"type": "NUMBER", "description": "Target load in kg (0 for bodyweight/unloaded)."},
                                                "rpe": {"type": "NUMBER", "description": "Target rate of perceived exertion 1-10 (optional, 0 if not used)."}
                                            },
                                            "required": ["name", "sets", "reps"]
                                        }
                                    }
                                },
                                "required": ["day", "activity_type", "title", "description", "duration_minutes"]
                            }
                        }
                    },
                    "required": ["summary", "resting_hr_trend", "hrv_trend", "weekly_focus", "workouts"]
                }
            }
        }

        async with shared_client() as client:
            response = await client.post(google_url, json=payload, timeout=GEMINI_TIMEOUT_SECONDS)
            response.raise_for_status()
            res_json = response.json()

        advice_text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not advice_text:
            raise HTTPException(status_code=500, detail="Could not generate a response from Gemini.")

        # 8. Save to database
        today_str = today_local().strftime('%Y-%m-%d')
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trainer_plans (date, goal, advice_text, limitations)
                VALUES (?, ?, ?, ?)
            ''', (today_str, goal, advice_text, limitations))
            conn.commit()
            plan_id = cursor.lastrowid

        # 9. Automatically book the generated plan's workouts into the weekly schedule.
        # Anchored on this week's Monday because the plan's weekday names are offsets from
        # the start date; passing today would shift every session by today's weekday. Days
        # that already passed are skipped rather than booked into the past.
        booking = None
        try:
            booking = await core_book_plan_internal(plan_id, _current_week_monday())
        except Exception as book_err:
            print(f"[TRAINER GENERATE] Auto-booking warning: {book_err}")

        return {
            "status": "success",
            "plan_id": plan_id,
            "date": today_str,
            "goal": goal,
            "limitations": limitations,
            "advice_text": advice_text,
            "booking": booking
        }
        
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Guided onboarding -------------------------------------------------------
# The profile fields drive every plan, but a user cannot reasonably guess their own HRV
# baseline or describe their real weekly volume. Onboarding derives everything the connected
# devices already know (Garmin / Strava / Withings) and only asks about what data cannot
# reveal: intent, schedule, preferences, equipment and how the body actually feels.
ONBOARDING_LOOKBACK_DAYS = 90    # History window analysed before the interview
MAX_ONBOARDING_QUESTIONS = 8
MAX_ONBOARDING_ANSWER_LEN = 500

# The profile columns onboarding is allowed to write, and how to coerce each one.
ONBOARDING_TEXT_FIELDS = ("goals", "limitations", "fitness_level", "availability", "location", "event", "event_date")
ONBOARDING_NUMERIC_FIELDS = ("baseline_resting_hr", "baseline_sleep_hours", "baseline_hrv")


def _collect_onboarding_signals(days: int = ONBOARDING_LOOKBACK_DAYS) -> dict:
    """Everything the connected devices can tell us about the user, as prompt-ready text."""
    days = max(14, min(int(days or ONBOARDING_LOOKBACK_DAYS), 365))
    cutoff = (today_local() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')

    garmin_lines, withings_lines = [], []
    activity_types = {}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''SELECT COUNT(*), AVG(steps), AVG(sleep_hours), AVG(resting_hr), AVG(hrv),
                          AVG(sleep_score), MAX(vo2max)
                   FROM garmin_health WHERE date >= ?''',
                (cutoff,)
            )
            row = cursor.fetchone() or []
            if row and row[0]:
                def _fmt(v, unit, decimals=1):
                    return f"{round(v, decimals)}{unit}" if v else "no data"
                garmin_lines.append(
                    f"{row[0]} days of Garmin data: avg {_fmt(row[1], ' steps', 0)}, "
                    f"sleep {_fmt(row[2], 'h')}, resting HR {_fmt(row[3], ' bpm')}, "
                    f"HRV {_fmt(row[4], ' ms')}, sleep score {_fmt(row[5], '')}, "
                    f"VO2max {_fmt(row[6], '')}"
                )
            cursor.execute(
                '''SELECT training_status, COUNT(*) FROM garmin_health
                   WHERE date >= ? AND training_status IS NOT NULL AND training_status != ''
                   GROUP BY training_status ORDER BY COUNT(*) DESC LIMIT 5''',
                (cutoff,)
            )
            statuses = [f"{r[0]} ({r[1]} days)" for r in cursor.fetchall()]
            if statuses:
                garmin_lines.append("Garmin training status distribution: " + ", ".join(statuses))
        except Exception as e:
            print(f"[TRAINER ONBOARDING] Garmin read error: {e}")

        try:
            cursor.execute(
                '''SELECT type, COUNT(*), AVG(moving_time), MAX(distance), AVG(average_heartrate), MAX(max_heartrate)
                   FROM strava_activities WHERE SUBSTR(date, 1, 10) >= ?
                   GROUP BY type ORDER BY COUNT(*) DESC''',
                (cutoff,)
            )
            for a_type, count, avg_time, max_dist, avg_hr, max_hr in cursor.fetchall():
                activity_types[a_type or "Träning"] = {
                    "sessions": count,
                    "avg_minutes": round((avg_time or 0) / 60.0, 1),
                    "longest_km": round((max_dist or 0) / 1000.0, 2),
                    "avg_hr": round(avg_hr, 0) if avg_hr else None,
                    "max_hr": max_hr,
                }
        except Exception as e:
            print(f"[TRAINER ONBOARDING] Strava read error: {e}")

        try:
            cursor.execute(
                '''SELECT COUNT(*), AVG(weight), MIN(weight), MAX(weight), AVG(fat_ratio)
                   FROM withings_measurements WHERE date >= ?''',
                (cutoff,)
            )
            row = cursor.fetchone() or []
            if row and row[0]:
                withings_lines.append(
                    f"{row[0]} Withings measurements: weight avg {round(row[1], 1) if row[1] else 'n/a'} kg "
                    f"(range {round(row[2], 1) if row[2] else 'n/a'}-{round(row[3], 1) if row[3] else 'n/a'} kg), "
                    f"body fat avg {round(row[4], 1) if row[4] else 'n/a'}%"
                )
        except Exception as e:
            print(f"[TRAINER ONBOARDING] Withings read error: {e}")

    activity_lines = [
        f"- {t}: {v['sessions']} sessions, typically {v['avg_minutes']} min, longest {v['longest_km']} km, "
        f"avg HR {v['avg_hr'] or 'n/a'}, max HR {v['max_hr'] or 'n/a'}"
        for t, v in activity_types.items()
    ]

    load = build_training_load_summary(TRAINING_LOAD_DAYS)
    return {
        "window_days": days,
        "garmin": "\n".join(garmin_lines) or "No Garmin data available.",
        "withings": "\n".join(withings_lines) or "No Withings data available.",
        "strava": "\n".join(activity_lines) or "No Strava activities available.",
        "trends": format_trends_summary(calculate_trends()),
        "training_load": format_training_load_summary(load),
        "injuries": format_active_injuries(),
        "strength": format_recent_strength_logs(),
        "load": load,
        "activity_types": activity_types,
    }


async def _call_gemini_json(prompt: str, schema: dict, max_tokens: int = 3000) -> dict:
    """Posts a JSON-schema-constrained prompt to Gemini and returns the parsed object."""
    api_key = get_api_key('freja_gemini_apikey') or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="The Gemini API key is not configured on the server.")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    async with shared_client() as client:
        response = await client.post(build_generate_url(get_gemini_model(), api_key), json=payload, timeout=GEMINI_TIMEOUT_SECONDS)
        response.raise_for_status()
        res_json = response.json()

    text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    if not text:
        raise HTTPException(status_code=500, detail="Gemini returned an empty response.")
    try:
        return json.loads(text.replace("```json", "").replace("```", "").strip())
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Gemini returned malformed JSON: {e}")


_ONBOARDING_PROFILE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "goals": {"type": "STRING", "description": "Primary training goal, in Swedish."},
        "fitness_level": {"type": "STRING", "description": "One of exactly: beginner, intermediate, advanced."},
        "availability": {"type": "STRING", "description": "Weekly availability, e.g. '3 dagar/vecka, 45 min'. In Swedish."},
        "location": {"type": "STRING", "description": "City used for the weather forecast."},
        "event": {"type": "STRING", "description": "Target event, or empty string if none."},
        "event_date": {"type": "STRING", "description": "Event date as YYYY-MM-DD, or empty string."},
        "limitations": {"type": "STRING", "description": "Injuries, illnesses and limitations, in Swedish."},
        "baseline_resting_hr": {"type": "NUMBER", "description": "Resting HR baseline in bpm (0 if unknown)."},
        "baseline_sleep_hours": {"type": "NUMBER", "description": "Sleep baseline in hours (0 if unknown)."},
        "baseline_hrv": {"type": "NUMBER", "description": "HRV baseline in ms (0 if unknown)."},
    },
    "required": ["goals", "fitness_level", "availability", "location", "limitations"],
}


@router.post("/api/trainer/onboarding/start")
async def start_trainer_onboarding(request: Request):
    """Step 1 of onboarding: analyse the connected data and return the interview questions.

    Everything the devices can answer is filled in up front; the questions cover only what
    the data cannot know (intent, schedule, equipment, how the body actually feels)."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        days = int(body.get("days") or ONBOARDING_LOOKBACK_DAYS)

        signals = _collect_onboarding_signals(days)
        profile = get_trainer_profile()
        existing = ", ".join(
            f"{k}={v}" for k, v in profile.items()
            if k in ONBOARDING_TEXT_FIELDS + ONBOARDING_NUMERIC_FIELDS and v
        ) or "The profile is empty."

        prompt = f"""
You are COACH AI, F.R.E.J.A.'s personal trainer, running the onboarding interview for a new user.
Analyse the connected health and training data below, then prepare the interview.

[EXISTING TRAINING PROFILE]: {existing}

[GARMIN - last {signals['window_days']} days]:
{signals['garmin']}

[STRAVA ACTIVITY BREAKDOWN - last {signals['window_days']} days]:
{signals['strava']}

[WITHINGS BODY MEASUREMENTS - last {signals['window_days']} days]:
{signals['withings']}

[HEALTH TRENDS]:
{signals['trends']}

[COMPLETED TRAINING LOAD]:
{signals['training_load']}

[LOGGED INJURIES]:
{signals['injuries']}

[LOGGED STRENGTH LOADS]:
{signals['strength']}

Produce:
1. `data_summary`: a short Swedish analysis of what the data reveals about this user's current
   fitness, training habits, recovery and body composition. Be concrete and quote real figures.
2. `proposed_profile`: your best estimate of every profile field FROM THE DATA. Derive
   fitness_level from real training volume and heart-rate data, availability from how often
   they actually train, and the baselines from the measured averages. Use 0 for a baseline the
   data cannot support. Keep any existing value that the data does not contradict.
3. `questions`: {MAX_ONBOARDING_QUESTIONS} or fewer questions, in Swedish, covering ONLY what the data
   cannot answer - the user's actual goal and motivation, their real weekly schedule, access to
   gym/equipment, preferred activities, pain or health issues not yet logged, and any target
   event. Never ask about something the data already shows (do not ask "how often do you train"
   when the activity log answers it - instead confirm your reading of it). For each question
   give `field` (the profile field it informs: goals, availability, limitations, event,
   event_date, location, fitness_level, or "other") and `suggested_answer` (your data-based
   guess the user can accept as-is).
Answer every free-text field in Swedish.
"""

        schema = {
            "type": "OBJECT",
            "properties": {
                "data_summary": {"type": "STRING", "description": "Swedish analysis of the connected data."},
                "proposed_profile": _ONBOARDING_PROFILE_SCHEMA,
                "questions": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "id": {"type": "STRING", "description": "Short stable id, e.g. 'goal'."},
                            "question": {"type": "STRING", "description": "The question, in Swedish."},
                            "why": {"type": "STRING", "description": "One sentence on why it matters, in Swedish."},
                            "field": {"type": "STRING", "description": "Profile field this informs."},
                            "suggested_answer": {"type": "STRING", "description": "Data-based suggested answer, in Swedish."},
                        },
                        "required": ["id", "question", "field"],
                    },
                },
            },
            "required": ["data_summary", "proposed_profile", "questions"],
        }

        result = await _call_gemini_json(prompt, schema)
        result["questions"] = (result.get("questions") or [])[:MAX_ONBOARDING_QUESTIONS]
        result["status"] = "success"
        result["signals"] = {
            "window_days": signals["window_days"],
            "sessions_last_30_days": signals["load"].get("session_count", 0),
            "avg_weekly_minutes": signals["load"].get("avg_weekly_minutes", 0),
            "longest_session_minutes": signals["load"].get("longest_session_minutes", 0),
            "activity_types": signals["activity_types"],
        }
        return result

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _coerce_onboarding_profile(proposed: dict) -> dict:
    """Filters a model-proposed profile down to writable fields with sane values."""
    values = {}
    for f in ONBOARDING_TEXT_FIELDS:
        val = str((proposed or {}).get(f) or "").strip()[:MAX_INPUT_LEN]
        if val:
            values[f] = val
    if values.get("fitness_level", "").lower() not in ("beginner", "intermediate", "advanced"):
        values.pop("fitness_level", None)
    if "event_date" in values:
        try:
            datetime.datetime.strptime(values["event_date"][:10], "%Y-%m-%d")
            values["event_date"] = values["event_date"][:10]
        except ValueError:
            values.pop("event_date")  # An unparseable date would break the date input.
    for f in ONBOARDING_NUMERIC_FIELDS:
        raw = (proposed or {}).get(f)
        try:
            num = float(raw)
        except (TypeError, ValueError):
            continue
        if num > 0:  # 0 is the schema's "unknown", not a real baseline.
            values[f] = round(num, 1)
    return values


@router.post("/api/trainer/onboarding/complete")
async def complete_trainer_onboarding(request: Request):
    """Step 2 of onboarding: merge the user's answers with the data and save the profile."""
    try:
        body = await request.json()
        answers = body.get("answers") or []
        proposed = body.get("proposed_profile") or {}

        clean_answers = []
        for a in answers[:MAX_ONBOARDING_QUESTIONS]:
            question = str((a or {}).get("question") or "").strip()[:MAX_INPUT_LEN]
            answer = str((a or {}).get("answer") or "").strip()[:MAX_ONBOARDING_ANSWER_LEN]
            if question and answer:
                clean_answers.append({"question": question, "answer": answer, "field": str((a or {}).get("field") or "")})

        if not clean_answers:
            # Nothing to merge - persist the data-derived proposal as-is rather than failing.
            values = _coerce_onboarding_profile(proposed)
            if not values:
                raise HTTPException(status_code=400, detail="No answers and no usable proposed profile were provided.")
            saved = _save_profile_values(values)
            return {"status": "success", "profile": saved, "summary":
                    "Profilen sparades utifrån din data. Inga frågor besvarades.", "answers_used": 0}

        signals = _collect_onboarding_signals()
        answers_block = "\n".join(f"- {a['question']}\n  Svar: {a['answer']}" for a in clean_answers)

        prompt = f"""
You are COACH AI completing F.R.E.J.A.'s onboarding for a user.

[YOUR DATA-BASED PROPOSAL]:
{json.dumps(proposed, ensure_ascii=False)}

[THE USER'S ANSWERS]:
{answers_block}

[COMPLETED TRAINING LOAD]:
{signals['training_load']}

[HEALTH TRENDS]:
{signals['trends']}

Merge the answers with your data-based proposal into the final training profile.
The user's own answers WIN over your estimate for intent, schedule, limitations and events.
Your measured values win for the physiological baselines - do not let a guess overwrite a
measured resting HR, sleep or HRV baseline. Use 0 for a baseline the data cannot support.
Write `summary` as a short Swedish confirmation of what you understood and how the plan will
be shaped, mentioning the training volume the progression will start from.
Answer every free-text field in Swedish.
"""

        schema = {
            "type": "OBJECT",
            "properties": {
                "profile": _ONBOARDING_PROFILE_SCHEMA,
                "summary": {"type": "STRING", "description": "Swedish confirmation of the finished profile."},
            },
            "required": ["profile", "summary"],
        }

        result = await _call_gemini_json(prompt, schema)
        values = _coerce_onboarding_profile(result.get("profile") or {})
        if not values:
            raise HTTPException(status_code=500, detail="The onboarding produced no usable profile fields.")

        saved = _save_profile_values(values)
        return {
            "status": "success",
            "profile": saved,
            "summary": result.get("summary", ""),
            "answers_used": len(clean_answers),
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/trainer/plans")
async def update_trainer_plan(request: Request):
    try:
        body = await request.json()
        plan_id = body.get("plan_id")
        advice_text = body.get("advice_text")
        if not plan_id or advice_text is None:
            raise HTTPException(status_code=400, detail="A plan ID and the updated training plan are required.")
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE trainer_plans 
                SET advice_text = ? 
                WHERE id = ?
            ''', (advice_text, plan_id))
            conn.commit()
            
        return {"status": "success", "message": "The training plan was updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Shared workout-event helpers -------------------------------------------
# Markers that identify an auto-scheduled F.R.E.J.A. PT session in the calendar,
# so recovery-driven adjustments only ever touch training events (never meetings).
WORKOUT_LOCATION_MARKER = "F.R.E.J.A. PT"
WORKOUT_SUMMARY_MARKERS = ("💪", "🏃", "🚶", "🚴", "🧘", "🏊")


def _format_exercises_for_calendar(exercises) -> str:
    """Renders a workout's structured exercises (Issue #34) as a Swedish text block for
    the calendar description. Returns an empty string when there are none."""
    if not exercises or not isinstance(exercises, list):
        return ""
    lines = []
    for ex in exercises:
        if not isinstance(ex, dict):
            continue
        name = str(ex.get("name") or "").strip()
        if not name:
            continue
        try:
            sets = int(ex.get("sets") or 0)
        except (TypeError, ValueError):
            sets = 0
        try:
            reps = int(ex.get("reps") or 0)
        except (TypeError, ValueError):
            reps = 0
        try:
            weight = float(ex.get("target_weight") or 0)
        except (TypeError, ValueError):
            weight = 0
        try:
            rpe = float(ex.get("rpe") or 0)
        except (TypeError, ValueError):
            rpe = 0
        detail = f"{sets}x{reps}" if (sets or reps) else ""
        if weight > 0:
            detail += f" @ {weight:g} kg"
        elif rpe > 0:
            detail += f" @ RPE {rpe:g}"
        lines.append(f"- {name}: {detail}".rstrip())
    if not lines:
        return ""
    return "\n\nÖvningar (COACH AI):\n" + "\n".join(lines)


def is_workout_event(ev: dict) -> bool:
    """True if a calendar event looks like a F.R.E.J.A. PT training session."""
    summary = ev.get("summary") or ""
    location_val = ev.get("location") or ""
    return (WORKOUT_LOCATION_MARKER in location_val) or any(
        marker in summary for marker in WORKOUT_SUMMARY_MARKERS
    )


def _event_duration_minutes(ev: dict) -> int:
    """Minutes between an event's start and end (0 if unparseable / all-day)."""
    try:
        s = datetime.datetime.strptime((ev.get("start_time") or "")[:16], "%Y-%m-%dT%H:%M")
        e = datetime.datetime.strptime((ev.get("end_time") or "")[:16], "%Y-%m-%dT%H:%M")
        return max(0, int((e - s).total_seconds() // 60))
    except Exception:
        return 0


# --- Pre-check-in wearable refresh -------------------------------------------
# A check-in should reflect last night, not the last time a sync happened to run, so the
# very first thing it does is pull the freshest data from Garmin, Strava and Withings.
# The window is deliberately small (the briefing only cares about last night and
# yesterday) and the whole refresh is capped so a stalled provider can never hold up the
# briefing — the HUD proxy allows the check-in 300s and this stays well inside that.
CHECKIN_SYNC_DAYS = 3
CHECKIN_SYNC_TIMEOUT_SECONDS = 90.0


async def _sync_garmin_for_checkin(days: int) -> str:
    """Pull the most recent Garmin nights synchronously. Uses the lean blocking sync
    (not run_garmin_sync_flow) on purpose: the check-in adjusts today's session itself,
    so it must not also kick off the recovery optimizer and double-adjust the calendar."""
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    if not email or not password:
        return "skipped (no credentials)"
    from backend.routes.garmin import run_garmin_sync_task_blocking
    from backend.services.sync_status import set_sync_state
    set_sync_state("garmin", "syncing")
    try:
        await asyncio.to_thread(run_garmin_sync_task_blocking, email, password, days)
        set_sync_state("garmin", "success")
        return "synced"
    except Exception as e:
        set_sync_state("garmin", "error", str(e))
        return f"failed: {e}"


async def _sync_strava_for_checkin(days: int) -> str:
    """Pull recent Strava activities. run_strava_sync_task manages its own sync_state."""
    client_id = get_api_key('freja_strava_client_id') or ""
    client_secret = get_api_key('freja_strava_client_secret') or ""
    refresh_token = get_api_key('freja_strava_refresh_token') or ""
    if not client_id or not client_secret or not refresh_token:
        return "skipped (no credentials)"
    from backend.routes.strava import run_strava_sync_task
    try:
        await run_strava_sync_task(client_id, client_secret, refresh_token, days, False)
        return "synced"
    except Exception as e:
        return f"failed: {e}"


async def _sync_withings_for_checkin(days: int) -> str:
    """Pull recent Withings measurements. run_withings_sync_task manages its own state."""
    client_id = get_api_key('freja_withings_client_id') or ""
    client_secret = get_api_key('freja_withings_client_secret') or ""
    refresh_token = get_api_key('freja_withings_refresh_token') or ""
    if not client_id or not client_secret or not refresh_token:
        return "skipped (no credentials)"
    from backend.routes.withings import run_withings_sync_task
    try:
        await run_withings_sync_task(client_id, client_secret, refresh_token, days)
        return "synced"
    except Exception as e:
        return f"failed: {e}"


async def refresh_health_sources_for_checkin(days: int = CHECKIN_SYNC_DAYS) -> dict:
    """Fetch the latest Garmin/Strava/Withings data before the check-in reads the DB.

    All three run concurrently (WAL + a 30s busy timeout let their short write bursts
    serialise safely) and every failure is swallowed: a missing credential, an expired
    token or a network blip must still leave the check-in able to brief from whatever
    data is already stored. Returns a per-provider status map for logging/response.
    """
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                _sync_garmin_for_checkin(days),
                _sync_strava_for_checkin(days),
                _sync_withings_for_checkin(days),
                return_exceptions=True,
            ),
            timeout=CHECKIN_SYNC_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        print("[TRAINER CHECKIN] Health-source refresh timed out; using stored data.")
        return {"garmin": "timeout", "strava": "timeout", "withings": "timeout"}

    def _norm(r):
        return r if isinstance(r, str) else f"failed: {r}"

    return {
        "garmin": _norm(results[0]),
        "strava": _norm(results[1]),
        "withings": _norm(results[2]),
    }


@router.post("/api/trainer/checkin")
async def trainer_daily_checkin(request: Request):
    """Daily morning check-in (COACH AI): first pulls the freshest Garmin/Strava/Withings
    data, then reads last night's Garmin/Withings snapshot, checks today's calendar
    workout, verifies if yesterday's session was completed on Strava, weighs in the
    weather, and returns a short coaching briefing in Swedish."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        profile = get_trainer_profile()
        location = (body.get("location") or profile.get("location") or DEFAULT_LOCATION)
        location = str(location).strip() or DEFAULT_LOCATION

        today = today_local()
        today_str = today.strftime('%Y-%m-%d')
        yesterday_str = (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d')

        # 0. FIRST, pull the freshest data from the wearables so every snapshot below
        #    reflects last night rather than the last background sync. Non-fatal by design.
        sync_results = await refresh_health_sources_for_checkin()
        print(f"[TRAINER CHECKIN] Pre-check-in sync: {sync_results}")

        # 1. Latest Garmin snapshot (most recent night)
        garmin_snapshot = "No Garmin data available."
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, sleep_score
                FROM garmin_health
                ORDER BY date DESC
                LIMIT 1
            ''')
            g = cursor.fetchone()
        if g:
            garmin_snapshot = (
                f"Date: {g[0]}, Steps: {g[1]}, Sleep: {g[2]}h (Deep: {g[11]}h, REM: {g[13]}h, Light: {g[12]}h, Awake: {g[14]}h, Score: {g[15]}), Resting HR: {g[3]}, Calories: {g[4]}kcal, "
                f"Workout: {g[5]} ({g[6]} min), Body Battery: {g[7]}, HRV: {g[8]}ms, "
                f"Recovery time: {g[9]}h, Status: {g[10]}"
            )

        # 2. Latest Withings snapshot (fallback for sleep/RHR + body composition)
        withings_snapshot = "No Withings data available."
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, weight, fat_ratio, bone_mass, heart_pulse, sleep_duration, steps, calories, sleep_score
                FROM withings_measurements
                ORDER BY date DESC
                LIMIT 1
            ''')
            w = cursor.fetchone()
        if w:
            sleep_h = round(w[5] / 3600.0, 1) if w[5] else 0
            withings_snapshot = (
                f"Date: {w[0]}, Weight: {w[1]} kg, Body fat: {w[2]}%, Pulse: {w[4]} BPM, "
                f"Sleep: {sleep_h}h (Score: {w[8]}), Steps: {w[6]}, Calories: {w[7]}kcal"
            )

        # 3. Did yesterday's workout get completed? (Strava)
        completed_summary = "No workout was recorded on Strava yesterday."
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT name, type, distance, moving_time, average_heartrate
                FROM strava_activities
                WHERE SUBSTR(date, 1, 10) = ?
                ORDER BY date DESC
            ''', (yesterday_str,))
            strava_rows = cursor.fetchall()
        if strava_rows:
            parts = []
            for r in strava_rows:
                dist_km = round(r[2] / 1000.0, 2) if r[2] else 0
                dur_min = round(r[3] / 60.0, 1) if r[3] else 0
                parts.append(f"{r[0]} ({r[1]}, {dist_km} km, {dur_min} min, avg HR {r[4]})")
            completed_summary = "Completed yesterday: " + "; ".join(parts)

        # 4. Today's calendar: separate planned workouts from other commitments
        from backend.routes.google_calendar import core_get_calendar_data
        todays_events = [e for e in core_get_calendar_data(days=1) if (e.get("start_time") or "")[:10] == today_str]

        workout_events = [e for e in todays_events if is_workout_event(e)]
        other_events = [e for e in todays_events if not is_workout_event(e)]

        if workout_events:
            todays_plan_str = "\n".join(
                f"- {e.get('summary', '')} ({(e.get('start_time') or '')[11:16]}–{(e.get('end_time') or '')[11:16]}): {e.get('description', '')}"
                for e in workout_events
            )
        else:
            todays_plan_str = "No workout is booked in the calendar for today."

        other_events_str = "\n".join(
            f"- {e.get('summary', '')} ({(e.get('start_time') or '')[11:16]}–{(e.get('end_time') or '')[11:16]})"
            for e in other_events
        ) if other_events else "No other commitments in the calendar today."

        # 4b. The rest of this week's booked sessions (tomorrow .. +7 days), so the briefing
        #     can relate today to the plan and give a short outlook instead of only commenting
        #     on today. Local DB read via core_get_calendar_data; never fatal.
        week_end_str = (today + datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        upcoming_workout_events = [
            e for e in core_get_calendar_data(days=8)
            if today_str < (e.get("start_time") or "")[:10] <= week_end_str and is_workout_event(e)
        ]
        if upcoming_workout_events:
            week_plan_str = "\n".join(
                f"- {(e.get('start_time') or '')[:10]} {e.get('summary', '')} "
                f"({(e.get('start_time') or '')[11:16]}–{(e.get('end_time') or '')[11:16]}): {e.get('description', '')}"
                for e in upcoming_workout_events
            )
        else:
            week_plan_str = "No further sessions are booked for the rest of this week."

        # 5. Calculated RHR/HRV trends + adherence (reuses plan-generation logic)
        trends = calculate_trends()
        trends_data_str = format_trends_summary(trends)

        adherence = compute_adherence(14)
        if adherence["adherence_pct"] is not None:
            adherence_str = (
                f"Last {adherence['window_days']} days: {adherence['completed']} of "
                f"{adherence['planned']} booked sessions completed ({adherence['adherence_pct']}%)."
            )
        else:
            adherence_str = "No booked session history to compare against yet."

        # 6. Fetch Gemini API key (fail fast before any external weather call)
        api_key = get_api_key('freja_gemini_apikey') or ""
        if not api_key:
            raise HTTPException(status_code=400, detail="The Gemini API key is not configured on the server.")

        # 7. Today's weather (first line of the 7-day forecast is today)
        weather_forecast = await fetch_7day_weather_forecast(location)

        # 8. Compile the check-in prompt (follows docs/FREJA_PT_COACH.md)
        prompt_content = f"""
You are F.R.E.J.A.'s personal trainer (COACH AI). It is morning and the user is doing their daily check-in.
Give a SHORT, warm and practical morning briefing following the coach model. Do not dump the raw data -
interpret it. Write the entire answer in Swedish.

TODAY'S DATE: {today_str}

[LATEST GARMIN DATA (last night / last 24 hours)]:
{garmin_snapshot}

[LATEST WITHINGS DATA (fallback for sleep/resting HR plus body composition)]:
{withings_snapshot}

[CALCULATED HEALTH TRENDS (RHR & HRV)]:
{trends_data_str}

[TRAINING ADHERENCE]:
{adherence_str}

[ACTIVE INJURY / PAIN LOG]:
{format_active_injuries()}

[WORKOUT COMPLETED YESTERDAY (Strava)]:
{completed_summary}

[TODAY'S PLANNED WORKOUT (Google Calendar)]:
{todays_plan_str}

[OTHER COMMITMENTS IN THE CALENDAR TODAY]:
{other_events_str}

[REMAINING PLANNED SESSIONS THIS WEEK (Google Calendar, tomorrow onwards)]:
{week_plan_str}

[WEATHER FORECAST (the first line is today)]:
{weather_forecast}

Rules for the briefing:
- Prefer Garmin for sleep/resting HR/HRV/body battery; use Withings as a complement/fallback.
- If the injury/pain log has active entries, take them into account for today's session: suggest
  easing or swapping the session when it would load an affected area, and ask briefly how it feels.
- Assess recovery: if the resting heart rate has risen sharply (>{RHR_ALERT_PCT:.0f}%) or HRV has dropped
  sharply (<{HRV_ALERT_PCT:.0f}%), or if sleep was short/poor or Body Battery is low, recommend lower
  intensity or active rest and briefly explain why.
- On good recovery: encourage the user and keep (or slightly extend) today's plan.
- Relate today's session to the overall plan and goal: say briefly whether the user is on track,
  ahead, or should ease off - i.e. how today's session fits the plan, not just what it is.
- Close the briefing with a SHORT outlook (1-2 sentences) on the remaining sessions this week from
  [REMAINING PLANNED SESSIONS THIS WEEK]: what is coming up and any early adjustment recovery suggests.
  If nothing is booked for the rest of the week, say so and suggest what to add.
- If yesterday's session was NOT completed: no guilt - suggest shifting it naturally if needed.
- If today's session is outdoors and bad weather (heavy rain, snow, thunderstorms, storms) is expected:
  suggest indoor training or rest.
- Take other calendar commitments into account, since they affect available energy and time today.
- If you set adjust_workout=true AND a session is booked today: set 'adjusted_duration_minutes' to the new
  length in minutes (integer, 0 = rest). F.R.E.J.A. then automatically rebooks today's calendar session.
- ALWAYS end with a clear question or action. Be polite but extremely knowledgeable (F.R.E.J.A. style).
- The 'briefing' field must be a finished, short markdown text that can be shown directly to the user
  (feel free to use emojis 📊 📅 💬 ✅ as in the coach model).
- Format the briefing with short **bold** labels, short paragraphs, emojis and simple bullet lists only.
  Do NOT use markdown headings (#, ##): the HUD does not render them and they would show as literal '##'.
"""

        # 9. Call Gemini with a structured schema
        google_url = build_generate_url(get_gemini_model(), api_key)
        payload = {
            "contents": [{"parts": [{"text": prompt_content}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 1500,
                "responseMimeType": "application/json",
                # All STRING fields are shown to the user as-is, so the model fills them in Swedish.
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "sleep_summary": {"type": "STRING", "description": "Short summary of last night's sleep, in Swedish."},
                        "recovery_summary": {"type": "STRING", "description": "Assessment of resting HR, HRV and Body Battery/recovery, in Swedish."},
                        "yesterday_status": {"type": "STRING", "description": "Whether yesterday's session was completed or missed, without blame. In Swedish."},
                        "todays_plan": {"type": "STRING", "description": "Today's planned workout in plain language, in Swedish."},
                        "recommendation": {"type": "STRING", "description": "The coach's recommendation: keep, lower or raise the intensity, with a short rationale, tied to how today's session fits the plan. In Swedish."},
                        "adjust_workout": {"type": "BOOLEAN", "description": "true if today's session should be adjusted compared with what is booked in the calendar."},
                        "adjusted_duration_minutes": {"type": "INTEGER", "description": "New length in minutes for today's session if adjust_workout=true (0 = rest). Omitted/0 if no adjustment."},
                        "weather_note": {"type": "STRING", "description": "Short weather comment relevant to today's session (empty string if not relevant). In Swedish."},
                        "week_outlook": {"type": "STRING", "description": "A short 1-2 sentence outlook on the remaining planned sessions this week and any early adjustment recovery suggests. In Swedish."},
                        "closing_question": {"type": "STRING", "description": "A clear closing question or action for the user, in Swedish."},
                        "briefing": {"type": "STRING", "description": "Finished short briefing in markdown, ready to display directly to the user. Must cover how today's session fits the plan, how recovery looks, and the outlook for the rest of the week. In Swedish."}
                    },
                    "required": ["sleep_summary", "recovery_summary", "yesterday_status", "todays_plan", "recommendation", "adjust_workout", "closing_question", "briefing"]
                }
            }
        }

        async with shared_client() as client:
            response = await client.post(google_url, json=payload, timeout=GEMINI_TIMEOUT_SECONDS)
            response.raise_for_status()
            res_json = response.json()

        briefing_text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not briefing_text:
            raise HTTPException(status_code=500, detail="Could not generate the check-in from Gemini.")

        try:
            briefing_data = json.loads(briefing_text)
        except Exception:
            briefing_data = {"briefing": briefing_text}

        # 10. Act on the recommendation: if the coach wants to adjust today's session
        #     and there is a workout event in the calendar, re-time it automatically.
        calendar_updated = False
        if briefing_data.get("adjust_workout") and workout_events:
            try:
                new_dur = int(briefing_data.get("adjusted_duration_minutes") or 0)
            except (TypeError, ValueError):
                new_dur = 0
            if 0 < new_dur <= MAX_WORKOUT_MINUTES:
                ev = workout_events[0]
                start_time = (ev.get("start_time") or "")[:16]  # YYYY-MM-DDTHH:MM
                try:
                    start_dt = datetime.datetime.strptime(start_time, "%Y-%m-%dT%H:%M")
                    end_time = (start_dt + datetime.timedelta(minutes=new_dur)).strftime("%Y-%m-%dT%H:%M")
                    from backend.routes.google_calendar import core_save_calendar_event
                    # Calendar text is read by the user, so it stays Swedish. The "[COACH AI"
                    # marker is also the split point above, which keeps repeated adjustments
                    # from stacking one annotation on top of another.
                    base_desc = (ev.get("description") or "").split("\n\n[COACH AI")[0]
                    new_desc = f"{base_desc}\n\n[COACH AI justerade passet till {new_dur} min baserat på din återhämtning ({today_str}).]"
                    await core_save_calendar_event(
                        summary=ev.get("summary", "Träningspass"),
                        start_time=start_time,
                        end_time=end_time,
                        description=new_desc,
                        location=ev.get("location", ""),
                        db_id=ev.get("id")
                    )
                    calendar_updated = True
                except Exception as adj_err:
                    print(f"[TRAINER CHECKIN] Could not adjust the calendar session: {adj_err}")

        return {
            "status": "success",
            "date": today_str,
            "checkin": briefing_data,
            "has_workout_today": bool(workout_events),
            "workout_completed_yesterday": bool(strava_rows),
            "adherence": adherence,
            "calendar_updated": calendar_updated,
            "sync": sync_results
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def core_optimize_upcoming_workouts(
    location: str = None, days_ahead: int = 7, trigger: str = "manual"
) -> dict:
    """Re-tunes the upcoming F.R.E.J.A. PT sessions in the calendar to the user's
    latest recovery data.

    Reads the most recent Garmin snapshot plus the RHR/HRV trends, pulls every
    workout event from today through ``days_ahead`` days out, and asks COACH AI
    whether each one is appropriate given sleep/HRV/recovery and the user's goal.
    Sessions that would risk injury or over-training are shortened, de-loaded, or
    turned into active rest — directly in Google Calendar. Good recovery leaves
    the plan untouched. Returns a summary of what changed (empty if nothing did).
    """
    profile = get_trainer_profile()
    goal = str(profile.get("goals") or profile.get("event") or "").strip()[:MAX_INPUT_LEN]
    limitations = str(profile.get("limitations") or "").strip()[:MAX_INPUT_LEN]

    today = today_local()
    today_str = today.strftime("%Y-%m-%d")
    horizon_str = (today + datetime.timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # Upcoming workouts (today .. horizon) that F.R.E.J.A. booked.
    from backend.routes.google_calendar import core_get_calendar_data, core_save_calendar_event
    all_events = core_get_calendar_data(days=max(days_ahead, 1))
    upcoming = [
        e for e in all_events
        if is_workout_event(e) and today_str <= (e.get("start_time") or "")[:10] <= horizon_str
    ]
    upcoming.sort(key=lambda e: (e.get("start_time") or ""))

    if not upcoming:
        return {
            "status": "no_workouts",
            "trigger": trigger,
            "assessment": "",
            # The briefing is displayed verbatim to the user, so it is written in Swedish.
            "briefing": "Inga inbokade träningspass hittades för den kommande perioden, så inget behövde justeras.",
            "changes": [],
            "changes_count": 0,
            "considered": 0,
        }

    # Latest Garmin recovery snapshot + calculated trends.
    garmin_snapshot = "No Garmin data available."
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, sleep_score
            FROM garmin_health
            ORDER BY date DESC
            LIMIT 1
        ''')
        g = cursor.fetchone()
    if g:
        garmin_snapshot = (
            f"Date: {g[0]}, Steps: {g[1]}, Sleep: {g[2]}h (Deep: {g[11]}h, REM: {g[13]}h, Light: {g[12]}h, Awake: {g[14]}h, Score: {g[15]}), Resting HR: {g[3]}, Calories: {g[4]}kcal, "
            f"Workout: {g[5]} ({g[6]} min), Body Battery: {g[7]}, HRV: {g[8]}ms, "
            f"Recovery time: {g[9]}h, Status: {g[10]}"
        )

    trends = calculate_trends()
    trends_data_str = format_trends_summary(trends)

    api_key = get_api_key('freja_gemini_apikey') or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="The Gemini API key is not configured on the server.")

    # Compile the upcoming workouts for the prompt (id lets us map adjustments back).
    workout_lines = []
    for e in upcoming:
        dur = _event_duration_minutes(e)
        first_desc_line = ""
        if e.get("description"):
            first_desc_line = (e.get("description") or "").splitlines()[0][:120]
        workout_lines.append(
            f"- id={e.get('id')} | {(e.get('start_time') or '')[:10]} kl {(e.get('start_time') or '')[11:16]} | "
            f"\"{e.get('summary', '')}\" | {dur} min | {first_desc_line}"
        )
    workouts_str = "\n".join(workout_lines)

    limitations_prompt = (
        f'\nINJURIES / ILLNESSES / LIMITATIONS: "{limitations}"\n'
        "Take particular account of these when choosing intensity and sessions."
        if limitations else ""
    )
    goal_str = goal or "No specific goal given - prioritise health and sustainable progression."

    prompt_content = f"""
You are F.R.E.J.A.'s personal trainer (COACH AI). Your task now is to review the user's ALREADY BOOKED
upcoming training sessions against their LATEST recovery data and decide whether anything needs to be
adjusted to avoid injury or overtraining - while the sessions still lead towards the goal.

GOAL: "{goal_str}"{limitations_prompt}

[LATEST GARMIN DATA (last 24 hours)]:
{garmin_snapshot}

[CALCULATED HEALTH TRENDS (RHR & HRV)]:
{trends_data_str}

[ACTIVE INJURY / PAIN LOG (dated entries the user is still bothered by)]:
{format_active_injuries()}

[BOOKED UPCOMING WORKOUTS (today through {horizon_str})]:
{workouts_str}

Rules:
- Assess recovery from sleep, resting heart rate (RHR), HRV, Body Battery, recovery time and training status.
- Treat the ACTIVE INJURY / PAIN LOG as a hard constraint: a session that would load an affected area must
  be reduced, or turned into active rest / an alternative modality when the logged severity is high (7-10).
  Say so in "reason" so the calendar records why.
- If recovery is POOR (e.g. RHR up sharply >{RHR_ALERT_PCT:.0f}%, HRV down sharply <{HRV_ALERT_PCT:.0f}%,
  short/poor sleep, low Body Battery, long recovery time, or a training status of "Övertränad",
  "Oproduktiv" or "Ansträngd" - the Garmin sync stores these in Swedish): reduce the length and/or
  intensity of the nearest sessions, or turn a hard session into active rest.
- If recovery is GOOD: keep the sessions as they are (action="keep"). NEVER reduce without cause.
- Never increase a single session by more than ~10-15%. Prioritise health over pushing towards the goal.
- For EVERY booked session above, return one entry in "adjustments" with exactly the same event_id (integer).
- action: "keep" (no change), "reduce" (shorter/easier session), or "rest" (turn into active rest / light mobility).
- new_duration_minutes: the session's new length in minutes (for "keep" = the current length; for "rest" = a
  short easy session, e.g. 15-25).
- new_title: an optional new title, written in Swedish (e.g. "🧘 Aktiv vila: rörlighet" for rest, or
  "🏃 Lugn löptur" when scaling down). Leave empty to keep the current title.
- reason: one short Swedish sentence explaining why (it is shown in the calendar).
- briefing: a finished SHORT markdown summary, in Swedish, shown directly to the user - what you changed and
  why (or that everything can stay as it is).
"""

    google_url = build_generate_url(get_gemini_model(), api_key)
    payload = {
        "contents": [{"parts": [{"text": prompt_content}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1500,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "assessment": {"type": "STRING", "description": "Short assessment of the recovery status, in Swedish."},
                    "briefing": {"type": "STRING", "description": "Finished short briefing in markdown, ready to show to the user. In Swedish."},
                    "adjustments": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "event_id": {"type": "INTEGER", "description": "Calendar id of the session (from the list above)."},
                                "action": {"type": "STRING", "description": "keep, reduce or rest."},
                                "new_duration_minutes": {"type": "INTEGER", "description": "The session's new length in minutes."},
                                "new_title": {"type": "STRING", "description": "Optional new title in Swedish (empty = keep the existing one)."},
                                "reason": {"type": "STRING", "description": "Short rationale, written in Swedish (shown in the calendar)."}
                            },
                            "required": ["event_id", "action", "new_duration_minutes", "reason"]
                        }
                    }
                },
                "required": ["assessment", "briefing", "adjustments"]
            }
        }
    }

    async with shared_client() as client:
        response = await client.post(google_url, json=payload, timeout=GEMINI_TIMEOUT_SECONDS)
        response.raise_for_status()
        res_json = response.json()

    opt_text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    if not opt_text:
        raise HTTPException(status_code=500, detail="Could not generate the optimization from Gemini.")

    try:
        opt_data = json.loads(opt_text)
    except Exception:
        opt_data = {"assessment": "", "briefing": opt_text, "adjustments": []}

    by_id = {e.get("id"): e for e in upcoming}
    changes = []
    for adj in (opt_data.get("adjustments") or []):
        try:
            eid = int(adj.get("event_id"))
        except (TypeError, ValueError):
            continue
        ev = by_id.get(eid)
        if not ev:
            continue

        action = str(adj.get("action") or "keep").strip().lower()
        if action == "keep":
            continue

        current_dur = _event_duration_minutes(ev)
        try:
            new_dur = int(adj.get("new_duration_minutes") or 0)
        except (TypeError, ValueError):
            new_dur = 0
        if action == "rest" and new_dur <= 0:
            new_dur = 20  # light active-recovery default
        new_dur = max(1, min(new_dur, MAX_WORKOUT_MINUTES))
        if action != "rest" and new_dur == current_dur:
            continue  # nothing to actually change

        start_time = (ev.get("start_time") or "")[:16]
        try:
            start_dt = datetime.datetime.strptime(start_time, "%Y-%m-%dT%H:%M")
        except ValueError:
            continue
        end_time = (start_dt + datetime.timedelta(minutes=new_dur)).strftime("%Y-%m-%dT%H:%M")

        orig_summary = ev.get("summary", "") or "Träningspass"
        new_title = str(adj.get("new_title") or "").strip()
        if action == "rest":
            summary = new_title or f"🧘 Aktiv vila (tidigare: {orig_summary})"
        else:
            summary = new_title or orig_summary
        reason = str(adj.get("reason") or "").strip()

        base_desc = (ev.get("description") or "").split("\n\n[COACH AI optimerade")[0]
        new_desc = (
            f"{base_desc}\n\n[COACH AI optimerade passet {current_dur}→{new_dur} min "
            f"({today_str}): {reason}]"
        )

        try:
            await core_save_calendar_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                description=new_desc,
                location=ev.get("location", ""),
                db_id=ev.get("id"),
            )
            changes.append({
                "event_id": eid,
                "date": start_time[:10],
                "action": action,
                "from_minutes": current_dur,
                "to_minutes": new_dur,
                "title": summary,
                "reason": reason,
            })
        except Exception as save_err:
            print(f"[TRAINER OPTIMIZE] Could not update event {eid}: {save_err}")

    return {
        "status": "success",
        "trigger": trigger,
        "assessment": opt_data.get("assessment", ""),
        "briefing": opt_data.get("briefing", ""),
        "changes": changes,
        "changes_count": len(changes),
        "considered": len(upcoming),
    }


@router.post("/api/trainer/optimize")
async def optimize_trainer_workouts(request: Request):
    """Manually trigger COACH AI's recovery-driven re-tuning of upcoming workouts."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        location = body.get("location")
        try:
            days_ahead = int(body.get("days_ahead") or 7)
        except (TypeError, ValueError):
            days_ahead = 7
        days_ahead = max(1, min(days_ahead, 28))
        return await core_optimize_upcoming_workouts(location=location, days_ahead=days_ahead, trigger="manual")
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _find_free_slot(workout_date: datetime.date, duration: int, day_events: list) -> datetime.datetime:
    """Finds a start time on workout_date that doesn't overlap existing events.

    Starts at the preferred hour and, on a clash, jumps to the end of the
    conflicting event, retrying until the day-end limit. Falls back to the
    preferred hour if no free slot fits.
    """
    dur = datetime.timedelta(minutes=duration)
    start = datetime.datetime.combine(workout_date, datetime.time(DEFAULT_WORKOUT_HOUR, 0))
    end_limit = datetime.datetime.combine(workout_date, datetime.time(DAY_END_HOUR, 0))

    intervals = []
    for e in day_events:
        try:
            s = datetime.datetime.strptime((e.get("start_time") or "")[:16], "%Y-%m-%dT%H:%M")
            en = datetime.datetime.strptime((e.get("end_time") or "")[:16], "%Y-%m-%dT%H:%M")
            intervals.append((s, en))
        except Exception:
            continue  # all-day / malformed events don't block scheduling
    intervals.sort()

    while start + dur <= end_limit:
        candidate_end = start + dur
        conflict_end = None
        for (s, en) in intervals:
            if start < en and candidate_end > s:  # overlap
                conflict_end = en
                break
        if conflict_end is None:
            return start
        start = conflict_end

    return datetime.datetime.combine(workout_date, datetime.time(DEFAULT_WORKOUT_HOUR, 0))


async def core_book_plan_internal(plan_id: int, start_date: datetime.date, skip_past: bool = True) -> dict:
    """Books a plan's workouts into the calendar, anchored on `start_date`.

    `start_date` must be a Monday: the plan's Swedish weekday names are turned into absolute
    offsets from it (Måndag = 0). `skip_past` drops sessions that would land before today,
    which is what makes booking a plan mid-week sane - a plan generated on Thursday keeps
    Friday/Saturday/Sunday and silently forgets the Monday session that already passed.
    Callers that deliberately book a historical window pass skip_past=False.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT advice_text FROM trainer_plans WHERE id = ?", (plan_id,))
        row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="The training plan was not found.")

    try:
        plan_data = json.loads(row[0])
    except Exception:
        raise HTTPException(status_code=400, detail="This training plan has no structured data and cannot be booked into the calendar.")

    workouts = plan_data.get("workouts", [])
    if not workouts:
        return {"status": "success", "message": "No workouts to book.", "booked_count": 0, "replaced_count": 0}

    # The plan's `day` field is a Swedish weekday name (enforced by the generate schema).
    # Offsets are relative to the plan's start_date, which the caller supplies. The
    # mapping is shared with the plan export so both schedule a plan identically.
    day_offsets = plan_export.SWEDISH_DAY_OFFSETS

    from backend.routes.google_calendar import (
        core_save_calendar_event, core_delete_calendar_event, core_get_calendar_data
    )

    # --- Idempotency: remove any events previously booked for THIS plan so
    #     re-booking updates instead of creating duplicates. ---
    rebooked = 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, event_id FROM trainer_bookings WHERE plan_id = ?", (plan_id,))
        prior = cursor.fetchall()
    for booking_id, event_id in prior:
        if event_id:
            try:
                await core_delete_calendar_event(event_id)
                rebooked += 1
            except Exception as del_err:
                print(f"[TRAINER BOOK] Could not delete the previous event {event_id}: {del_err}")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_bookings WHERE plan_id = ?", (plan_id,))
        conn.commit()

    # Existing calendar events used for conflict avoidance (mutated as we book).
    all_events = core_get_calendar_data(days=60)

    booked_count = 0
    skipped_past = 0
    for w in workouts:
        day_name = str(w.get("day", "")).lower()
        offset = day_offsets.get(day_name)
        if offset is None:
            continue

        try:
            duration = int(w.get("duration_minutes", 0) or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            continue  # Skip rest day
        duration = min(duration, MAX_WORKOUT_MINUTES)  # Sanity cap

        try:
            week = max(0, min(51, int(w.get("week", 0) or 0)))
        except (TypeError, ValueError):
            week = 0

        workout_date = start_date + datetime.timedelta(days=offset + week * 7)
        if skip_past and workout_date < today_local():
            skipped_past += 1
            continue

        # Find a non-conflicting slot; format at minute precision so the
        # Google push (which appends ":00") produces a valid RFC3339 time.
        day_events = [e for e in all_events if (e.get("start_time") or "")[:10] == workout_date.isoformat()]
        slot_start = _find_free_slot(workout_date, duration, day_events)
        slot_end = slot_start + datetime.timedelta(minutes=duration)
        start_dt = slot_start.strftime("%Y-%m-%dT%H:%M")
        end_dt = slot_end.strftime("%Y-%m-%dT%H:%M")

        # Event title/description land in the user's calendar, so they stay Swedish.
        # The 💪 prefix and the "F.R.E.J.A. PT" location are what is_workout_event()
        # later matches on to tell PT sessions apart from ordinary meetings.
        summary = f"💪 {w.get('activity_type', 'Träning')}: {w.get('title', 'Pass')}"
        exercises_block = _format_exercises_for_calendar(w.get("exercises"))
        description = (
            f"Träningspass genererat av COACH AI.\n\nBeskrivning:\n{w.get('description', '')}"
            f"{exercises_block}\n\nTid: {duration} minuter."
        )
        location = WORKOUT_LOCATION_MARKER

        result = await core_save_calendar_event(
            summary=summary,
            start_time=start_dt,
            end_time=end_dt,
            description=description,
            location=location
        )
        event_id = (result.get("event") or {}).get("id")

        # Record the booking so it can be de-duplicated / adjusted later.
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO trainer_bookings (plan_id, event_id, workout_date, week) VALUES (?, ?, ?, ?)",
                (plan_id, event_id, workout_date.isoformat(), week)
            )
            conn.commit()

        # Make this new event visible to the next same-day workout.
        all_events.append({"start_time": f"{start_dt}:00", "end_time": f"{end_dt}:00"})
        booked_count += 1

    msg = f"Successfully booked {booked_count} workouts into your calendar."
    if rebooked:
        msg += f" ({rebooked} previously booked sessions were replaced.)"
    if skipped_past:
        msg += f" ({skipped_past} sessions fell before today and were skipped.)"
    return {
        "status": "success",
        "message": msg,
        "booked_count": booked_count,
        "replaced_count": rebooked,
        "skipped_past_count": skipped_past,
    }


@router.post("/api/trainer/plans/book")
async def book_trainer_plan(request: Request):
    try:
        body = await request.json()
        plan_id = body.get("plan_id")
        start_date_str = body.get("start_date")

        if not plan_id or not start_date_str:
            raise HTTPException(status_code=400, detail="A plan ID and a start date are required.")

        try:
            start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start date format (use YYYY-MM-DD).")

        # An explicitly requested start date is honoured in full, including sessions in the
        # past - the caller picked that date deliberately (e.g. re-booking a past week).
        return await core_book_plan_internal(plan_id, start_date, skip_past=False)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
