"""The daily check-in briefing: syncs health sources, then asks Gemini for coaching text."""

import asyncio
import datetime
import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from backend.database import get_db_connection, get_api_key
from backend.services import llm_client
from backend.services.time_utils import today_local
from .shared import (
    get_trainer_profile, fetch_7day_weather_forecast, calculate_trends, format_trends_summary,
    compute_adherence, format_active_injuries, is_workout_event, _event_duration_minutes,
    GEMINI_TIMEOUT_SECONDS, RHR_ALERT_PCT, HRV_ALERT_PCT, DEFAULT_LOCATION, MAX_WORKOUT_MINUTES,
)

router = APIRouter()


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
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, sleep_score, training_readiness, training_readiness_level, training_readiness_feedback
                FROM garmin_health
                ORDER BY date DESC
                LIMIT 1
            ''')
            g = cursor.fetchone()
        if g:
            # Training Readiness (#180) leads the snapshot - it's the calibrated answer to
            # exactly the question this check-in exists to ask ("how hard should the user go
            # today"), combining sleep/HRV/load/stress from the user's own baselines.
            readiness_prefix = ""
            if g[16] is not None:
                readiness_prefix = f"Training Readiness: {g[16]}/100 ({g[17]})" + (f" - \"{g[18]}\". " if g[18] else ". ")
            garmin_snapshot = (
                f"{readiness_prefix}Date: {g[0]}, Steps: {g[1]}, Sleep: {g[2]}h (Deep: {g[11]}h, REM: {g[13]}h, Light: {g[12]}h, Awake: {g[14]}h, Score: {g[15]}), Resting HR: {g[3]}, Calories: {g[4]}kcal, "
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
        elif adherence.get("reliable") is False:
            # A broken/stale sync must read as "unknown", not silently omitted - stating
            # nothing here is what used to let a 0% figure stand in for it instead (#187).
            adherence_str = (
                "Adherence could not be determined this time "
                f"({adherence.get('reason', 'sync data unavailable')}) - do not assume the "
                "user skipped sessions; ask instead if it matters for this check-in."
            )
        else:
            adherence_str = "No booked session history to compare against yet."

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

        # 9. Call the LLM with a structured schema
        # All STRING fields are shown to the user as-is, so the model fills them in Swedish.
        schema = {
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
        briefing_data = await llm_client.generate_json(
            prompt_content, schema, temperature=0.3, max_tokens=1500, timeout=GEMINI_TIMEOUT_SECONDS
        )
        # Which provider actually served this briefing (Ollama first, Gemini fallback),
        # surfaced so the client can show an "active provider" indicator.
        active_provider = llm_client.get_active_provider()

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
            "provider": active_provider,
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

