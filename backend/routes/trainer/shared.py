"""Shared constants and helpers used by 3+ trainer route groups.

Split out of the former monolithic backend/routes/trainer.py so each route group (profile,
plans, generation, checkin, optimize, booking) can live in its own file without duplicating
the health-data formatting, weather fetch, or booking-cleanup logic they all depend on.
"""

import datetime
import urllib.parse
from backend.services.http_client import shared_client
from backend.database import get_db_connection
from backend.services.weather_codes import describe_weather_code
from backend.services.time_utils import today_local

# --- Configuration constants -------------------------------------------------
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
    """Returns the most recent logged strength sets, newest first, as dicts.

    Deduped on (date, exercise_name): if the same session was both logged by hand and
    auto-imported from Garmin (Issue #183), the Garmin row wins as the more accurate record
    and the manual duplicate is dropped, so the coach doesn't see the session twice."""
    limit = max(1, min(int(limit or 40), MAX_STRENGTH_LOGS))
    with get_db_connection() as conn:
        conn.row_factory = _dict_row
        cursor = conn.cursor()
        try:
            # Over-fetch before deduping, since a duplicate pair collapses to one row;
            # MAX_STRENGTH_LOGS caps how far this can grow on a single call.
            fetch_limit = min(limit * 2, MAX_STRENGTH_LOGS)
            cursor.execute(
                '''SELECT id, date, exercise_name, sets, reps, weight, rpe, notes, plan_id,
                          created_at, source, activity_id
                   FROM trainer_strength_logs
                   ORDER BY date DESC, id DESC
                   LIMIT ?''',
                (fetch_limit,)
            )
            rows = [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            print(f"[TRAINER STRENGTH] Error reading strength logs: {e}")
            return []

    by_key: dict = {}
    ordered_keys = []
    for row in rows:
        key = (row.get("date"), (row.get("exercise_name") or "").strip().lower())
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = row
            ordered_keys.append(key)
        elif existing.get("source") != "garmin" and row.get("source") == "garmin":
            by_key[key] = row
    return [by_key[k] for k in ordered_keys][:limit]


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
            # Reads garmin_activities (one row per session, see #177) rather than the
            # garmin_health same-day rollup, so a multi-session day counts every session
            # instead of only the day's dominant one.
            cursor.execute(
                '''SELECT date, type, duration_minutes, distance_m, avg_hr
                   FROM garmin_activities
                   WHERE date >= ?
                   ORDER BY date ASC''',
                (cutoff_str,)
            )
            for d, a_type, minutes, distance_m, avg_hr in cursor.fetchall():
                if not d or d[:10] in strava_dates:
                    continue  # Strava already has this day; don't double-count it.
                minutes = round(float(minutes or 0), 1)
                if minutes <= 0 or not a_type:
                    continue
                sessions.append({
                    "date": d[:10],
                    "type": str(a_type).strip(),
                    "minutes": minutes,
                    "distance_km": round((distance_m or 0) / 1000.0, 2) if distance_m else None,
                    "avg_hr": avg_hr,
                    "source": "Garmin",
                })
        except Exception as e:
            print(f"[TRAINER LOAD] Error reading Garmin activities: {e}")

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

    # Garmin's own training load (#179): CTL/ATL/ACWR from the most recent day that has a
    # reading, rather than the minute-based proxy above. TSB ("form") is derived here rather
    # than read from a stored column, so it can never drift from chronic/acute.
    latest_load = None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''SELECT date, training_load_acute, training_load_chronic, acwr, acwr_status,
                          load_aerobic_low, load_aerobic_high, load_anaerobic
                   FROM garmin_health
                   WHERE training_load_acute IS NOT NULL OR training_load_chronic IS NOT NULL
                   ORDER BY date DESC LIMIT 1'''
            )
            row = cursor.fetchone()
            if row:
                d, acute, chronic, acwr_val, acwr_status, aero_low, aero_high, anaerobic = row
                latest_load = {
                    "date": d,
                    "ctl": chronic,
                    "atl": acute,
                    "tsb": round(chronic - acute, 1) if chronic is not None and acute is not None else None,
                    "acwr": acwr_val,
                    "acwr_status": acwr_status,
                    "load_aerobic_low": aero_low,
                    "load_aerobic_high": aero_high,
                    "load_anaerobic": anaerobic,
                }
        except Exception as e:
            print(f"[TRAINER LOAD] Error reading Garmin training load: {e}")

    # Weekly easy/hard HR-zone split (#184): a number that is normal tells the model
    # nothing; the point is spotting when the split drifts from the 80/20-style target, so
    # this is read as a deviation signal rather than resident every turn (see #189).
    weekly_zone_split = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''SELECT a.date, z.secs_zone_1, z.secs_zone_2, z.secs_zone_3, z.secs_zone_4, z.secs_zone_5
                   FROM garmin_activity_zones z
                   JOIN garmin_activities a ON a.activity_id = z.activity_id
                   WHERE a.date >= ?
                   ORDER BY a.date ASC''',
                (cutoff_str,)
            )
            from backend.routes.garmin import zone_percentages
            zone_weeks: dict = {}
            for d, z1, z2, z3, z4, z5 in cursor.fetchall():
                try:
                    parsed = datetime.datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    continue
                bucket = (today - parsed).days // 7
                wk = zone_weeks.setdefault(bucket, [0, 0, 0, 0, 0])
                for i, z in enumerate((z1, z2, z3, z4, z5)):
                    wk[i] += z or 0
            for bucket, totals in sorted(zone_weeks.items()):
                pct = zone_percentages(*totals)
                if pct["easy_pct"] is not None:
                    weekly_zone_split.append({"weeks_ago": bucket, **pct})
        except Exception as e:
            print(f"[TRAINER LOAD] Error reading Garmin HR zones: {e}")

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
        "latest_load": latest_load,
        "weekly_zone_split": weekly_zone_split,
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

    # ACWR guardrail (#179): Garmin's own injury-risk ratio, alongside the minute-based
    # ceilings above rather than replacing them - the percentage caps still protect against
    # a bad ACWR reading, and ACWR still protects against a volume jump the minute caps alone
    # would allow if intensity (not just duration) is what spiked.
    latest_load = load.get("latest_load")
    if latest_load and latest_load.get("acwr") is not None:
        rules.append(
            f"- Garmin's acute:chronic workload ratio (ACWR) is {latest_load['acwr']} "
            f"({latest_load.get('acwr_status') or 'no status given'}). A ratio above ~1.5 is "
            "the classic overreaching/injury-risk signal - if it is elevated, favor the "
            "conservative end of the ceilings above rather than the aggressive end, "
            "regardless of how much minute-budget is technically available."
        )

    # Intensity guardrail (#184): volume is capped above; intensity was entirely uncapped,
    # which is the more common way to get injured. Only surfaced when the most recent week
    # is outside the 80/20-style target band - a normal split says nothing worth stating.
    zone_split = load.get("weekly_zone_split") or []
    if zone_split:
        latest_week = min(zone_split, key=lambda w: w["weeks_ago"])
        if latest_week["easy_pct"] is not None and latest_week["easy_pct"] < 80:
            rules.append(
                f"- INTENSITY WARNING: only {latest_week['easy_pct']}% of last week's HR-zone "
                f"time was easy (zones 1-2); the rest was moderate-to-hard. The established "
                "80/20 endurance principle targets roughly 80% easy - do not add more "
                "hard/threshold work this week even if the minute ceilings allow it, and "
                "note the imbalance in the summary."
            )
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

    # Garmin's own training load (#179) - a real measure of accumulated stress from heart
    # rate/pace/EPOC, alongside the minute-based volume figures above.
    latest_load = load.get("latest_load")
    if latest_load:
        load_bits = []
        if latest_load.get("ctl") is not None and latest_load.get("atl") is not None:
            load_bits.append(
                f"fitness (CTL) {latest_load['ctl']}, fatigue (ATL) {latest_load['atl']}, "
                f"form (TSB) {latest_load['tsb']}"
            )
        if latest_load.get("acwr") is not None:
            status = f" ({latest_load['acwr_status']})" if latest_load.get("acwr_status") else ""
            load_bits.append(f"ACWR {latest_load['acwr']}{status}")
        if any(latest_load.get(k) is not None for k in ("load_aerobic_low", "load_aerobic_high", "load_anaerobic")):
            load_bits.append(
                f"monthly load vs target - aerobic low {latest_load.get('load_aerobic_low')}, "
                f"aerobic high {latest_load.get('load_aerobic_high')}, "
                f"anaerobic {latest_load.get('load_anaerobic')}"
            )
        if load_bits:
            lines.append(f"Garmin training load (as of {latest_load['date']}): " + "; ".join(load_bits) + ".")

    # Weekly easy/hard HR-zone split (#184) - only the most recent week, and only when it
    # deviates from the 80/20-style target, matching _format_progression_rules()' guardrail.
    zone_split = load.get("weekly_zone_split") or []
    if zone_split:
        latest_week = min(zone_split, key=lambda w: w["weeks_ago"])
        if latest_week["easy_pct"] is not None and latest_week["easy_pct"] < 80:
            lines.append(
                f"Last week's HR-zone split was {latest_week['easy_pct']}% easy / "
                f"{latest_week['hard_pct']}% hard - outside the 80/20 target."
            )

    # Garmin performance benchmarks (#186) - threshold pace/HR lets the plan state
    # prescriptions as numbers ("4:35/km") instead of vague terms ("tröskelfart"). Only the
    # benchmarks with direct coaching consequences; the rest is a tool call away.
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT key, value, unit FROM garmin_benchmarks WHERE key IN "
                "('lactate_threshold_pace', 'lactate_threshold_hr', 'endurance_score', 'fitness_age')"
            )
            benchmarks = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
        except Exception as e:
            print(f"[TRAINER LOAD] Error reading Garmin benchmarks: {e}")
            benchmarks = {}
    if benchmarks:
        bits = []
        pace = benchmarks.get('lactate_threshold_pace', (None, None))[0]
        hr = benchmarks.get('lactate_threshold_hr', (None, None))[0]
        threshold_parts = [p for p in (f"{pace} min/km" if pace else None, f"{hr} bpm" if hr else None) if p]
        if threshold_parts:
            bits.append("threshold " + " / ".join(threshold_parts))
        if 'endurance_score' in benchmarks:
            bits.append(f"endurance score {benchmarks['endurance_score'][0]}")
        if 'fitness_age' in benchmarks:
            bits.append(f"fitness age {benchmarks['fitness_age'][0]}")
        if bits:
            lines.append(
                "Garmin performance benchmarks: " + "; ".join(bits) +
                ". State prescriptions using the threshold pace/HR where relevant, "
                "instead of only vague effort terms."
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


async def _clear_bookings(rows) -> int:
    """Deletes trainer_bookings rows whose calendar event is confirmed gone.

    `rows` is an iterable of (booking_id, event_id) tuples. A booking is only removed once
    its calendar event has actually been deleted (or never had one) - if the Google-side
    delete fails, the row is left in place so it keeps representing the still-live event
    instead of becoming an untracked orphan on the calendar (issue #58). Shared between plan
    deletion and plan re-booking, which both need to replace a set of prior bookings.
    """
    from backend.routes.google_calendar import core_delete_calendar_event

    cleared_ids = []
    for booking_id, event_id in rows:
        if event_id:
            try:
                await core_delete_calendar_event(event_id)
            except ValueError:
                # The local google_calendar_events row is already gone (e.g. a prior sync
                # cleaned it up) - there's nothing left to protect, so the booking row is
                # safe to clear too. Treating this the same as a genuine delete failure below
                # left it stuck forever: it would fail with the same "not found" on every
                # future rebook attempt while no longer representing anything real.
                pass
            except Exception as del_err:
                print(f"[TRAINER BOOK] Could not delete the previous event {event_id}: {del_err}")
                continue
        cleared_ids.append(booking_id)

    if cleared_ids:
        placeholders = ",".join("?" * len(cleared_ids))
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM trainer_bookings WHERE id IN ({placeholders})", cleared_ids)
            conn.commit()
    return len(cleared_ids)


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
