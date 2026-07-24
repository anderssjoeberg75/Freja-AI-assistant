"""Garmin Connect API routes using FastAPI."""

import datetime
import json
import os
import asyncio
from fastapi import APIRouter, HTTPException, Query, Request
from backend.config import PROJECT_ROOT
from backend.database import get_db_connection, get_api_key, set_api_key
from backend.services.sync_status import set_sync_state
from backend.services.time_utils import today_local

# --- Sync window sizing (Issue #56) ------------------------------------------
# A run fetches at most this many days, to stay clear of Garmin's rate limits. The days a
# longer gap leaves uncovered are remembered in BACKFILL_KEY instead of being dropped:
# `last_sync_garmin` jumps to now after every successful run, so anything the cap trimmed
# would otherwise never be fetched by any later sync.
MAX_SYNC_DAYS = 30
BACKFILL_CHUNK_DAYS = 30
BACKFILL_KEY = "garmin_backfill_range"   # "YYYY-MM-DD:YYYY-MM-DD", inclusive, or absent
DEFAULT_SYNC_DAYS = 7                    # used when there is no sync history to measure from


def _read_backfill_range():
    """Returns the pending backfill window as (start_date, end_date), or None."""
    raw = get_api_key(BACKFILL_KEY)
    if not raw or ":" not in raw:
        return None
    try:
        start_str, end_str = raw.split(":", 1)
        start = datetime.datetime.strptime(start_str.strip(), "%Y-%m-%d").date()
        end = datetime.datetime.strptime(end_str.strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (start, end) if start <= end else None


def _write_backfill_range(start, end):
    """Persists (or clears, when the window is empty) the pending backfill window."""
    set_api_key(BACKFILL_KEY, f"{start.isoformat()}:{end.isoformat()}" if start <= end else "")


def _queue_backfill(start, end):
    """Records days [start..end] as still needing a sync, merging with anything pending.

    Merging takes the union rather than replacing, so a second long absence cannot discard
    a gap that is still waiting to be drained."""
    if start > end:
        return
    pending = _read_backfill_range()
    if pending:
        start = min(start, pending[0])
        end = max(end, pending[1])
    _write_backfill_range(start, end)
    print(f"[Garmin Sync] Queued backfill for {start} .. {end} ({(end - start).days + 1} days).")

# Garmin's typeKey -> the Swedish label persisted in garmin_health.workout_type and
# garmin_activities.type. These labels are rendered directly in the HUD dashboard, which is
# why they are Swedish. Changing them would require migrating existing rows.
GARMIN_TYPE_MAPPING = {
    'running': 'Löpning',
    'cycling': 'Cykling',
    'fitness_equipment': 'Styrketräning',
    'swimming': 'Simning',
    'walking': 'Promenad',
    'yoga': 'Yoga'
}


def _map_garmin_type(type_key):
    if not type_key:
        return None
    return GARMIN_TYPE_MAPPING.get(type_key, type_key.replace('_', ' ').capitalize())


def _index_daily_response(rows, date_keys=('date', 'calendarDate')):
    """Indexes a Garmin ranged-endpoint response by its per-entry date (Issue #178).

    Tries each key in `date_keys` in order per entry, since different endpoints name their
    date field differently (confirmed from the installed client's typed models:
    `get_body_battery` entries use plain `date`; steps-style daily endpoints use
    `calendarDate`). Returns `{}` on anything that isn't a list of dicts, so a bulk-call
    failure degrades to "every date in the window misses" rather than raising - the caller's
    `.get(date_str)` on a date absent from the dict then yields `None`, exactly like a failed
    per-day call used to, preserving the "reset every field per day" contract."""
    indexed = {}
    if not isinstance(rows, list):
        return indexed
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        d = next((entry.get(key) for key in date_keys if entry.get(key)), None)
        if d:
            indexed[str(d)[:10]] = entry
    return indexed


def _garmin_token_dir():
    """The cached-token directory shared by the sync task and /api/garmin/reauth (#181)."""
    return os.path.join(os.path.dirname(os.path.abspath(PROJECT_ROOT)), '.garminconnect')


def _classify_garmin_error(e: Exception) -> str:
    """Distinguishes an auth failure from a transient one so the UI can say something
    actionable instead of a raw exception string (#181). An expired token, a rate limit and
    a network blip used to all surface as the same generic "error" state - which tells the
    user nothing about whether the fix is "log in again" or "wait a few minutes"."""
    # Imported separately so one missing/unmockable class doesn't sink the other check.
    try:
        from garminconnect import GarminConnectAuthenticationError
        if isinstance(e, GarminConnectAuthenticationError):
            return "auth_required"
    except ImportError:
        pass
    try:
        from garminconnect import GarminConnectTooManyRequestsError
        if isinstance(e, GarminConnectTooManyRequestsError):
            # Rate limiting must not be presented as "your credentials are wrong" - and
            # given the per-day request volume before #178, it was a plausible failure mode.
            return "rate_limited"
    except ImportError:
        pass
    return "error"


def _latest_device_entry(device_map):
    """Picks the freshest entry from a device-keyed dict (Issue #179).

    Garmin's training-status payload nests `acuteTrainingLoadDTO`/`metricsTrainingLoadBalanceDTOMap`
    one level down, keyed by device id rather than date - one entry per device that has
    reported. When more than one device has reported, the one with the most recent
    `calendarDate` wins, so the result is deterministic rather than dependent on dict
    ordering."""
    if not isinstance(device_map, dict) or not device_map:
        return None
    entries = [v for v in device_map.values() if isinstance(v, dict)]
    if not entries:
        return None
    return max(entries, key=lambda e: e.get('calendarDate') or '')


def _upsert_garmin_activities(cursor, activities):
    """Upserts every fetched activity into garmin_activities, keyed on Garmin's activity_id.

    Idempotent by construction (ON CONFLICT DO UPDATE), so the recent-window sync and a
    backfill chunk can overlap without duplicating rows (#177)."""
    for act in activities:
        activity_id = act.get('activityId')
        start_time_local = act.get('startTimeLocal') or ''
        if activity_id is None or not start_time_local:
            continue
        raw_type_key = (act.get('activityType', {}) or {}).get('typeKey')
        act_type = _map_garmin_type(raw_type_key)
        cursor.execute('''
            INSERT INTO garmin_activities (
                activity_id, date, start_time_local, type, name, duration_minutes,
                distance_m, avg_hr, max_hr, calories, training_load, aerobic_te, anaerobic_te,
                raw_type_key, lap_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(activity_id) DO UPDATE SET
                date = excluded.date,
                start_time_local = excluded.start_time_local,
                type = excluded.type,
                name = excluded.name,
                duration_minutes = excluded.duration_minutes,
                distance_m = excluded.distance_m,
                avg_hr = excluded.avg_hr,
                max_hr = excluded.max_hr,
                calories = excluded.calories,
                training_load = excluded.training_load,
                aerobic_te = excluded.aerobic_te,
                anaerobic_te = excluded.anaerobic_te,
                raw_type_key = excluded.raw_type_key,
                lap_count = excluded.lap_count
        ''', (
            str(activity_id), start_time_local[:10], start_time_local, act_type,
            act.get('activityName'),
            round((act.get('duration') or 0) / 60.0, 1),
            act.get('distance'), act.get('averageHR'), act.get('maxHR'), act.get('calories'),
            act.get('activityTrainingLoad'), act.get('aerobicTrainingEffect'),
            act.get('anaerobicTrainingEffect'), raw_type_key, act.get('lapCount'),
        ))


DETAIL_FETCH_CAP = 10   # activities per sync run (Issue #182)
# Old activities are opt-in: the automatic per-sync pass only reaches activities on or after
# this date. A fresh install does not silently vacuum a multi-year history the moment this
# ships - that's what POST /api/garmin/activities/backfill-detail is for.
DETAIL_FETCH_SHIP_DATE = "2026-07-24"

# Garmin activity types that can carry a strength-set breakdown (Issue #183) - no point
# calling get_activity_exercise_sets for a run.
STRENGTH_ACTIVITY_TYPE_KEYS = {'fitness_equipment', 'strength_training', 'indoor_cardio'}

# Activity types where heart-rate zone time is not meaningful (Issue #184) - strength work
# is already handled by #183, and a walk's "zone distribution" says nothing useful.
SKIP_ZONE_ACTIVITY_TYPE_KEYS = STRENGTH_ACTIVITY_TYPE_KEYS | {'walking'}


def _parse_hr_zones(response):
    """Returns `{zone_number: secs_in_zone}` from get_activity_hr_in_timezones (Issue #184).

    No typed model in the installed client for this endpoint; handles both a bare list of
    zone dicts and a dict wrapping one under a nested key, mirroring this codebase's
    existing normalisation pattern for other undocumented Garmin response shapes (e.g.
    get_training_status's list-vs-dict handling)."""
    if isinstance(response, list):
        entries = response
    elif isinstance(response, dict):
        entries = response.get('zonesDTO') or response.get('heartRateZones') or []
    else:
        entries = []
    zones = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        zone_num = entry.get('zoneNumber')
        secs = entry.get('secsInZone')
        if isinstance(zone_num, (int, float)) and isinstance(secs, (int, float)):
            zones[int(zone_num)] = int(secs)
    return zones


def zone_percentages(secs_zone_1, secs_zone_2, secs_zone_3, secs_zone_4, secs_zone_5):
    """Derives easy_pct (zones 1-2) / hard_pct (zones 4-5) from raw zone seconds (#184).

    Computed on read rather than stored, so these cannot drift from the seconds they are
    derived from. Returns `{"easy_pct": None, "hard_pct": None}` for a zero-total session
    rather than dividing by zero."""
    zones = [secs_zone_1, secs_zone_2, secs_zone_3, secs_zone_4, secs_zone_5]
    total = sum(z or 0 for z in zones)
    if not total:
        return {"easy_pct": None, "hard_pct": None}
    easy = (secs_zone_1 or 0) + (secs_zone_2 or 0)
    hard = (secs_zone_4 or 0) + (secs_zone_5 or 0)
    return {
        "easy_pct": round(100.0 * easy / total, 1),
        "hard_pct": round(100.0 * hard / total, 1),
    }


def _import_garmin_laps(cursor, activity_id, activity_date, splits_payload):
    """Stores per-lap detail from get_activity_splits() (Issue #185), replacing any prior
    laps for this activity_id (idempotent re-fetch). Returns the number of laps stored.

    Field-name note: no typed model for this endpoint in the installed client - the exact
    key names (`lapDTOs`, `movingDuration`, `averageRunCadence`, `intensityType`, ...) are
    the issue's best-effort description, not independently verified against a live account.
    A shape mismatch degrades to "no laps stored" via the caller's try/except, not a crash."""
    laps = (splits_payload or {}).get('lapDTOs') or []
    if not laps:
        return 0

    cursor.execute("DELETE FROM garmin_activity_laps WHERE activity_id = ?", (str(activity_id),))
    for i, lap in enumerate(laps):
        if not isinstance(lap, dict):
            continue
        cursor.execute('''
            INSERT INTO garmin_activity_laps (
                activity_id, lap_index, date, distance_m, duration_s, moving_duration_s,
                avg_speed, max_speed, avg_hr, max_hr, avg_cadence, avg_power,
                elevation_gain_m, intensity_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(activity_id), i, activity_date,
            lap.get('distance'), lap.get('duration'), lap.get('movingDuration'),
            lap.get('averageSpeed'), lap.get('maxSpeed'), lap.get('averageHR'), lap.get('maxHR'),
            lap.get('averageRunCadence'), lap.get('averagePower') or lap.get('normalizedPower'),
            lap.get('elevationGain'), lap.get('intensityType'),
        ))
    return len(laps)


def _import_garmin_strength_sets(cursor, activity_id, activity_date, exercise_sets_payload):
    """Groups active sets by exercise and (re-)imports them into trainer_strength_logs for
    this activity_id (Issue #183). Re-import replaces only this activity's own `source =
    'garmin'` rows - manual rows on the same day are never touched. Returns the count of
    exercises imported.

    Field-name note: `get_activity_exercise_sets` has no typed model in the installed
    client, so the exact key names here (`exerciseSets`, `exerciseName`, `weight` in grams,
    `setType`) are the issue's best-effort description of an undocumented endpoint, not
    independently verified against a live account. Wrapped in the caller's per-activity
    try/except, so a shape mismatch degrades to "no exercises imported this run" rather than
    failing the whole detail pass."""
    from backend.services.garmin_exercises import garmin_to_swedish

    sets = (exercise_sets_payload or {}).get('exerciseSets') or []
    by_exercise: dict = {}
    for s in sets:
        if not isinstance(s, dict) or (s.get('setType') or '').upper() == 'REST':
            continue
        raw_name = s.get('exerciseName') or s.get('category') or ''
        entry = by_exercise.setdefault(raw_name, {'reps': [], 'weights_kg': []})
        reps = s.get('repetitionCount')
        if isinstance(reps, (int, float)):
            entry['reps'].append(int(reps))
        weight_g = s.get('weight')
        if isinstance(weight_g, (int, float)) and weight_g > 0:
            entry['weights_kg'].append(weight_g / 1000.0)

    if not by_exercise:
        return 0

    cursor.execute(
        "DELETE FROM trainer_strength_logs WHERE activity_id = ? AND source = 'garmin'",
        (str(activity_id),)
    )
    now_str = datetime.datetime.now().isoformat()
    imported = 0
    for raw_name, data in by_exercise.items():
        if not data['reps'] and not data['weights_kg']:
            continue
        swedish_name = garmin_to_swedish(raw_name)
        set_count = len(data['reps']) or len(data['weights_kg'])
        # Modal rep count is more representative of "what was actually done" than an
        # average across sets that may include a lighter warm-up set; the full per-set
        # breakdown goes into notes instead of being lost to that single number.
        modal_reps = max(set(data['reps']), key=data['reps'].count) if data['reps'] else None
        top_weight = round(max(data['weights_kg']), 1) if data['weights_kg'] else None
        reps_str = "/".join(str(r) for r in data['reps']) if data['reps'] else None
        notes = f"{reps_str} @ {top_weight}kg" if reps_str and top_weight is not None else reps_str
        cursor.execute(
            '''INSERT INTO trainer_strength_logs
               (date, exercise_name, sets, reps, weight, rpe, notes, plan_id, created_at, source, activity_id)
               VALUES (?, ?, ?, ?, ?, NULL, ?, NULL, ?, 'garmin', ?)''',
            (activity_date, swedish_name, set_count, modal_reps, top_weight, notes, now_str, str(activity_id))
        )
        imported += 1
    return imported


def fetch_activity_details(client, cursor, limit=DETAIL_FETCH_CAP, since_date=None):
    """Fetches per-activity detail for activities that have never had it (Issue #182).

    A completed activity is immutable, so detail only needs fetching once, ever, per
    `activity_id` - every subsequent sync costs zero requests for it. Capped per call so a
    first-ever run against years of history spreads over several syncs instead of firing
    hundreds of requests at once; a failed fetch leaves `detail_fetched_at` NULL so the next
    run retries it, and one malformed activity cannot abort the pass for the others.
    `since_date` (`'YYYY-MM-DD'`) narrows the automatic per-sync pass to recent activities;
    the manual backfill endpoint omits it to reach the full history.
    Strength-type activities (Issue #183) also get their exercise sets imported into
    `trainer_strength_logs` in the same pass, sharing the same once-ever marker.
    Returns `{"fetched": N, "failed": N, "remaining": N}` - `remaining` lets the caller log
    a deferred count rather than a capped pass silently reading as complete."""
    query = "SELECT activity_id, date, raw_type_key, lap_count FROM garmin_activities WHERE detail_fetched_at IS NULL"
    params = []
    if since_date:
        query += " AND date >= ?"
        params.append(since_date)
    query += " ORDER BY date ASC"
    cursor.execute(query, params)
    pending = cursor.fetchall()

    fetched, failed = 0, 0
    now_str = datetime.datetime.now().isoformat()
    for activity_id, activity_date, raw_type_key, lap_count in pending[:limit]:
        try:
            detail = client.get_activity(activity_id) or {}
            summary = detail.get('summaryDTO') or {}
            cursor.execute('''
                INSERT INTO garmin_activity_detail (
                    activity_id, recovery_time_hours, training_effect_label,
                    training_effect_message, avg_ground_contact_time, avg_vertical_oscillation,
                    avg_vertical_ratio, avg_stride_length, norm_power, avg_power, max_power,
                    min_temperature, max_temperature, vo2max_value
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(activity_id) DO UPDATE SET
                    recovery_time_hours = excluded.recovery_time_hours,
                    training_effect_label = excluded.training_effect_label,
                    training_effect_message = excluded.training_effect_message,
                    avg_ground_contact_time = excluded.avg_ground_contact_time,
                    avg_vertical_oscillation = excluded.avg_vertical_oscillation,
                    avg_vertical_ratio = excluded.avg_vertical_ratio,
                    avg_stride_length = excluded.avg_stride_length,
                    norm_power = excluded.norm_power,
                    avg_power = excluded.avg_power,
                    max_power = excluded.max_power,
                    min_temperature = excluded.min_temperature,
                    max_temperature = excluded.max_temperature,
                    vo2max_value = excluded.vo2max_value
            ''', (
                str(activity_id),
                summary.get('recoveryTimeInHours'),
                summary.get('trainingEffectLabel'),
                summary.get('trainingEffectMessage'),
                summary.get('avgGroundContactTime'),
                summary.get('avgVerticalOscillation'),
                summary.get('avgVerticalRatio'),
                summary.get('avgStrideLength'),
                summary.get('normPower'),
                summary.get('avgPower'),
                summary.get('maxPower'),
                summary.get('minTemperature'),
                summary.get('maxTemperature'),
                summary.get('vO2MaxValue'),
            ))
            cursor.execute(
                "UPDATE garmin_activities SET detail_fetched_at = ? WHERE activity_id = ?",
                (now_str, str(activity_id))
            )

            # Own try/except: the activity summary above already succeeded and its marker is
            # stamped, so a failure here (an endpoint this codebase cannot verify live - see
            # _import_garmin_strength_sets' docstring) must not un-stamp it or count this
            # activity as failed; it just means these sets are not retried automatically -
            # POST /api/garmin/activities/backfill-detail still can be, once the shape is
            # confirmed and the parser fixed if needed.
            if (raw_type_key or '') in STRENGTH_ACTIVITY_TYPE_KEYS:
                try:
                    exercise_sets = client.get_activity_exercise_sets(activity_id)
                    _import_garmin_strength_sets(cursor, activity_id, activity_date, exercise_sets)
                except Exception as strength_err:
                    print(f"[Garmin Sync] Error importing strength sets for {activity_id}: {strength_err}")

            # Time in HR zones (#184), own try/except for the same reason as above - a
            # failure here must not un-stamp the marker or abort the pass.
            if (raw_type_key or '') not in SKIP_ZONE_ACTIVITY_TYPE_KEYS:
                try:
                    zones = _parse_hr_zones(client.get_activity_hr_in_timezones(activity_id))
                    if zones:
                        cursor.execute('''
                            INSERT INTO garmin_activity_zones (
                                activity_id, secs_zone_1, secs_zone_2, secs_zone_3,
                                secs_zone_4, secs_zone_5
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(activity_id) DO UPDATE SET
                                secs_zone_1 = excluded.secs_zone_1,
                                secs_zone_2 = excluded.secs_zone_2,
                                secs_zone_3 = excluded.secs_zone_3,
                                secs_zone_4 = excluded.secs_zone_4,
                                secs_zone_5 = excluded.secs_zone_5
                        ''', (
                            str(activity_id), zones.get(1), zones.get(2), zones.get(3),
                            zones.get(4), zones.get(5),
                        ))
                    # No row when the activity genuinely has no HR data - absence, not a
                    # zero-filled row, is the truthful outcome (issue's own requirement).
                except Exception as zones_err:
                    print(f"[Garmin Sync] Error fetching HR zones for {activity_id}: {zones_err}")

            # Lap splits (#185), own try/except for the same reason as above. Skipped for
            # lapCount <= 1 - an unstructured steady run produces one lap and tells us
            # nothing new, so this avoids a pointless request per easy jog.
            if lap_count is not None and lap_count > 1:
                try:
                    splits = client.get_activity_splits(activity_id)
                    _import_garmin_laps(cursor, activity_id, activity_date, splits)
                except Exception as laps_err:
                    print(f"[Garmin Sync] Error fetching laps for {activity_id}: {laps_err}")

            fetched += 1
        except Exception as detail_err:
            print(f"[Garmin Sync] Error fetching activity detail for {activity_id}: {detail_err}")
            failed += 1

    remaining = max(0, len(pending) - limit)
    if remaining:
        print(f"[Garmin Sync] Activity detail pass deferred {remaining} activities to the next run.")
    return {"fetched": fetched, "failed": failed, "remaining": remaining}


router = APIRouter()

@router.get("/api/garmin/data")
async def get_garmin_data(days: int = Query(7, description="Number of days to retrieve")):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, stress_avg, stress_max, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, vo2max, intensity_minutes, sleep_score, training_load_acute, training_load_chronic, acwr, acwr_status, load_aerobic_low, load_aerobic_high, load_anaerobic, training_readiness, training_readiness_level, training_readiness_feedback
                FROM garmin_health
                ORDER BY date DESC
                LIMIT ?
            ''', (days,))
            rows = cursor.fetchall()

        results = []
        for row in rows:
            training_load_acute, training_load_chronic = row[20], row[21]
            results.append({
                'date': row[0],
                'steps': row[1],
                'sleep_hours': row[2],
                'resting_hr': row[3],
                'active_calories': row[4],
                'workout_type': row[5] or 'Ingen',
                'workout_duration': row[6],
                'body_battery': row[7],
                'hrv': row[8],
                'recovery_time': row[9],
                'training_status': row[10],
                'stress_avg': row[11],
                'stress_max': row[12],
                'sleep_deep_hours': row[13],
                'sleep_light_hours': row[14],
                'sleep_rem_hours': row[15],
                'sleep_awake_hours': row[16],
                'vo2max': row[17],
                'intensity_minutes': row[18],
                'sleep_score': row[19],
                'training_load_acute': training_load_acute,
                'training_load_chronic': training_load_chronic,
                # TSB ("form"): derived, never stored, so it cannot drift from its inputs (#179).
                'tsb': round(training_load_chronic - training_load_acute, 1)
                       if training_load_chronic is not None and training_load_acute is not None else None,
                'acwr': row[22],
                'acwr_status': row[23],
                'load_aerobic_low': row[24],
                'load_aerobic_high': row[25],
                'load_anaerobic': row[26],
                'training_readiness': row[27],
                'training_readiness_level': row[28],
                'training_readiness_feedback': row[29],
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/garmin/activities")
async def get_garmin_activities(days: int = Query(30, description="Number of days to retrieve")):
    """Per-activity Garmin detail, mirroring /api/strava/data - one row per session rather
    than the same-day rollup /api/garmin/data serves (#177)."""
    try:
        cutoff = (today_local() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT a.activity_id, a.date, a.start_time_local, a.type, a.name,
                       a.duration_minutes, a.distance_m, a.avg_hr, a.max_hr, a.calories,
                       a.training_load, a.aerobic_te, a.anaerobic_te,
                       d.recovery_time_hours, d.training_effect_label, d.training_effect_message,
                       d.avg_ground_contact_time, d.avg_vertical_oscillation, d.avg_vertical_ratio,
                       d.avg_stride_length, d.norm_power, d.avg_power, d.max_power,
                       d.min_temperature, d.max_temperature, d.vo2max_value
                FROM garmin_activities a
                LEFT JOIN garmin_activity_detail d ON d.activity_id = a.activity_id
                WHERE a.date >= ?
                ORDER BY a.date DESC, a.start_time_local DESC
            ''', (cutoff,))
            rows = cursor.fetchall()

        return [
            {
                'activity_id': row[0],
                'date': row[1],
                'start_time_local': row[2],
                'type': row[3],
                'name': row[4],
                'duration_minutes': row[5],
                'distance_m': row[6],
                'avg_hr': row[7],
                'max_hr': row[8],
                'calories': row[9],
                'training_load': row[10],
                'aerobic_te': row[11],
                'anaerobic_te': row[12],
                # Per-activity detail (#182) - None on every field until the fetch-once pass
                # reaches this activity, rather than a partially-populated placeholder row.
                'recovery_time_hours': row[13],
                'training_effect_label': row[14],
                'training_effect_message': row[15],
                'avg_ground_contact_time': row[16],
                'avg_vertical_oscillation': row[17],
                'avg_vertical_ratio': row[18],
                'avg_stride_length': row[19],
                'norm_power': row[20],
                'avg_power': row[21],
                'max_power': row[22],
                'min_temperature': row[23],
                'max_temperature': row[24],
                'vo2max_value': row[25],
            }
            for row in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/garmin/activities/backfill-detail")
async def post_garmin_activities_backfill_detail(
    limit: int = Query(DETAIL_FETCH_CAP, description="Max activities to fetch detail for in this call")
):
    """Deliberate historical fill for per-activity detail (Issue #182).

    The automatic per-sync pass only reaches activities on/after DETAIL_FETCH_SHIP_DATE -
    backfilling a user's entire history is a bulk operation against an unofficial API, so it
    is opt-in. Repeated calls drain the backlog through the same capped mechanism."""
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    if not email or not password:
        raise HTTPException(
            status_code=400,
            detail="Garmin Connect credentials are missing. Enter the email and password in Settings."
        )

    limit = max(1, min(int(limit), 100))
    try:
        from garminconnect import Garmin
        token_dir = _garmin_token_dir()
        os.makedirs(token_dir, exist_ok=True)
        client = Garmin(email, password)
        client.login(tokenstore=token_dir)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            result = fetch_activity_details(client, cursor, limit=limit, since_date=None)
            conn.commit()
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/garmin/zones")
async def get_garmin_zones(days: int = Query(28, description="Number of days to retrieve")):
    """Per-session time-in-HR-zones, for the HUD's weekly intensity-distribution chart (#184)."""
    try:
        days = max(1, min(int(days), 180))
        cutoff = (today_local() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT a.activity_id, a.date, a.type,
                       z.secs_zone_1, z.secs_zone_2, z.secs_zone_3, z.secs_zone_4, z.secs_zone_5
                FROM garmin_activity_zones z
                JOIN garmin_activities a ON a.activity_id = z.activity_id
                WHERE a.date >= ?
                ORDER BY a.date DESC
            ''', (cutoff,))
            rows = cursor.fetchall()

        results = []
        for activity_id, date_str, act_type, z1, z2, z3, z4, z5 in rows:
            pct = zone_percentages(z1, z2, z3, z4, z5)
            results.append({
                'activity_id': activity_id,
                'date': date_str,
                'type': act_type,
                'secs_zone_1': z1, 'secs_zone_2': z2, 'secs_zone_3': z3,
                'secs_zone_4': z4, 'secs_zone_5': z5,
                'easy_pct': pct['easy_pct'], 'hard_pct': pct['hard_pct'],
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/garmin/activities/{activity_id}/laps")
async def get_garmin_activity_laps(activity_id: str):
    """Per-lap detail for one activity (Issue #185), for the PT panel's lap table."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT lap_index, distance_m, duration_s, moving_duration_s, avg_speed,
                       max_speed, avg_hr, max_hr, avg_cadence, avg_power, elevation_gain_m,
                       intensity_type
                FROM garmin_activity_laps
                WHERE activity_id = ?
                ORDER BY lap_index ASC
            ''', (activity_id,))
            rows = cursor.fetchall()

        return [
            {
                'lap_index': row[0],
                'distance_m': row[1],
                'duration_s': row[2],
                'moving_duration_s': row[3],
                'avg_speed': row[4],
                'max_speed': row[5],
                'avg_hr': row[6],
                'max_hr': row[7],
                'avg_cadence': row[8],
                'avg_power': row[9],
                'elevation_gain_m': row[10],
                'intensity_type': row[11],
            }
            for row in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Slow-moving account facts (Issue #186) - none of these change fast enough to justify a
# daily request, so the refresh below self-limits to this cadence, mirroring
# recompute_health_baselines()'s pattern.
BENCHMARK_REFRESH_DAYS = 7
BENCHMARKS_UPDATED_AT_KEY = "garmin_benchmarks_updated_at"


def _store_benchmark(cursor, key, value, unit=None, as_of_date=None):
    if value is None:
        return
    cursor.execute('''
        INSERT INTO garmin_benchmarks (key, value, unit, as_of_date, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            unit = excluded.unit,
            as_of_date = excluded.as_of_date,
            updated_at = excluded.updated_at
    ''', (key, str(value), unit, as_of_date, datetime.datetime.now().isoformat()))


def refresh_garmin_benchmarks(client, force=False):
    """Refreshes slow-moving Garmin account-level benchmarks (Issue #186): threshold pace/
    HR, race predictions, PRs, running tolerance, and the endurance/hill/fitness-age trend
    scores. Self-limited to `BENCHMARK_REFRESH_DAYS`, like `recompute_health_baselines()`.

    Each benchmark has its own try/except - several are device- or sport-dependent
    (`get_cycling_ftp` needs a power meter, the trend scores need enough recent activity),
    so a missing one is a normal outcome, never surfaced as a failed sync.

    Field-name caveat: none of these endpoints have a typed model in the installed client.
    `lactate_threshold`'s `speed`/`heartRate` keys are confirmed from the library's own
    source (it merges two near-identical dicts using exactly those names). The others'
    exact field names are not independently verified against a live account, so
    race-predictions/personal-records/running-tolerance are stored as raw JSON rather than
    decomposed into individual fields that might be guessed wrong; endurance/hill score and
    fitness age try the most likely key names with a documented fallback.
    Returns `{"status": "success"|"skipped", "fetched": [...]}`."""
    if not force:
        last = get_api_key(BENCHMARKS_UPDATED_AT_KEY)
        if last:
            try:
                last_dt = datetime.datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S")
                if (datetime.datetime.now() - last_dt).days < BENCHMARK_REFRESH_DAYS:
                    return {"status": "skipped", "reason": "refreshed_recently", "fetched": []}
            except (ValueError, TypeError):
                pass  # Unparseable timestamp - treat as stale and refresh.

    today_str = today_local().strftime('%Y-%m-%d')
    fetched = []

    with get_db_connection() as conn:
        cursor = conn.cursor()

        try:
            lt = client.get_lactate_threshold(latest=True) or {}
            speed_hr = lt.get('speed_and_heart_rate') or {}
            speed = speed_hr.get('speed')
            hr = speed_hr.get('heartRate')
            as_of = speed_hr.get('calendarDate') or today_str
            if isinstance(speed, (int, float)) and speed > 0:
                pace_sec = 1000.0 / speed
                pace_str = f"{int(pace_sec // 60)}:{int(round(pace_sec % 60)):02d}"
                _store_benchmark(cursor, 'lactate_threshold_pace', pace_str, 'min/km', as_of)
                fetched.append('lactate_threshold_pace')
            if isinstance(hr, (int, float)):
                _store_benchmark(cursor, 'lactate_threshold_hr', int(hr), 'bpm', as_of)
                fetched.append('lactate_threshold_hr')
        except Exception as e:
            print(f"[Garmin Benchmarks] Lactate threshold unavailable: {e}")

        try:
            rp = client.get_race_predictions()
            if rp:
                _store_benchmark(cursor, 'race_predictions_json', json.dumps(rp), None, today_str)
                fetched.append('race_predictions_json')
        except Exception as e:
            print(f"[Garmin Benchmarks] Race predictions unavailable: {e}")

        try:
            pr = client.get_personal_record()
            if pr:
                _store_benchmark(cursor, 'personal_records_json', json.dumps(pr), None, today_str)
                fetched.append('personal_records_json')
        except Exception as e:
            print(f"[Garmin Benchmarks] Personal records unavailable: {e}")

        try:
            window_start = (today_local() - datetime.timedelta(days=90)).strftime('%Y-%m-%d')
            rt = client.get_running_tolerance(window_start, today_str, aggregation='weekly')
            latest_entry = rt[-1] if isinstance(rt, list) and rt else (rt if rt else None)
            if latest_entry:
                _store_benchmark(cursor, 'running_tolerance_latest_json', json.dumps(latest_entry), None, today_str)
                fetched.append('running_tolerance_latest_json')
        except Exception as e:
            print(f"[Garmin Benchmarks] Running tolerance unavailable: {e}")

        try:
            es = client.get_endurance_score(today_str) or {}
            score = es.get('overallScore') or es.get('score')
            if isinstance(score, (int, float)):
                _store_benchmark(cursor, 'endurance_score', int(score), None, today_str)
                fetched.append('endurance_score')
        except Exception as e:
            print(f"[Garmin Benchmarks] Endurance score unavailable: {e}")

        try:
            hs = client.get_hill_score(today_str) or {}
            score = hs.get('overallScore') or hs.get('score')
            if isinstance(score, (int, float)):
                _store_benchmark(cursor, 'hill_score', int(score), None, today_str)
                fetched.append('hill_score')
        except Exception as e:
            print(f"[Garmin Benchmarks] Hill score unavailable: {e}")

        try:
            fa = client.get_fitnessage_data(today_str) or {}
            fitness_age = fa.get('fitnessAge') or fa.get('fitness_age')
            if isinstance(fitness_age, (int, float)):
                _store_benchmark(cursor, 'fitness_age', round(float(fitness_age), 1), 'years', today_str)
                fetched.append('fitness_age')
        except Exception as e:
            print(f"[Garmin Benchmarks] Fitness age unavailable: {e}")

        conn.commit()

    set_api_key(BENCHMARKS_UPDATED_AT_KEY, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return {"status": "success", "fetched": fetched}


@router.get("/api/garmin/benchmarks")
async def get_garmin_benchmarks():
    """All stored Garmin account-level benchmarks, for the PT panel's benchmarks card."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value, unit, as_of_date, updated_at FROM garmin_benchmarks ORDER BY key")
            rows = cursor.fetchall()
        return {
            row[0]: {"value": row[1], "unit": row[2], "as_of_date": row[3], "updated_at": row[4]}
            for row in rows
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def push_single_workout_to_garmin(cursor, client, plan_id, workout, date_str, duration_minutes):
    """Uploads and schedules one plan session to Garmin (Issue #176), tracking it in
    `garmin_pushed_workouts` keyed on `(plan_id, date_str)` so a re-push updates the
    existing Garmin workout in place instead of duplicating it on the watch. A workout
    already pushed under a *different* Garmin workout_id (the plan changed since the last
    push) has its stale Garmin-side workout deleted after the new one is scheduled.
    Raises on failure - callers decide how to report/log it."""
    from backend.services.garmin_workout import build_garmin_workout

    cursor.execute(
        "SELECT garmin_workout_id FROM garmin_pushed_workouts WHERE plan_id = ? AND workout_date = ?",
        (plan_id, date_str)
    )
    existing = cursor.fetchone()

    workout_json = build_garmin_workout(workout, duration_minutes)
    uploaded = client.upload_workout(workout_json)
    workout_id = uploaded.get("workoutId") if isinstance(uploaded, dict) else None
    if not workout_id:
        raise RuntimeError(f"Garmin did not return a workoutId: {uploaded}")

    scheduled = client.schedule_workout(workout_id, date_str)
    schedule_id = scheduled.get("workoutScheduleId") if isinstance(scheduled, dict) else None

    cursor.execute('''
        INSERT INTO garmin_pushed_workouts (plan_id, workout_date, garmin_workout_id, garmin_schedule_id, pushed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(plan_id, workout_date) DO UPDATE SET
            garmin_workout_id = excluded.garmin_workout_id,
            garmin_schedule_id = excluded.garmin_schedule_id,
            pushed_at = excluded.pushed_at
    ''', (plan_id, date_str, str(workout_id), str(schedule_id) if schedule_id else None,
          datetime.datetime.now().isoformat()))

    if existing and existing[0] and str(existing[0]) != str(workout_id):
        try:
            client.delete_workout(existing[0])
        except Exception as cleanup_err:
            print(f"[Garmin Workout Push] Could not clean up stale workout {existing[0]}: {cleanup_err}")

    return {"workout_id": workout_id, "schedule_id": schedule_id}


@router.post("/api/garmin/workouts/push")
async def post_garmin_workouts_push(request: Request):
    """Pushes a plan's sessions to the Garmin watch (Issue #176, step 1): builds a simple
    time-based workout per session via `plan_occurrences()`, uploads and schedules each on
    its date. This writes real workouts to the user's Garmin account."""
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    if not email or not password:
        raise HTTPException(
            status_code=400,
            detail="Garmin Connect credentials are missing. Enter the email and password in Settings."
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    plan_id = body.get("plan_id")
    if not plan_id:
        raise HTTPException(status_code=400, detail="plan_id is required.")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT advice_text FROM trainer_plans WHERE id = ?", (plan_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"No plan with id {plan_id} was found.")
    try:
        plan_data = json.loads(str(row[0] or "").replace("```json", "").replace("```", "").strip())
    except Exception:
        raise HTTPException(status_code=400, detail="The plan's advice_text could not be parsed.")

    start_date_str = body.get("start_date")
    if start_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date must be YYYY-MM-DD.")
    else:
        today = today_local()
        start_date = today - datetime.timedelta(days=today.weekday())  # this week's Monday

    from backend.services.plan_export import plan_occurrences
    occurrences = plan_occurrences(plan_data, start_date)
    if not occurrences:
        return {"status": "success", "pushed": [], "pushed_count": 0, "failed_count": 0,
                "message": "No sessions to push (rest days only, or an empty plan)."}

    try:
        from garminconnect import Garmin
        token_dir = _garmin_token_dir()
        os.makedirs(token_dir, exist_ok=True)
        client = Garmin(email, password)
        client.login(tokenstore=token_dir)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Garmin login failed: {e}")

    results = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for occ in occurrences:
            date_str = occ["date"].strftime("%Y-%m-%d")
            try:
                pushed = push_single_workout_to_garmin(
                    cursor, client, plan_id, occ["workout"], date_str, occ["duration"]
                )
                results.append({"date": date_str, "status": "pushed", **pushed})
            except Exception as e:
                print(f"[Garmin Workout Push] Failed for {date_str}: {e}")
                results.append({"date": date_str, "status": "failed", "reason": str(e)})
        conn.commit()

    return {
        "status": "success",
        "pushed": results,
        "pushed_count": sum(1 for r in results if r["status"] == "pushed"),
        "failed_count": sum(1 for r in results if r["status"] == "failed"),
    }


@router.delete("/api/garmin/workouts/push")
async def delete_garmin_pushed_workouts(plan_id: int = Query(..., description="Plan id whose pushed workouts should be removed from the Garmin account")):
    """Unschedules and deletes every Garmin workout pushed for this plan (Issue #176).

    Writes to the real Garmin account - the client must confirm with the user before
    calling this."""
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="Garmin Connect credentials are missing.")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, garmin_workout_id, garmin_schedule_id FROM garmin_pushed_workouts WHERE plan_id = ?",
            (plan_id,)
        )
        rows = cursor.fetchall()
    if not rows:
        return {"status": "success", "removed": 0, "message": "Nothing was pushed for this plan."}

    try:
        from garminconnect import Garmin
        token_dir = _garmin_token_dir()
        client = Garmin(email, password)
        client.login(tokenstore=token_dir)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Garmin login failed: {e}")

    removed = 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for row_id, workout_id, schedule_id in rows:
            try:
                if schedule_id:
                    client.unschedule_workout(schedule_id)
                if workout_id:
                    client.delete_workout(workout_id)
                cursor.execute("DELETE FROM garmin_pushed_workouts WHERE id = ?", (row_id,))
                removed += 1
            except Exception as e:
                print(f"[Garmin Workout Push] Failed to remove pushed workout {workout_id}: {e}")
        conn.commit()

    return {"status": "success", "removed": removed}


def run_garmin_sync_task_blocking(email, password, days, end_date=None):
    """Syncs `days` days of Garmin data ending on `end_date` (default: today).

    `end_date` exists so a window can be anchored somewhere other than today, which is what
    lets the backfill drain an older gap without disturbing the recent window (Issue #56).
    """
    try:
        from garminconnect import Garmin
        token_dir = _garmin_token_dir()
        os.makedirs(token_dir, exist_ok=True)

        client = Garmin(email, password)
        client.login(tokenstore=token_dir)

        window_end = end_date or today_local()
        dates_to_sync = [(window_end - datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
        dates_to_sync.reverse()

        activities = []
        try:
            # get_activities(0, 30) returns the 30 most-recent activities counting back from
            # *now*, unfiltered by date - not a date-range query. During a backfill chunk
            # (anchored weeks/months in the past) or on a high-activity account, those 30
            # activities can miss the window being synced entirely, silently dropping
            # workout_type/workout_duration for backfilled or older days.
            # get_activities_by_date is the actual date-range-correct call.
            activities = client.get_activities_by_date(dates_to_sync[0], dates_to_sync[-1])
        except Exception as act_err:
            print(f"Error fetching activities: {act_err}")

        # Group once by day so a day with multiple sessions (e.g. an easy run in the morning
        # and strength in the evening) keeps all of them, instead of the old per-day scan that
        # took the first match and silently dropped the rest (#177).
        activities_by_date = {}
        for act in activities:
            start_time_local = act.get('startTimeLocal') or ''
            if start_time_local:
                activities_by_date.setdefault(start_time_local[:10], []).append(act)

        # Body battery and steps genuinely have date-ranged endpoints returning one entry
        # per day, so both are fetched once for the whole window instead of once per day -
        # removing (days_to_sync - 1) requests each (Issue #178). Verified against the
        # installed client: get_weekly_stress/get_weekly_intensity_minutes were also
        # considered, but their name is accurate - they return one aggregate per *week*
        # (`calendarDate` = week start), not per day, so they cannot replace the daily
        # stress_avg/stress_max/intensity_minutes columns without losing granularity; those
        # stay on the per-day get_stats() call below. Each bulk call gets its own try/except
        # degrading to {}, mirroring the per-day try/except pattern so one bulk failure only
        # blanks that metric for the window rather than aborting the sync.
        bb_by_date = {}
        try:
            bb_by_date = _index_daily_response(
                client.get_body_battery(dates_to_sync[0], dates_to_sync[-1]), date_keys=('date',)
            )
        except Exception as bb_range_err:
            print(f"Error fetching body battery range: {bb_range_err}")

        steps_by_date = {}
        try:
            steps_by_date = _index_daily_response(
                client.get_daily_steps(dates_to_sync[0], dates_to_sync[-1]),
                date_keys=('calendarDate', 'date'),
            )
        except Exception as steps_range_err:
            print(f"Error fetching daily steps range: {steps_range_err}")

        # Tracks whether ANY per-day metric call succeeded for ANY day in the window. If the
        # whole run fails silently (expired session, Garmin rate-limiting/lockout mid-run),
        # every field for every day stays None and the per-day try/excepts swallow it, so
        # nothing here would otherwise stop the run from committing all-NULL rows and
        # reporting "success" - which also advances last_sync_garmin, so the gap is never
        # retried by a later sync.
        any_day_succeeded = False

        with get_db_connection() as conn:
            cursor = conn.cursor()

            try:
                _upsert_garmin_activities(cursor, activities)
            except Exception as upsert_err:
                # Must not fail the health-metric sync that already succeeded; a failed
                # upsert here just leaves garmin_activities stale until the next run.
                print(f"[Garmin Sync] Error upserting activities: {upsert_err}")

            for date_str in dates_to_sync:
                # Reset EVERY per-day field here, so a failed fetch leaves NULL instead of
                # carrying over the previous day's value. Each metric below is assigned only
                # inside its own try/if, so anything missing from this block silently
                # duplicates yesterday's reading into today's row - which is worse than a
                # gap, because a plausible number is invisible once it reaches the trend
                # charts, the health baselines and the coach's recovery assessment.
                # Keep this list exhaustive when adding a column.
                steps = None
                active_calories = None
                sleep_hours = None
                resting_hr = None
                body_battery = None
                hrv = None
                recovery_time = None
                training_status = None
                stress_avg = None
                stress_max = None
                sleep_deep_hours = None
                sleep_light_hours = None
                sleep_rem_hours = None
                sleep_awake_hours = None
                vo2max = None
                intensity_minutes = None
                sleep_score = None
                training_load_acute = None
                training_load_chronic = None
                acwr = None
                acwr_status = None
                load_aerobic_low = None
                load_aerobic_high = None
                load_anaerobic = None
                training_readiness = None
                training_readiness_level = None
                training_readiness_feedback = None
                # workout_type/workout_duration are the one exception: they are derived from
                # the `activities` list fetched once above, not from a per-day call, so "no
                # workout that day" is a real answer and 0 minutes is the truthful value.
                workout_type = None
                workout_duration = 0
                try:
                    stats = client.get_stats(date_str)
                    if stats:
                        active_calories = int(stats.get('activeCalories', 0) or 0)
                        # All-day stress. Garmin returns -1 (no data) / -2 (too little data)
                        # when there is no valid reading, so only keep non-negative values.
                        # No verified date-ranged endpoint returns this per day (#178) -
                        # get_weekly_stress is a weekly aggregate, not a daily one - so this
                        # stays a per-day call.
                        avg_s = stats.get('averageStressLevel')
                        max_s = stats.get('maxStressLevel')
                        stress_avg = int(avg_s) if isinstance(avg_s, (int, float)) and avg_s >= 0 else None
                        stress_max = int(max_s) if isinstance(max_s, (int, float)) and max_s >= 0 else None
                        # Intensity minutes toward the weekly goal: vigorous counts double,
                        # the same weighting Garmin displays. Same reasoning as stress above -
                        # get_weekly_intensity_minutes is weekly, not daily.
                        mod = stats.get('moderateIntensityMinutes') or 0
                        vig = stats.get('vigorousIntensityMinutes') or 0
                        intensity_minutes = int(mod) + 2 * int(vig)
                except Exception as stats_err:
                    print(f"Error fetching stats for {date_str}: {stats_err}")
                # Steps come from the ranged get_daily_steps call fetched once above (#178),
                # not from get_stats - a miss here yields None via .get(), same as a failed
                # per-day call would have.
                day_steps = steps_by_date.get(date_str)
                if day_steps:
                    steps = int(day_steps.get('totalSteps') or day_steps.get('steps') or 0)
                try:
                    sleep_data = client.get_sleep_data(date_str)
                    if sleep_data:
                        dto = sleep_data.get('dailySleepDTO', {}) or {}
                        sleep_time_sec = dto.get('sleepTimeSeconds', 0) or 0
                        sleep_hours = round(sleep_time_sec / 3600.0, 1)
                        # Sleep stages (seconds -> hours) from the same nightly record.
                        sleep_deep_hours = round((dto.get('deepSleepSeconds') or 0) / 3600.0, 2)
                        sleep_light_hours = round((dto.get('lightSleepSeconds') or 0) / 3600.0, 2)
                        sleep_rem_hours = round((dto.get('remSleepSeconds') or 0) / 3600.0, 2)
                        sleep_awake_hours = round((dto.get('awakeSleepSeconds') or 0) / 3600.0, 2)
                        # Overall sleep score (0-100), when Garmin provides one.
                        score_val = (dto.get('sleepScores', {}) or {}).get('overall', {}).get('value')
                        sleep_score = int(score_val) if isinstance(score_val, (int, float)) else None
                except Exception as sleep_err:
                    print(f"Error fetching sleep for {date_str}: {sleep_err}")
                try:
                    # VO2max estimate (running). get_max_metrics returns a list; the value
                    # lives under the 'generic' block.
                    mm = client.get_max_metrics(date_str)
                    if mm:
                        entry = mm[0] if isinstance(mm, list) and mm else mm
                        generic = (entry or {}).get('generic', {}) or {}
                        vo2_val = generic.get('vo2MaxPreciseValue') or generic.get('vo2MaxValue')
                        vo2max = round(float(vo2_val), 1) if isinstance(vo2_val, (int, float)) else None
                except Exception as vo2_err:
                    print(f"Error fetching VO2max for {date_str}: {vo2_err}")
                try:
                    heart_rates = client.get_heart_rates(date_str)
                    if heart_rates:
                        resting_hr = int(heart_rates.get('restingHeartRate', 0) or 0)
                except Exception as hr_err:
                    print(f"Error fetching heart rates for {date_str}: {hr_err}")
                    
                # garmin_health keeps one row per date, so multiple sessions on one day are
                # rolled up rather than stored individually here - the full per-activity detail
                # lives in garmin_activities (upserted above). Dominant = longest session (what
                # a user would call "today's workout"); duration = the day's total, so a second
                # session is no longer silently dropped (#177).
                day_activities = activities_by_date.get(date_str, [])
                if day_activities:
                    dominant = max(day_activities, key=lambda a: a.get('duration', 0) or 0)
                    act_type = (dominant.get('activityType', {}) or {}).get('typeKey')
                    workout_type = _map_garmin_type(act_type)
                    workout_duration = int(round(
                        sum(a.get('duration', 0) or 0 for a in day_activities) / 60.0
                    ))

                try:
                    # Fetched once for the whole window above (#178), not per day.
                    day_bb = bb_by_date.get(date_str)
                    if day_bb:
                        # Extract the maximum value from bodyBatteryValuesArray (list of [timestamp, value] pairs)
                        bb_values = [v[1] for v in (day_bb.get('bodyBatteryValuesArray') or []) if isinstance(v, list) and len(v) > 1 and v[1] is not None]
                        body_battery = max(bb_values) if bb_values else day_bb.get('highest')
                except Exception as bb_err:
                    print(f"Error extracting body battery for {date_str}: {bb_err}")
                try:
                    hrv_data = client.get_hrv_data(date_str)
                    if hrv_data and isinstance(hrv_data, dict):
                        hrv_summary = hrv_data.get('hrvSummary', {})
                        if hrv_summary:
                            hrv = hrv_summary.get('lastNightAvg')
                except Exception as hrv_err:
                    print(f"Error fetching HRV for {date_str}: {hrv_err}")
                    
                try:
                    ts_data = client.get_training_status(date_str)
                    if ts_data:
                        if isinstance(ts_data, list) and len(ts_data) > 0:
                            ts_data = ts_data[0]
                        if isinstance(ts_data, dict):
                            raw_status = ts_data.get('trainingStatus')
                            if raw_status:
                                # Also persisted in Swedish and shown as-is in the HUD. The trainer
                                # prompts reference these exact strings when judging recovery.
                                status_mapping = {
                                    'PRODUCTIVE': 'Produktiv',
                                    'MAINTAINING': 'Underhållande',
                                    'UNPRODUCTIVE': 'Oproduktiv',
                                    'PEAKING': 'Toppform',
                                    'OVERREACHING': 'Övertränad',
                                    'RECOVERY': 'Återhämtning',
                                    'DETRAINING': 'Avtagande form',
                                    'STRAINED': 'Ansträngd'
                                }
                                training_status = status_mapping.get(raw_status.upper(), raw_status.capitalize())
                            recovery_time = ts_data.get('recoveryTimeInHours')

                            # Garmin's own training load (#179) - already present in the same
                            # get_training_status response we already call; no extra request.
                            most_recent_status = ts_data.get('mostRecentTrainingStatus') or {}
                            device_status_map = most_recent_status.get('latestTrainingStatusData') or {}
                            status_entry = _latest_device_entry(device_status_map)
                            if status_entry:
                                acute_dto = status_entry.get('acuteTrainingLoadDTO') or {}
                                training_load_acute = acute_dto.get('dailyTrainingLoadAcute')
                                training_load_chronic = acute_dto.get('dailyTrainingLoadChronic')
                                acwr = acute_dto.get('dailyAcuteChronicWorkloadRatio')
                                acwr_status = acute_dto.get('acwrStatus')

                            most_recent_balance = ts_data.get('mostRecentTrainingLoadBalance') or {}
                            device_balance_map = most_recent_balance.get('metricsTrainingLoadBalanceDTOMap') or {}
                            balance_entry = _latest_device_entry(device_balance_map)
                            if balance_entry:
                                load_aerobic_low = balance_entry.get('monthlyLoadAerobicLow')
                                load_aerobic_high = balance_entry.get('monthlyLoadAerobicHigh')
                                load_anaerobic = balance_entry.get('monthlyLoadAnaerobic')
                except Exception as ts_err:
                    print(f"Error fetching training status for {date_str}: {ts_err}")
                    
                # Called unconditionally now (#180) - previously gated behind
                # `if recovery_time is None`, so the readiness score itself was never stored
                # on days get_training_status already supplied a recovery_time, and coverage
                # was arbitrary. The recovery_time fallback below is preserved unchanged; it
                # just no longer decides whether the call happens at all.
                try:
                    tr_data = client.get_training_readiness(date_str)
                    if tr_data:
                        if isinstance(tr_data, list) and len(tr_data) > 0:
                            tr_data = tr_data[0]
                        if isinstance(tr_data, dict):
                            if recovery_time is None:
                                recovery_time = tr_data.get('recoveryTime') or tr_data.get('recoveryTimeInHours')
                                if not recovery_time and 'trainingReadinessDTO' in tr_data:
                                    recovery_time = tr_data['trainingReadinessDTO'].get('recoveryTime')
                            score_val = tr_data.get('score')
                            training_readiness = int(score_val) if isinstance(score_val, (int, float)) else None
                            training_readiness_level = tr_data.get('level')
                            training_readiness_feedback = tr_data.get('feedbackLong') or tr_data.get('feedbackShort')
                except Exception as tr_err:
                    print(f"Error fetching training readiness for {date_str}: {tr_err}")
                        
                day_fields = (
                    steps, sleep_hours, resting_hr, active_calories, body_battery, hrv,
                    recovery_time, training_status, stress_avg, stress_max, sleep_deep_hours,
                    sleep_light_hours, sleep_rem_hours, sleep_awake_hours, vo2max,
                    intensity_minutes, sleep_score, training_load_acute, training_load_chronic,
                    acwr, load_aerobic_low, load_aerobic_high, load_anaerobic, training_readiness,
                )
                if any(v is not None for v in day_fields):
                    any_day_succeeded = True

                cursor.execute('''
                    INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, stress_avg, stress_max, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, vo2max, intensity_minutes, sleep_score, training_load_acute, training_load_chronic, acwr, acwr_status, load_aerobic_low, load_aerobic_high, load_anaerobic, training_readiness, training_readiness_level, training_readiness_feedback)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        steps = COALESCE(excluded.steps, garmin_health.steps),
                        sleep_hours = COALESCE(excluded.sleep_hours, garmin_health.sleep_hours),
                        resting_hr = COALESCE(excluded.resting_hr, garmin_health.resting_hr),
                        active_calories = COALESCE(excluded.active_calories, garmin_health.active_calories),
                        workout_type = excluded.workout_type,
                        workout_duration = excluded.workout_duration,
                        body_battery = COALESCE(excluded.body_battery, garmin_health.body_battery),
                        hrv = COALESCE(excluded.hrv, garmin_health.hrv),
                        recovery_time = COALESCE(excluded.recovery_time, garmin_health.recovery_time),
                        training_status = COALESCE(excluded.training_status, garmin_health.training_status),
                        stress_avg = COALESCE(excluded.stress_avg, garmin_health.stress_avg),
                        stress_max = COALESCE(excluded.stress_max, garmin_health.stress_max),
                        sleep_deep_hours = COALESCE(excluded.sleep_deep_hours, garmin_health.sleep_deep_hours),
                        sleep_light_hours = COALESCE(excluded.sleep_light_hours, garmin_health.sleep_light_hours),
                        sleep_rem_hours = COALESCE(excluded.sleep_rem_hours, garmin_health.sleep_rem_hours),
                        sleep_awake_hours = COALESCE(excluded.sleep_awake_hours, garmin_health.sleep_awake_hours),
                        vo2max = COALESCE(excluded.vo2max, garmin_health.vo2max),
                        intensity_minutes = COALESCE(excluded.intensity_minutes, garmin_health.intensity_minutes),
                        sleep_score = COALESCE(excluded.sleep_score, garmin_health.sleep_score),
                        training_load_acute = COALESCE(excluded.training_load_acute, garmin_health.training_load_acute),
                        training_load_chronic = COALESCE(excluded.training_load_chronic, garmin_health.training_load_chronic),
                        acwr = COALESCE(excluded.acwr, garmin_health.acwr),
                        acwr_status = COALESCE(excluded.acwr_status, garmin_health.acwr_status),
                        load_aerobic_low = COALESCE(excluded.load_aerobic_low, garmin_health.load_aerobic_low),
                        load_aerobic_high = COALESCE(excluded.load_aerobic_high, garmin_health.load_aerobic_high),
                        load_anaerobic = COALESCE(excluded.load_anaerobic, garmin_health.load_anaerobic),
                        training_readiness = COALESCE(excluded.training_readiness, garmin_health.training_readiness),
                        training_readiness_level = COALESCE(excluded.training_readiness_level, garmin_health.training_readiness_level),
                        training_readiness_feedback = COALESCE(excluded.training_readiness_feedback, garmin_health.training_readiness_feedback)
                ''', (date_str, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, stress_avg, stress_max, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, vo2max, intensity_minutes, sleep_score, training_load_acute, training_load_chronic, acwr, acwr_status, load_aerobic_low, load_aerobic_high, load_anaerobic, training_readiness, training_readiness_level, training_readiness_feedback))

            conn.commit()

        if dates_to_sync and not any_day_succeeded:
            raise Exception(
                "Garmin sync retrieved no health data for any day in the requested window - "
                "the session may have expired or Garmin may be rate-limiting/blocking login."
            )
    except Exception as e:
        raise e


async def auto_optimize_workouts_after_sync_async():
    """Trigger the async workout optimization on the main event loop."""
    from backend.routes.trainer import get_trainer_profile, core_optimize_upcoming_workouts

    profile = get_trainer_profile()
    if not profile:
        return
    # Default ON: only an explicit 0/false disables automatic adjustment.
    if str(profile.get("auto_adjust", 1)).strip().lower() in ("0", "false"):
        return
    if not (profile.get("goals") or profile.get("event")):
        return  # No training goal yet — nothing meaningful to optimize toward.

    result = await core_optimize_upcoming_workouts(trigger="garmin_sync")
    if isinstance(result, dict) and result.get("changes_count"):
        print(f"[GARMIN SYNC] COACH AI justerade {result['changes_count']} kommande pass efter ny Garmin-data.")


async def drain_garmin_backfill(email, password) -> dict:
    """Syncs one chunk of the pending backfill window, oldest days first.

    Advances the stored range only after the chunk lands, so a failure mid-drain leaves the
    days queued for the next run rather than skipping them. Returns a summary of what moved.
    """
    pending = _read_backfill_range()
    if not pending:
        return {"status": "idle", "remaining_days": 0}

    start, end = pending
    chunk_end = min(start + datetime.timedelta(days=BACKFILL_CHUNK_DAYS - 1), end)
    chunk_days = (chunk_end - start).days + 1

    print(f"[Garmin Sync] Backfilling {start} .. {chunk_end} ({chunk_days} days).")
    await asyncio.to_thread(run_garmin_sync_task_blocking, email, password, chunk_days, chunk_end)

    next_start = chunk_end + datetime.timedelta(days=1)
    _write_backfill_range(next_start, end)
    remaining = max(0, (end - next_start).days + 1)
    if remaining == 0:
        print("[Garmin Sync] Backfill complete.")
    return {"status": "success", "synced_days": chunk_days, "remaining_days": remaining}


def _run_activity_detail_pass_blocking(email, password):
    """Blocking helper: logs in and runs one capped activity-detail pass (Issue #182)."""
    from garminconnect import Garmin
    token_dir = _garmin_token_dir()
    os.makedirs(token_dir, exist_ok=True)
    client = Garmin(email, password)
    client.login(tokenstore=token_dir)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        result = fetch_activity_details(client, cursor, since_date=DETAIL_FETCH_SHIP_DATE)
        conn.commit()
    return result


def _run_benchmarks_refresh_blocking(email, password):
    """Blocking helper: logs in and runs the self-limited benchmarks refresh (Issue #186)."""
    from garminconnect import Garmin
    token_dir = _garmin_token_dir()
    os.makedirs(token_dir, exist_ok=True)
    client = Garmin(email, password)
    client.login(tokenstore=token_dir)
    return refresh_garmin_benchmarks(client)


async def run_garmin_sync_flow(email, password, days):
    """Asynchronous orchestrator for Garmin Connect synchronization.
    Runs the blocking sync in a thread executor, then executes optimization in the main event loop.
    """
    try:
        # Run blocking HTTP requests / SQLite updates in a background thread executor
        await asyncio.to_thread(run_garmin_sync_task_blocking, email, password, days)
        set_sync_state("garmin", "success")

        # Drain a chunk of any gap a previous cap left behind (Issue #56). Done after the
        # recent window so today's data is never held up by history, and one chunk per run
        # so a long absence spreads its catch-up over several syncs instead of one burst.
        try:
            await drain_garmin_backfill(email, password)
        except Exception as backfill_err:
            # A failed backfill must not fail the sync that already succeeded; the range
            # stays queued and the next run retries it.
            print(f"[GARMIN SYNC] Backfill chunk was skipped: {backfill_err}")

        # Fetch per-activity detail (training effect, running dynamics, ...) for activities
        # that have never had it (Issue #182). Own try/except: a failure here must not fail
        # a sync that already succeeded - a re-login on top of the one above is deliberate,
        # since this runs as its own blocking pass after the health-metric sync's connection
        # has already been used and closed.
        try:
            detail_result = await asyncio.to_thread(_run_activity_detail_pass_blocking, email, password)
            if detail_result.get("fetched") or detail_result.get("remaining"):
                print(f"[GARMIN SYNC] Activity detail pass: {detail_result}")
        except Exception as detail_pass_err:
            print(f"[GARMIN SYNC] Activity detail pass was skipped: {detail_pass_err}")

        # Refresh slow-moving account benchmarks (threshold, race predictions, PRs, trend
        # scores - Issue #186). Self-limited to a weekly cadence inside the function itself,
        # so this is cheap to call every sync; own try/except for the same reason as above.
        try:
            benchmarks_result = await asyncio.to_thread(_run_benchmarks_refresh_blocking, email, password)
            if benchmarks_result.get("fetched"):
                print(f"[GARMIN SYNC] Benchmarks refreshed: {benchmarks_result['fetched']}")
        except Exception as benchmarks_err:
            print(f"[GARMIN SYNC] Benchmarks refresh was skipped: {benchmarks_err}")

        # Refresh the PT health baselines from the freshly-synced data. This is a
        # cheap SQLite pass that self-limits to a weekly cadence (Issue #35), so it is
        # safe to call after every sync.
        try:
            from backend.routes.trainer import recompute_health_baselines
            result = recompute_health_baselines()
            if isinstance(result, dict) and result.get("status") == "success":
                print(f"[GARMIN SYNC] PT-hälsobaslinjer uppdaterade: {result.get('updated')}")
        except Exception as base_err:
            print(f"[GARMIN SYNC] Baseline recompute was skipped: {base_err}")

        # Run the async optimizer directly in the main event loop
        try:
            await auto_optimize_workouts_after_sync_async()
        except Exception as opt_err:
            print(f"[GARMIN SYNC] Automatic workout optimization was skipped: {opt_err}")
    except Exception as e:
        print(f"[GARMIN SYNC TASK ERROR]: {e}")
        set_sync_state("garmin", _classify_garmin_error(e), str(e))


@router.get("/api/garmin/sync")
async def get_garmin_sync(
    days: int = Query(None, description="Number of days to sync. If not provided, syncs since the last sync date (capped at 30).")
):
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    
    if not email or not password:
        raise HTTPException(
            status_code=400,
            detail="Garmin Connect credentials are missing. Enter the email and password in Settings."
        )
        
    if days is None:
        last_sync_val = get_api_key("last_sync_garmin")
        if last_sync_val:
            try:
                # Parse the last sync date and calculate difference
                last_sync_dt = datetime.datetime.strptime(last_sync_val, "%Y-%m-%d %H:%M:%S").date()
                today = today_local()
                wanted = max(1, (today - last_sync_dt).days + 1)  # +1 so today is included
                days = min(MAX_SYNC_DAYS, wanted)
                if wanted > days:
                    # The cap trimmed the window. Remember the part it cut off, because
                    # last_sync_garmin advances to now on success and would otherwise
                    # strand those days permanently.
                    oldest_covered = today - datetime.timedelta(days=days - 1)
                    _queue_backfill(last_sync_dt, oldest_covered - datetime.timedelta(days=1))
            except Exception as e:
                # An unparseable timestamp says nothing about how much is missing, so fall
                # back to the same window used when there is no history at all. Narrowing
                # to a single day here would quietly shrink every future sync.
                print(f"[Garmin Sync] Error calculating days since last sync: {e}")
                days = DEFAULT_SYNC_DAYS
        else:
            days = DEFAULT_SYNC_DAYS  # No sync history yet
    else:
        # An explicitly-supplied `days` (HTTP caller or the get_garmin_health AI tool) bypassed
        # the cap entirely - only the no-args branch above clamped to MAX_SYNC_DAYS. An
        # unbounded value here queues a run that hammers Garmin's API (~8 calls/day) far past
        # its rate limits and monopolizes the single-worker background task queue.
        days = max(1, min(int(days), MAX_SYNC_DAYS))

    set_sync_state("garmin", "syncing")

    from backend.services.task_queue import enqueue_task
    enqueue_task(run_garmin_sync_flow, email, password, days)
    
    return {
        'status': 'syncing',
        'message': "Garmin sync started in the background queue."
    }

@router.get("/api/garmin/delete")
async def delete_garmin_log(date: str = Query(..., description="Date to delete")):
    date_to_delete = date.strip()
    if not date_to_delete:
        raise HTTPException(status_code=400, detail="Date is missing.")
        
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM garmin_health WHERE date = ?', (date_to_delete,))
            deleted = cursor.rowcount
            conn.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail=f"No log was found for {date_to_delete}.")
        return {'status': 'success', 'message': f"The log for {date_to_delete} was deleted."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Garmin tokens last on the order of 6 months (#181); warn once a cached tokenstore
# approaches that, rather than waiting for the failure.
TOKEN_STALE_WARNING_DAYS = 150


def _garmin_token_age_days():
    """Age in days of the cached Garmin tokenstore, or None if it doesn't exist yet."""
    token_dir = _garmin_token_dir()
    if not os.path.isdir(token_dir):
        return None
    try:
        mtimes = [
            os.path.getmtime(os.path.join(token_dir, name))
            for name in os.listdir(token_dir)
            if os.path.isfile(os.path.join(token_dir, name))
        ]
    except OSError:
        return None
    if not mtimes:
        return None
    return (datetime.datetime.now() - datetime.datetime.fromtimestamp(max(mtimes))).days


@router.get("/api/garmin/credentials")
async def get_garmin_credentials():
    email = get_api_key('freja_garmin_email') or ""
    token_age_days = _garmin_token_age_days()
    return {
        "email": email,
        "token_age_days": token_age_days,
        "token_stale_warning": token_age_days is not None and token_age_days >= TOKEN_STALE_WARNING_DAYS,
    }


@router.post("/api/garmin/reauth")
async def post_garmin_reauth():
    """Clears the cached tokenstore and performs a fresh login, so renewing an expired
    Garmin session is a click in the settings panel rather than deleting a file over
    SSH (#181)."""
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    if not email or not password:
        raise HTTPException(
            status_code=400,
            detail="Garmin Connect credentials are missing. Enter the email and password in Settings."
        )

    token_dir = _garmin_token_dir()
    try:
        import shutil
        if os.path.isdir(token_dir):
            shutil.rmtree(token_dir)
        os.makedirs(token_dir, exist_ok=True)

        from garminconnect import Garmin
        client = Garmin(email, password)
        # Note: this deliberately does not pass return_on_mfa - an account with 2FA enabled
        # will fail here with an auth-classified error rather than complete a two-step MFA
        # flow. The installed client (garminconnect==0.3.6) does support resuming an MFA
        # login (Garmin(..., return_on_mfa=True) + client.resume_login(client_state, code)),
        # but that needs holding client_state safely between two HTTP calls and confirming
        # 2FA is actually enabled on this account first - deferred rather than guessed at.
        client.login(tokenstore=token_dir)
        set_sync_state("garmin", "success")
        return {"status": "success", "message": "Garmin re-authentication succeeded."}
    except Exception as e:
        set_sync_state("garmin", _classify_garmin_error(e), str(e))
        raise HTTPException(status_code=400, detail=f"Garmin re-authentication failed: {e}")

@router.post("/api/garmin/data")
@router.post("/api/garmin/save")
async def post_garmin_data(request: Request):
    try:
        data = await request.json()
        date_str = data.get('date')
        if not date_str:
            raise ValueError('Date is missing.')
        steps = int(data.get('steps', 0) or 0)
        sleep_hours = float(data.get('sleep_hours', 0.0) or 0.0)
        resting_hr = int(data.get('resting_hr', 0) or 0)
        active_calories = int(data.get('active_calories', 0) or 0)
        workout_type = data.get('workout_type', '').strip() or None
        workout_duration = int(data.get('workout_duration', 0) or 0)
        body_battery = int(data.get('body_battery')) if data.get('body_battery') is not None else None
        hrv = int(data.get('hrv')) if data.get('hrv') is not None else None
        recovery_time = int(data.get('recovery_time')) if data.get('recovery_time') is not None else None
        training_status = data.get('training_status', '').strip() or None
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    steps = excluded.steps,
                    sleep_hours = excluded.sleep_hours,
                    resting_hr = excluded.resting_hr,
                    active_calories = excluded.active_calories,
                    workout_type = excluded.workout_type,
                    workout_duration = excluded.workout_duration,
                    body_battery = excluded.body_battery,
                    hrv = excluded.hrv,
                    recovery_time = excluded.recovery_time,
                    training_status = excluded.training_status
            ''', (date_str, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status))
            conn.commit()
        return {'status': 'success', 'message': 'Garmin log saved.'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


