"""Garmin/Withings/Strava health-data tools."""

import datetime
from backend.database import get_api_key
from backend.routes.garmin import run_garmin_sync_flow, get_garmin_data
from backend.routes.withings import run_withings_sync_task, get_withings_data
from backend.routes.strava import (
    run_strava_sync_task,
    get_strava_data,
    get_strava_activity_details,
    get_strava_athlete_stats,
)
from ._registry import registry, Math_round

def is_sync_recent(provider: str, max_age_hours: int = 12) -> bool:
    try:
        last_sync_value = get_api_key(f"last_sync_{provider}")
        if last_sync_value:
            last_sync = datetime.datetime.strptime(last_sync_value, "%Y-%m-%d %H:%M:%S")
            age = datetime.datetime.now() - last_sync
            if age.total_seconds() < max_age_hours * 3600:
                return True
    except Exception as e:
        print(f"[tool_registry] Error checking recent sync for {provider}: {e}")
    return False

@registry.register(
    name="get_garmin_health",
    description="Gets the user's latest Garmin health and training data (steps, sleep, resting heart rate, calories, body battery, HRV, recovery time, training status and workouts). Defaults to 1 day (only the last 24 hours) unless the user explicitly asks for a longer period such as the last week.",
    permission_key="freja_tool_get_garmin_health_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "days": {
                "type": "INTEGER",
                "description": "Number of days of history to fetch (default is 1, i.e. only the most recent day)."
            }
        }
    },
)
async def exec_garmin_health(args):
    days = int(args.get("days", 1) or 1)
    sync_status = "not performed"
    sync_message = ""
    
    # 1. Retrieve keys and sync
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    
    if email and password:
        # Force sync if fetching historical data (days > 1) or if no recent sync has run in the last 15 minutes.
        # We do not skip if today's data already exists, since steps and body battery update throughout the day.
        is_recent = is_sync_recent("garmin", max_age_hours=0.25)
        
        if is_recent and days <= 1:
            sync_status = "success"
            sync_message = "Garmin sync skipped (recently updated)."
            print("[Garmin Tool] Recent sync found in the last 15 minutes. Skipping API sync, using cached DB data.")
        else:
            try:
                from backend.services.task_queue import enqueue_task
                await enqueue_task(run_garmin_sync_flow, email, password, days)
                sync_status = "success"
                sync_message = "Garmin sync completed."
            except Exception as sync_err:
                sync_status = "failed"
                sync_message = str(sync_err)
            
    # 2. Query database for health logs
    try:
        data = await get_garmin_data(days=days)
        if not data:
            return {
                "sync_status": sync_status,
                "sync_message": sync_message,
                "message": "No Garmin data was found in the database."
            }
            
        # 3. Calculate summary stats
        total_steps = 0
        steps_count = 0
        total_sleep = 0.0
        sleep_count = 0
        total_hr = 0
        hr_count = 0
        total_calories = 0
        calories_count = 0
        workout_days = 0
        total_workout_min = 0
        total_bb = 0
        bb_count = 0
        total_hrv = 0
        hrv_count = 0
        total_recovery = 0
        recovery_count = 0
        total_stress = 0
        stress_count = 0
        total_sleep_score = 0
        sleep_score_count = 0
        total_intensity = 0
        intensity_count = 0

        for day in data:
            # Count each metric only on days that actually carry a reading. Days the watch
            # was not worn store NULL, and averaging those in as 0 reported, for example, a
            # resting heart rate far below the real one - the same distortion the counted
            # metrics below already avoid by dividing by their own sample count.
            if day.get('steps') is not None:
                total_steps += day['steps']
                steps_count += 1
            if day.get('sleep_hours') is not None:
                total_sleep += day['sleep_hours']
                sleep_count += 1
            if day.get('resting_hr') is not None:
                total_hr += day['resting_hr']
                hr_count += 1
            if day.get('active_calories') is not None:
                total_calories += day['active_calories']
                calories_count += 1
            # "Ingen" is the Swedish placeholder get_garmin_data() substitutes for a NULL
            # workout_type, i.e. a rest day. Anything else counts as a real workout.
            if day.get('workout_type') and day.get('workout_type') != "Ingen":
                workout_days += 1
                total_workout_min += day.get('workout_duration', 0) or 0
            if day.get('body_battery') is not None:
                total_bb += day['body_battery']
                bb_count += 1
            if day.get('hrv') is not None:
                total_hrv += day['hrv']
                hrv_count += 1
            if day.get('recovery_time') is not None:
                total_recovery += day['recovery_time']
                recovery_count += 1
            if day.get('stress_avg') is not None:
                total_stress += day['stress_avg']
                stress_count += 1
            if day.get('sleep_score') is not None:
                total_sleep_score += day['sleep_score']
                sleep_score_count += 1
            if day.get('intensity_minutes') is not None:
                total_intensity += day['intensity_minutes']
                intensity_count += 1

        num_days = len(data)
        avg_steps = Math_round(total_steps / steps_count) if steps_count > 0 else 0
        avg_sleep = round(total_sleep / sleep_count, 1) if sleep_count > 0 else 0.0
        avg_hr = Math_round(total_hr / hr_count) if hr_count > 0 else 0
        avg_calories = Math_round(total_calories / calories_count) if calories_count > 0 else 0
        avg_bb = Math_round(total_bb / bb_count) if bb_count > 0 else None
        avg_hrv = Math_round(total_hrv / hrv_count) if hrv_count > 0 else None
        avg_recovery = Math_round(total_recovery / recovery_count) if recovery_count > 0 else None
        avg_stress = Math_round(total_stress / stress_count) if stress_count > 0 else None
        avg_sleep_score = Math_round(total_sleep_score / sleep_score_count) if sleep_score_count > 0 else None
        latest_vo2max = data[0].get('vo2max') if data else None

        return {
            "sync_status": sync_status,
            "sync_message": sync_message,
            "period_days": num_days,
            "latest_metrics": {
                "training_status": data[0].get('training_status') if data else None,
                "recovery_time_hours": data[0].get('recovery_time') if data else None,
                "vo2max": latest_vo2max
            },
            "averages": {
                "avg_daily_steps": avg_steps,
                "avg_sleep_hours": avg_sleep,
                "avg_resting_heart_rate": avg_hr,
                "avg_active_calories": avg_calories,
                "avg_body_battery": avg_bb,
                "avg_hrv": avg_hrv,
                "avg_recovery_time_hours": avg_recovery,
                "avg_stress": avg_stress,
                "avg_sleep_score": avg_sleep_score,
                "total_intensity_minutes": total_intensity if intensity_count > 0 else None,
                "total_workouts": workout_days,
                "total_workout_minutes": total_workout_min
            },
            "daily_logs": data
        }
    except Exception as e:
        return {"error": f"Could not fetch Garmin data: {str(e)}"}

