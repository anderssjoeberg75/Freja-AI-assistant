"""get_personal_trainer_advice, get_trainer_workouts and update_trainer_workout tools."""

import datetime
import json
from backend.database import get_db_connection
from ._registry import registry

@registry.register(
    name="get_personal_trainer_advice",
    description="Fetches the user's health and training data (from Garmin, Strava and Withings) and compiles personal training advice, tips and a training plan based on the user's stated goal.",
    permission_key="freja_tool_get_personal_trainer_advice_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "goal": {
                "type": "STRING",
                "description": "The user's training goal or focus area (e.g. 'lose weight', 'improve running', 'strength training')."
            },
            "limitations": {
                "type": "STRING",
                "description": "Any injuries, illnesses or physical limitations (e.g. 'exercise-induced asthma', 'sensitive knees')."
            }
        },
        "required": ["goal"]
    },
)
async def exec_trainer_advice(args):
    """Gathers the raw health/training/weather context the model needs to write a plan.

    This tool deliberately returns data, not advice: Gemini composes the actual coaching
    text (in Swedish) from the payload. The `/api/trainer/*` routes are the ones that call
    a second model pass with a structured JSON schema."""
    goal = args.get("goal", "health and fitness")
    limitations = args.get("limitations", "")

    # Imported lazily: backend.routes.trainer imports google_calendar, which would
    # otherwise pull a heavier import chain in at module load.
    from backend.routes.trainer import fetch_7day_weather_forecast
    weather_forecast = await fetch_7day_weather_forecast("Stockholm")

    # Fetch complete PT plan context (scheduled workouts, today's workout, active plan, injuries)
    pt_context = await _build_trainer_context_summary(days=14)

    garmin_data = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, sleep_score
                FROM garmin_health ORDER BY date DESC LIMIT 7
            ''')
            garmin_data = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error fetching Garmin data for trainer: {e}")

    strava_data = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT name, type, date, distance, moving_time, total_elevation_gain, average_heartrate, max_heartrate, calories
                FROM strava_activities ORDER BY date DESC LIMIT 7
            ''')
            strava_data = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error fetching Strava data for trainer: {e}")

    withings_data = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, weight, fat_ratio, bone_mass, heart_pulse, sleep_duration, steps, calories, sleep_score
                FROM withings_measurements ORDER BY date DESC LIMIT 7
            ''')
            withings_data = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error fetching Withings data for trainer: {e}")

    return {
        "goal": goal,
        "limitations": limitations,
        "today_date": pt_context.get("today_date"),
        "today_weekday": pt_context.get("today_weekday"),
        "today_scheduled_workout": pt_context.get("today_scheduled_workout"),
        "active_plan": pt_context.get("active_plan"),
        "scheduled_workouts": pt_context.get("scheduled_workouts"),
        "injuries": pt_context.get("injuries"),
        "weather_forecast_next_7_days": weather_forecast,
        "garmin_health_last_7_days": garmin_data,
        "strava_activities_last_7_activities": strava_data,
        "withings_measurements_last_7_days": withings_data
    }


async def _build_trainer_context_summary(days: int = 14) -> dict:
    """Builds a comprehensive summary of active training plan, scheduled workouts,
    recent running history (Garmin/Strava), health recovery data, and active injuries."""
    today = datetime.date.today()
    swedish_weekdays = ["Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag", "Lördag", "Söndag"]
    today_weekday_str = swedish_weekdays[today.weekday()]

    result = {
        "status": "success",
        "today_date": today.isoformat(),
        "today_weekday": today_weekday_str,
        "today_scheduled_workout": None,
        "active_plan": None,
        "scheduled_workouts": [],
        "recent_runs": [],
        "health_summary": [],
        "injuries": []
    }

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 1. Fetch active training plan
            cursor.execute('''
                SELECT id, date, goal, advice_text, limitations
                FROM trainer_plans
                ORDER BY id DESC LIMIT 1
            ''')
            plan_row = cursor.fetchone()
            if plan_row:
                plan_id, p_date, p_goal, p_advice, p_limitations = plan_row
                plan_json = {}
                if p_advice:
                    try:
                        cleaned = p_advice.replace("```json", "").replace("```", "").strip()
                        plan_json = json.loads(cleaned)
                    except Exception:
                        plan_json = {"summary": p_advice[:300]}

                result["active_plan"] = {
                    "plan_id": plan_id,
                    "date": p_date,
                    "goal": p_goal,
                    "limitations": p_limitations,
                    "summary": plan_json.get("summary", ""),
                    "weekly_focus": plan_json.get("weekly_focus", ""),
                    "workouts_defined": plan_json.get("workouts", [])
                }

            # 2. Fetch scheduled workouts for current week
            try:
                monday = today - datetime.timedelta(days=today.weekday())
                sunday = monday + datetime.timedelta(days=6)

                cursor.execute('''
                    SELECT b.id, b.plan_id, b.workout_date, b.week, p.advice_text
                    FROM trainer_bookings b
                    JOIN trainer_plans p ON b.plan_id = p.id
                    WHERE b.workout_date >= ? AND b.workout_date <= ?
                    ORDER BY b.workout_date ASC
                ''', (monday.isoformat(), sunday.isoformat()))
                rows = cursor.fetchall()
                day_offsets = {"måndag": 0, "tisdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lördag": 5, "söndag": 6}

                for b_row in rows:
                    b_id, p_id, w_date_str, w_week, advice_text = b_row
                    w_title = "Scheduled Workout"
                    w_desc = ""
                    w_dur = 0
                    try:
                        p_obj = json.loads(advice_text.replace("```json", "").replace("```", "").strip())
                        w_list = p_obj.get("workouts", [])
                        w_date = datetime.datetime.strptime(w_date_str, "%Y-%m-%d").date()
                        w_dow = w_date.weekday()
                        # A multi-week plan can list the same weekday more than once (one
                        # entry per week), so the booking's own `week` must be matched too -
                        # weekday alone always resolved to whichever entry came first in the
                        # array, i.e. always week 0's version of that weekday.
                        for w in w_list:
                            d_name = str(w.get("day", "")).lower()
                            try:
                                entry_week = int(w.get("week", 0) or 0)
                            except (TypeError, ValueError):
                                entry_week = 0
                            if day_offsets.get(d_name) == w_dow and entry_week == (w_week or 0):
                                w_dur = w.get("duration_minutes", 0)
                                w_title = f"{w.get('activity_type', 'Träning')}: {w.get('title', 'Pass')} ({w_dur} min)"
                                w_desc = w.get("description", "")
                                break
                    except Exception:
                        pass

                    w_date_obj = datetime.datetime.strptime(w_date_str, "%Y-%m-%d").date()
                    dow_name = swedish_weekdays[w_date_obj.weekday()]

                    item_info = {
                        "booking_id": b_id,
                        "plan_id": p_id,
                        "workout_date": w_date_str,
                        "weekday": dow_name,
                        "workout_summary": w_title,
                        "description": w_desc,
                        "duration_minutes": w_dur,
                        "is_today": (w_date_str == today.isoformat())
                    }
                    result["scheduled_workouts"].append(item_info)
                    if w_date_str == today.isoformat():
                        result["today_scheduled_workout"] = item_info

                # If no booking exists specifically for today, match today's weekday against active_plan workouts
                if not result["today_scheduled_workout"] and result["active_plan"] and result["active_plan"].get("workouts_defined"):
                    for w in result["active_plan"]["workouts_defined"]:
                        d_name = str(w.get("day", "")).lower()
                        if day_offsets.get(d_name) == today.weekday():
                            w_dur = w.get("duration_minutes", 0)
                            result["today_scheduled_workout"] = {
                                "workout_date": today.isoformat(),
                                "weekday": today_weekday_str,
                                "workout_summary": f"{w.get('activity_type', 'Träning')}: {w.get('title', 'Pass')} ({w_dur} min)",
                                "description": w.get("description", ""),
                                "duration_minutes": w_dur,
                                "is_today": True
                            }
                            break
            except Exception as b_err:
                print(f"[TRAINER CONTEXT] Booking fetch error: {b_err}")

            # 3. Fetch active injuries
            try:
                cursor.execute('''
                    SELECT area, description, severity, date_logged
                    FROM trainer_injury_logs
                    ORDER BY date_logged DESC
                    LIMIT 5
                ''')
                for inj in cursor.fetchall():
                    result["injuries"].append({
                        "area": inj[0],
                        "description": inj[1],
                        "severity": inj[2],
                        "date_logged": inj[3]
                    })
            except Exception:
                pass

            # 4. Fetch recent run history from Strava & Garmin
            try:
                cursor.execute('''
                    SELECT name, type, date, distance, moving_time, average_heartrate, max_heartrate
                    FROM strava_activities
                    ORDER BY date DESC
                    LIMIT 10
                ''')
                for s in cursor.fetchall():
                    dist_km = round(s[3] / 1000.0, 2) if s[3] else 0
                    dur_min = round(s[4] / 60.0, 1) if s[4] else 0
                    result["recent_runs"].append({
                        "source": "Strava",
                        "name": s[0],
                        "type": s[1],
                        "date": s[2],
                        "distance_km": dist_km,
                        "duration_min": dur_min,
                        "avg_hr": s[5],
                        "max_hr": s[6]
                    })
            except Exception:
                pass

            try:
                cursor.execute('''
                    SELECT date, workout_type, workout_duration, resting_hr, body_battery, hrv, sleep_score
                    FROM garmin_health
                    ORDER BY date DESC
                    LIMIT ?
                ''', (days,))
                for g in cursor.fetchall():
                    result["health_summary"].append({
                        "date": g[0],
                        "workout": g[1],
                        "duration_min": g[2],
                        "resting_hr": g[3],
                        "body_battery": g[4],
                        "hrv": g[5],
                        "sleep_score": g[6]
                    })
            except Exception:
                pass
    except Exception as e:
        print(f"[TRAINER CONTEXT ERROR]: {e}")

    return result


@registry.register(
    name="get_trainer_workouts",
    description="Retrieves scheduled PT training sessions, active training plan goal, limitations/injuries, and recent Garmin/Strava running history so Freja can discuss workout rationale and progression with the user.",
    permission_key="freja_tool_get_trainer_workouts_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "days": {
                "type": "INTEGER",
                "description": "Number of days of history/workouts to inspect (default 14)."
            }
        }
    },
)
async def exec_get_trainer_workouts(args):
    days = int((args or {}).get("days", 14) or 14)
    return await _build_trainer_context_summary(days)


@registry.register(
    name="update_trainer_workout",
    description="Updates or adjusts a specific scheduled workout in the user's PT training plan (e.g. changing duration, title, description, or activity type based on coaching decisions).",
    permission_key="freja_tool_update_trainer_workout_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "workout_date": {
                "type": "STRING",
                "description": "Date of the workout to update in YYYY-MM-DD format (e.g. '2026-07-20')."
            },
            "duration_minutes": {
                "type": "INTEGER",
                "description": "New duration in minutes (e.g. 35)."
            },
            "title": {
                "type": "STRING",
                "description": "Updated workout title (e.g. 'Lugnt pass med gå-pauser')."
            },
            "description": {
                "type": "STRING",
                "description": "Updated detailed description and structure of the workout."
            },
            "activity_type": {
                "type": "STRING",
                "description": "Activity type (e.g. 'Löpning', 'Styrketräning', 'Aktiv vila', 'Cykling')."
            }
        },
        "required": ["workout_date"]
    },
)
async def exec_update_trainer_workout(args):
    w_date = str(args.get("workout_date", "")).strip()
    new_dur = args.get("duration_minutes")
    new_title = str(args.get("title", "")).strip()
    new_desc = str(args.get("description", "")).strip()
    new_act = str(args.get("activity_type", "")).strip()

    if not w_date:
        return {"error": "workout_date is required."}

    updated_plan = False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, advice_text FROM trainer_plans
            ORDER BY id DESC LIMIT 1
        ''')
        plan_row = cursor.fetchone()
        if plan_row:
            plan_id, advice_text = plan_row
            try:
                cleaned = advice_text.replace("```json", "").replace("```", "").strip()
                plan_json = json.loads(cleaned)
                workouts = plan_json.get("workouts", [])

                day_offsets = {"måndag": 0, "tisdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lördag": 5, "söndag": 6}
                target_dt = datetime.datetime.strptime(w_date, "%Y-%m-%d").date()
                target_dow = target_dt.weekday()

                # A multi-week plan can repeat the same weekday once per week - matching by
                # weekday alone always resolved to whichever entry came first in the array
                # (week 0's version), silently editing the wrong week's workout. Disambiguate
                # using the actual booking's `week` for this date; falls back to week 0 (the
                # plan's first/template week) if this date was never booked yet.
                cursor.execute(
                    "SELECT week FROM trainer_bookings WHERE plan_id = ? AND workout_date = ?",
                    (plan_id, w_date)
                )
                booking_row = cursor.fetchone()
                target_week = booking_row[0] if booking_row else 0

                for w in workouts:
                    d_name = str(w.get("day", "")).lower()
                    try:
                        entry_week = int(w.get("week", 0) or 0)
                    except (TypeError, ValueError):
                        entry_week = 0
                    if day_offsets.get(d_name) == target_dow and entry_week == (target_week or 0):
                        if new_dur is not None: w["duration_minutes"] = int(new_dur)
                        if new_title: w["title"] = new_title
                        if new_desc: w["description"] = new_desc
                        if new_act: w["activity_type"] = new_act
                        updated_plan = True
                        break

                if updated_plan:
                    cursor.execute('''
                        UPDATE trainer_plans SET advice_text = ? WHERE id = ?
                    ''', (json.dumps(plan_json, ensure_ascii=False, indent=2), plan_id))
                    conn.commit()
            except Exception as p_err:
                print(f"[UPDATE WORKOUT] Plan update error: {p_err}")

    events_updated = 0
    try:
        from backend.routes.google_calendar import core_get_calendar_data, core_save_calendar_event
        events = core_get_calendar_data(14)
        for ev in events:
            if (ev.get("start_time") or "")[:10] == w_date:
                summary = new_title or ev.get("summary")
                if new_act and not summary.startswith("💪"):
                    summary = f"💪 {new_act}: {summary}"
                start_dt = ev.get("start_time")
                end_dt = ev.get("end_time")
                if new_dur and start_dt and len(start_dt) >= 16:
                    s_time = datetime.datetime.strptime(start_dt[:16], "%Y-%m-%dT%H:%M")
                    e_time = s_time + datetime.timedelta(minutes=int(new_dur))
                    end_dt = e_time.strftime("%Y-%m-%dT%H:%M")

                await core_save_calendar_event(
                    summary=summary,
                    start_time=start_dt[:16],
                    end_time=end_dt[:16] if end_dt else start_dt[:16],
                    description=new_desc or ev.get("description", ""),
                    location=ev.get("location", ""),
                    db_id=ev.get("id")
                )
                events_updated += 1
    except Exception as c_err:
        print(f"[UPDATE WORKOUT] Calendar update error: {c_err}")

    return {
        "status": "success",
        "message": f"Workout on {w_date} updated successfully.",
        "plan_updated": updated_plan,
        "events_updated": events_updated
    }
