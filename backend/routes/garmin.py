"""Garmin Connect API routes using FastAPI."""

import datetime
import os
from fastapi import APIRouter, HTTPException, Query, Request, BackgroundTasks
from backend.config import PROJECT_ROOT
from backend.database import get_db_connection, get_api_key
from backend.services.sync_status import set_sync_state

router = APIRouter()

@router.get("/api/garmin/data")
async def get_garmin_data(days: int = Query(7, description="Number of days to retrieve")):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status 
                FROM garmin_health 
                ORDER BY date DESC 
                LIMIT ?
            ''', (days,))
            rows = cursor.fetchall()
        
        results = []
        for row in rows:
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
                'training_status': row[10]
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run_garmin_sync_task(email, password, days):
    try:
        from garminconnect import Garmin
        token_dir = os.path.join(os.path.dirname(os.path.abspath(PROJECT_ROOT)), '.garminconnect')
        os.makedirs(token_dir, exist_ok=True)
        
        client = Garmin(email, password)
        client.login(tokenstore=token_dir)
        
        today = datetime.date.today()
        dates_to_sync = [(today - datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
        dates_to_sync.reverse()
        
        activities = []
        try:
            activities = client.get_activities(0, 30)
        except Exception as act_err:
            print(f"Error fetching activities: {act_err}")
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            steps = 0
            active_calories = 0
            sleep_hours = 0.0
            resting_hr = 0
            workout_type = None
            workout_duration = 0
            body_battery = None
            hrv = None
            recovery_time = None
            training_status = None
            
            for date_str in dates_to_sync:
                try:
                    stats = client.get_stats(date_str)
                    if stats:
                        steps = int(stats.get('totalSteps', 0) or 0)
                        active_calories = int(stats.get('activeCalories', 0) or 0)
                except Exception as stats_err:
                    print(f"Error fetching stats for {date_str}: {stats_err}")
                try:
                    sleep_data = client.get_sleep_data(date_str)
                    if sleep_data:
                        sleep_time_sec = sleep_data.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) or 0
                        sleep_hours = round(sleep_time_sec / 3600.0, 1)
                except Exception as sleep_err:
                    print(f"Error fetching sleep for {date_str}: {sleep_err}")
                try:
                    heart_rates = client.get_heart_rates(date_str)
                    if heart_rates:
                        resting_hr = int(heart_rates.get('restingHeartRate', 0) or 0)
                except Exception as hr_err:
                    print(f"Error fetching heart rates for {date_str}: {hr_err}")
                    
                workout_type = None
                workout_duration = 0
                for act in activities:
                    start_time_local = act.get('startTimeLocal', '')
                    if start_time_local and start_time_local.startswith(date_str):
                        act_type = act.get('activityType', {}).get('typeKey')
                        # Garmin's typeKey -> the Swedish label persisted in garmin_health.workout_type.
                        # These labels are rendered directly in the HUD dashboard, which is why they are
                        # Swedish. Changing them would require migrating existing rows.
                        type_mapping = {
                            'running': 'Löpning',
                            'cycling': 'Cykling',
                            'fitness_equipment': 'Styrketräning',
                            'swimming': 'Simning',
                            'walking': 'Promenad',
                            'yoga': 'Yoga'
                        }
                        if act_type in type_mapping:
                            workout_type = type_mapping[act_type]
                        else:
                            workout_type = act_type.replace('_', ' ').capitalize()
                        workout_duration = int(round(act.get('duration', 0) / 60.0))
                        break
                        
                try:
                    bb_data = client.get_body_battery(date_str)
                    if bb_data and isinstance(bb_data, list):
                        day_bb = bb_data[0]
                        body_battery = day_bb.get('highest')
                except Exception as bb_err:
                    print(f"Error fetching body battery for {date_str}: {bb_err}")
                try:
                    hrv_data = client.get_hrv_data(date_str)
                    if hrv_data and isinstance(hrv_data, dict):
                        hrv_summary = hrv_data.get('hrvSummary', {})
                        if hrv_summary:
                            hrv = hrv_summary.get('lastNightAvg')
                except Exception as hrv_err:
                    print(f"Error fetching HRV for {date_str}: {hrv_err}")
                    
                recovery_time = None
                training_status = None
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
                except Exception as ts_err:
                    print(f"Error fetching training status for {date_str}: {ts_err}")
                    
                if recovery_time is None:
                    try:
                        tr_data = client.get_training_readiness(date_str)
                        if tr_data:
                            if isinstance(tr_data, list) and len(tr_data) > 0:
                                tr_data = tr_data[0]
                            if isinstance(tr_data, dict):
                                recovery_time = tr_data.get('recoveryTime') or tr_data.get('recoveryTimeInHours')
                                if not recovery_time and 'trainingReadinessDTO' in tr_data:
                                    recovery_time = tr_data['trainingReadinessDTO'].get('recoveryTime')
                    except Exception as tr_err:
                        print(f"Error fetching training readiness for {date_str}: {tr_err}")
                        
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
        set_sync_state("garmin", "success")
        # Fresh recovery data just landed — let COACH AI re-tune upcoming workouts.
        try:
            _auto_optimize_workouts_after_sync()
        except Exception as opt_err:
            print(f"[GARMIN SYNC] Automatic workout optimization was skipped: {opt_err}")
    except Exception as e:
        print(f"[GARMIN SYNC TASK ERROR]: {e}")
        set_sync_state("garmin", "error", str(e))


def _auto_optimize_workouts_after_sync():
    """After a Garmin sync, let COACH AI adjust the upcoming calendar workouts to
    the user's latest recovery — unless auto-adjust is disabled or no training
    goal is set. Runs the async optimizer in a private event loop (we're in a
    background worker thread) and never raises back into the sync task."""
    import asyncio
    from backend.routes.trainer import get_trainer_profile, core_optimize_upcoming_workouts

    profile = get_trainer_profile()
    if not profile:
        return
    # Default ON: only an explicit 0/false disables automatic adjustment.
    if str(profile.get("auto_adjust", 1)).strip().lower() in ("0", "false"):
        return
    if not (profile.get("goals") or profile.get("event")):
        return  # No training goal yet — nothing meaningful to optimize toward.

    result = asyncio.run(core_optimize_upcoming_workouts(trigger="garmin_sync"))
    if isinstance(result, dict) and result.get("changes_count"):
        print(f"[GARMIN SYNC] COACH AI justerade {result['changes_count']} kommande pass efter ny Garmin-data.")

@router.get("/api/garmin/sync")
async def get_garmin_sync(
    background_tasks: BackgroundTasks,
    days: int = Query(7, description="Number of days to sync")
):
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    
    if not email or not password:
        raise HTTPException(
            status_code=400,
            detail="Garmin Connect credentials are missing. Enter the email and password in Settings."
        )
        
    set_sync_state("garmin", "syncing")
    background_tasks.add_task(run_garmin_sync_task, email, password, days)
    return {
        'status': 'syncing',
        'message': "Garmin sync started in the background."
    }

@router.get("/api/garmin/delete")
async def delete_garmin_log(date: str = Query(..., description="Date to delete")):
    date_to_delete = date.strip()
    if not date_to_delete:
        raise HTTPException(status_code=400, detail="Datum saknas.")
        
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM garmin_health WHERE date = ?', (date_to_delete,))
            conn.commit()
        return {'status': 'success', 'message': f"The log for {date_to_delete} was deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/garmin/credentials")
async def get_garmin_credentials():
    email = get_api_key('freja_garmin_email') or ""
    return {"email": email}

@router.post("/api/garmin/data")
@router.post("/api/garmin/save")
async def post_garmin_data(request: Request):
    try:
        data = await request.json()
        date_str = data.get('date')
        if not date_str:
            raise ValueError('Datum saknas.')
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
        return {'status': 'success', 'message': 'Garmin-logg sparad.'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