@registry.register(
    name="get_withings_health",
    description="Gets the user's latest Withings measurements including weight, body composition, heart rate, sleep statistics (score, duration) and daily activity (steps, calories). The 'days' parameter sets how many days of history to fetch (default 7).",
    permission_key="freja_tool_get_withings_health_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "days": {
                "type": "INTEGER",
                "description": "Number of days of history to fetch (default 7)."
            }
        }
    },
)
async def exec_withings_health(args):
    days = int(args.get("days", 7) or 7)
    sync_status = "not performed"
    sync_message = ""
    
    # 1. Retrieve keys and sync synchronously
    client_id = get_api_key('freja_withings_client_id') or ""
    client_secret = get_api_key('freja_withings_client_secret') or ""
    refresh_token = get_api_key('freja_withings_refresh_token') or ""

    if client_id and client_secret and refresh_token:
        if is_sync_recent("withings"):
            sync_status = "success"
            sync_message = "Withings sync skipped (recently updated)."
            print("[Withings Tool] Recent sync found. Skipping API sync, using cached DB data.")
        else:
            try:
                await run_withings_sync_task(client_id, client_secret, refresh_token, days)
                sync_status = "success"
                sync_message = "Withings sync completed."
            except Exception as sync_err:
                sync_status = "failed"
                sync_message = str(sync_err)
            
    # 2. Query database for metrics
    try:
        data = await get_withings_data(days=days)
        if not data:
            return {
                "sync_status": sync_status,
                "sync_message": sync_message,
                "message": "No Withings data was found in the database."
            }
            
        # Calculate averages
        total_weight, total_fat, total_bone, total_pulse = 0.0, 0.0, 0.0, 0.0
        count_weight, count_fat, count_bone, count_pulse = 0, 0, 0, 0
        total_sleep, count_sleep = 0, 0
        total_sleep_score, count_sleep_score = 0, 0
        total_steps, count_steps = 0, 0
        total_calories, count_calories = 0, 0
        
        for entry in data:
            if entry.get('weight') is not None:
                total_weight += entry['weight']
                count_weight += 1
            if entry.get('fat_ratio') is not None:
                total_fat += entry['fat_ratio']
                count_fat += 1
            if entry.get('bone_mass') is not None:
                total_bone += entry['bone_mass']
                count_bone += 1
            if entry.get('heart_pulse') is not None:
                total_pulse += entry['heart_pulse']
                count_pulse += 1
            if entry.get('sleep_duration') is not None:
                total_sleep += entry['sleep_duration']
                count_sleep += 1
            if entry.get('sleep_score') is not None:
                total_sleep_score += entry['sleep_score']
                count_sleep_score += 1
            if entry.get('steps') is not None:
                total_steps += entry['steps']
                count_steps += 1
            if entry.get('calories') is not None:
                total_calories += entry['calories']
                count_calories += 1
                
        avg_weight = round(total_weight / count_weight, 2) if count_weight > 0 else None
        avg_fat = round(total_fat / count_fat, 1) if count_fat > 0 else None
        avg_bone = round(total_bone / count_bone, 2) if count_bone > 0 else None
        avg_pulse = Math_round(total_pulse / count_pulse) if count_pulse > 0 else None
        avg_sleep = round((total_sleep / count_sleep) / 3600.0, 2) if count_sleep > 0 else None
        avg_sleep_score = Math_round(total_sleep_score / count_sleep_score) if count_sleep_score > 0 else None
        avg_steps = Math_round(total_steps / count_steps) if count_steps > 0 else None
        avg_calories = Math_round(total_calories / count_calories) if count_calories > 0 else None
        
        formatted_measurements = []
        for entry in data:
            formatted_measurements.append({
                'date': entry.get('date'),
                'weight': entry.get('weight'),
                'fat_ratio': entry.get('fat_ratio'),
                'bone_mass': entry.get('bone_mass'),
                'heart_pulse': entry.get('heart_pulse'),
                'sleep_hours': round(entry['sleep_duration'] / 3600.0, 2) if entry.get('sleep_duration') else None,
                'sleep_deep_hours': round(entry['sleep_deep'] / 3600.0, 2) if entry.get('sleep_deep') else None,
                'sleep_rem_hours': round(entry['sleep_rem'] / 3600.0, 2) if entry.get('sleep_rem') else None,
                'sleep_score': entry.get('sleep_score'),
                'steps': entry.get('steps'),
                'distance_km': round(entry['distance'] / 1000.0, 2) if entry.get('distance') else None,
                'calories': entry.get('calories'),
                'elevation_m': entry.get('elevation')
            })
            
        return {
            "sync_status": sync_status,
            "sync_message": sync_message,
            "period_days": len(data),
            "averages": {
                "avg_weight": avg_weight,
                "avg_fat_ratio": avg_fat,
                "avg_bone_mass": avg_bone,
                "avg_heart_pulse": avg_pulse,
                "avg_steps": avg_steps,
                "avg_sleep_hours": avg_sleep,
                "avg_sleep_score": avg_sleep_score,
                "avg_active_calories": avg_calories
            },
            "measurements": formatted_measurements
        }
    except Exception as e:
        return {"error": f"Could not fetch Withings data: {str(e)}"}

@registry.register(
    name="get_strava_data",
    description="Gets the user's latest Strava activities (name, type, distance, moving time, elevation gain, average heart rate, max heart rate and calories). Defaults to 7 days of history unless the user explicitly asks for a longer period such as 14 or 30 days.",
    permission_key="freja_tool_get_strava_data_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "days": {
                "type": "INTEGER",
                "description": "Number of days of history to fetch (default is 7)."
            }
        }
    },
)
async def exec_strava_data(args):
    days = int(args.get("days", 7) or 7)
    sync_status = "not performed"
    sync_message = ""
    
    # 1. Retrieve keys and sync synchronously
    client_id = get_api_key('freja_strava_client_id') or ""
    client_secret = get_api_key('freja_strava_client_secret') or ""
    refresh_token = get_api_key('freja_strava_refresh_token') or ""

    if client_id and client_secret and refresh_token:
        if is_sync_recent("strava"):
            sync_status = "success"
            sync_message = "Strava sync skipped (recently updated)."
            print("[Strava Tool] Recent sync found. Skipping API sync, using cached DB data.")
        else:
            try:
                await run_strava_sync_task(client_id, client_secret, refresh_token, days)
                sync_status = "success"
                sync_message = "Strava sync completed."
            except Exception as sync_err:
                sync_status = "failed"
                sync_message = str(sync_err)
            
    # 2. Retrieve database records
    try:
        data = await get_strava_data(days=days)
        if not data:
            return {
                "sync_status": sync_status,
                "sync_message": sync_message,
                "message": "No Strava activities were found in the database."
            }
            
        # Calculate summary statistics
        total_distance = 0.0
        total_moving_time = 0
        total_elevation = 0.0
        total_calories = 0.0
        heart_rate_sum = 0.0
        heart_rate_count = 0
        max_heart_rate_peak = 0
        activity_count = len(data)
        
        for act in data:
            total_distance += act.get('distance', 0.0) or 0.0
            total_moving_time += act.get('moving_time', 0) or 0
            total_elevation += act.get('total_elevation_gain', 0.0) or 0.0
            total_calories += act.get('calories', 0.0) or 0.0
            
            if act.get('average_heartrate') is not None:
                heart_rate_sum += act['average_heartrate']
                heart_rate_count += 1
            if act.get('max_heartrate') is not None:
                if act['max_heartrate'] > max_heart_rate_peak:
                    max_heart_rate_peak = act['max_heartrate']
                    
        avg_heart_rate = Math_round(heart_rate_sum / heart_rate_count) if heart_rate_count > 0 else None
        
        return {
            "sync_status": sync_status,
            "sync_message": sync_message,
            "period_days": days,
            "summary": {
                "activity_count": activity_count,
                "total_distance_meters": Math_round(total_distance),
                "total_distance_km": round(total_distance / 1000.0, 2),
                "total_moving_time_seconds": total_moving_time,
                "total_moving_time_minutes": Math_round(total_moving_time / 60),
                "total_elevation_gain_meters": Math_round(total_elevation),
                "total_calories_kcal": Math_round(total_calories),
                "average_heartrate": avg_heart_rate,
                "max_heartrate_peak": max_heart_rate_peak if max_heart_rate_peak > 0 else None
            },
            "activities": data
        }
    except Exception as e:
        return {"error": f"Could not fetch Strava activities: {str(e)}"}

@registry.register(
    name="get_strava_activity_analysis",
    description="Gets lap times (laps/splits) plus heart rate and power zone distributions for one specific activity ID. This makes it possible to analyse tempo, pacing and aerobic/anaerobic load during the session.",
    permission_key="freja_tool_get_strava_activity_analysis_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "activity_id": {
                "type": "STRING",
                "description": "The unique activity ID (from Strava, e.g. obtained via get_strava_data)."
            }
        },
        "required": ["activity_id"]
    },
)
async def exec_strava_activity_analysis(args):
    activity_id = args.get("activity_id", "")
    if not activity_id:
        return {"error": "Activity ID is missing."}
    return await get_strava_activity_details(id=activity_id)

@registry.register(
    name="get_strava_athlete_stats",
    description="Gets the user's accumulated training volume, including year-to-date (YTD) and all-time totals plus statistics for the last 4 weeks broken down by running, cycling and swimming.",
    permission_key="freja_tool_get_strava_athlete_stats_allowed",
    parameters={"type": "OBJECT", "properties": {}},
)
async def exec_strava_athlete_stats(args):
    return await get_strava_athlete_stats()

