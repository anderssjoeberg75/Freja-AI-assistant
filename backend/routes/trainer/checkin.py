"""The daily check-in briefing: syncs health sources, then asks Gemini for coaching text."""

import asyncio
import datetime
import httpx
import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from backend.database import get_db_connection, get_api_key
from backend.services import llm_client
from backend.services.time_utils import today_local
from .shared import (
    get_trainer_profile, fetch_7day_weather_forecast, calculate_trends, format_trends_summary,
    compute_adherence, format_active_injuries, is_workout_event, _event_duration_minutes,
    GEMINI_TIMEOUT_SECONDS, RHR_ALERT_PCT, HRV_ALERT_PCT, DEFAULT_LOCATION, MAX_WORKOUT_MINUTES,
)

from backend.models import User
from backend.routes.auth import get_current_user

router = APIRouter()


CHECKIN_SYNC_DAYS = 3
CHECKIN_SYNC_TIMEOUT_SECONDS = 90.0
FEEDBACK_ONLY_SYNC_STATUS = "skipped (feedback only, no sync)"


async def _sync_garmin_for_checkin(days: int) -> str:
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


async def _build_daily_checkin_briefing(location: str, user_id: int = 1) -> dict:
    """Reads stored data for user_id and asks LLM for coaching briefing."""
    today = today_local()
    today_str = today.strftime('%Y-%m-%d')
    yesterday_str = (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d')

    garmin_snapshot = "No Garmin data available."
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, sleep_score, training_readiness, training_readiness_level, training_readiness_feedback
            FROM garmin_health
            WHERE user_id = ? OR user_id IS NULL
            ORDER BY date DESC
            LIMIT 1
        ''', (user_id,))
        g = cursor.fetchone()
    if g:
        readiness_prefix = ""
        if g[16] is not None:
            readiness_prefix = f"Training Readiness: {g[16]}/100 ({g[17]})" + (f" - \"{g[18]}\". " if g[18] else ". ")
        garmin_snapshot = (
            f"{readiness_prefix}Date: {g[0]}, Steps: {g[1]}, Sleep: {g[2]}h (Deep: {g[11]}h, REM: {g[13]}h, Light: {g[12]}h, Awake: {g[14]}h, Score: {g[15]}), Resting HR: {g[3]}, Calories: {g[4]}kcal, "
            f"Workout: {g[5]} ({g[6]} min), Body Battery: {g[7]}, HRV: {g[8]}ms, "
            f"Recovery time: {g[9]}h, Status: {g[10]}"
        )

    withings_snapshot = "No Withings data available."
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT date, weight, fat_ratio, bone_mass, heart_pulse, sleep_duration, steps, calories, sleep_score
            FROM withings_measurements
            WHERE user_id = ? OR user_id IS NULL
            ORDER BY date DESC
            LIMIT 1
        ''', (user_id,))
        w = cursor.fetchone()
    if w:
        sleep_h = round(w[5] / 3600.0, 1) if w[5] else 0
        withings_snapshot = (
            f"Date: {w[0]}, Weight: {w[1]} kg, Body fat: {w[2]}%, Pulse: {w[4]} BPM, "
            f"Sleep: {sleep_h}h (Score: {w[8]}), Steps: {w[6]}, Calories: {w[7]}kcal"
        )

    completed_summary = "No workout was recorded yesterday."
    from .shared import unified_sessions
    yesterday_sessions = [
        s for s in unified_sessions(yesterday_str, yesterday_str, user_id=user_id)
        if (s.get("duration_minutes") or 0) > 0
    ]
    if yesterday_sessions:
        parts = []
        for s in yesterday_sessions:
            dist_km = s.get("distance_km") or 0
            dur_min = s.get("duration_minutes") or 0
            parts.append(
                f"{s.get('type') or 'Träning'} ({s['source']}, {dist_km} km, {dur_min} min, "
                f"avg HR {s.get('avg_hr')})"
            )
        completed_summary = "Completed yesterday: " + "; ".join(parts)

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

    trends = calculate_trends(user_id=user_id)
    trends_data_str = format_trends_summary(trends)

    adherence = compute_adherence(14, user_id=user_id)
    if adherence["adherence_pct"] is not None:
        adherence_str = (
            f"Last {adherence['window_days']} days: {adherence['completed']} of "
            f"{adherence['planned']} booked sessions completed ({adherence['adherence_pct']}%)."
        )
    elif adherence.get("reliable") is False:
        adherence_str = (
            "Adherence could not be determined this time "
            f"({adherence.get('reason', 'sync data unavailable')}) - do not assume the "
            "user skipped sessions; ask instead if it matters for this check-in."
        )
    else:
        adherence_str = "No booked session history to compare against yet."

    weather_forecast = await fetch_7day_weather_forecast(location)

    prompt_content = f"""
You are F.R.E.J.A.'s personal trainer (COACH AI), giving the user their coaching briefing for today.
Give a SHORT, warm and practical briefing following the coach model. Do not dump the raw data -
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
{format_active_injuries(user_id=user_id)}

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
- If the injury/pain log has active entries, take them into account for today's session.
- Assess recovery and adjust recommendations accordingly.
- The 'briefing' field must be finished Swedish markdown.
"""

    schema = {
        "type": "OBJECT",
        "properties": {
            "sleep_summary": {"type": "STRING", "description": "Short summary of last night's sleep, in Swedish."},
            "recovery_summary": {"type": "STRING", "description": "Assessment of resting HR, HRV and Body Battery/recovery, in Swedish."},
            "yesterday_status": {"type": "STRING", "description": "Whether yesterday's session was completed or missed, without blame. In Swedish."},
            "todays_plan": {"type": "STRING", "description": "Today's session in concrete practical terms."},
            "recommendation": {"type": "STRING", "description": "The coach's recommendation."},
            "adjust_workout": {"type": "BOOLEAN", "description": "true if today's session should be adjusted."},
            "adjusted_duration_minutes": {"type": "INTEGER", "description": "New length in minutes."},
            "health_tip": {"type": "STRING", "description": "ONE concrete, actionable health tip for today."},
            "weather_note": {"type": "STRING", "description": "Short weather comment."},
            "week_outlook": {"type": "STRING", "description": "Short 1-2 sentence outlook for the week."},
            "closing_question": {"type": "STRING", "description": "Closing question."},
            "briefing": {"type": "STRING", "description": "Finished short briefing in markdown."}
        },
        "required": ["sleep_summary", "recovery_summary", "yesterday_status", "todays_plan", "recommendation", "adjust_workout", "health_tip", "closing_question", "briefing"]
    }
    briefing_data = await llm_client.generate_json(
        prompt_content, schema, temperature=0.3, max_tokens=2500, timeout=GEMINI_TIMEOUT_SECONDS
    )
    active_provider = llm_client.get_active_provider()

    return {
        "today": today,
        "today_str": today_str,
        "briefing_data": briefing_data,
        "active_provider": active_provider,
        "workout_events": workout_events,
        "yesterday_sessions": yesterday_sessions,
        "adherence": adherence,
    }


def _resolve_checkin_location(body: dict, user_id: int = 1) -> str:
    profile = get_trainer_profile(user_id=user_id)
    location = (body.get("location") or profile.get("location") or DEFAULT_LOCATION)
    return str(location).strip() or DEFAULT_LOCATION


@router.post("/api/trainer/checkin")
async def trainer_daily_checkin(request: Request, current_user: User = Depends(get_current_user)):
    """Daily morning check-in (COACH AI)."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        location = _resolve_checkin_location(body, user_id=current_user.id)

        sync_results = await refresh_health_sources_for_checkin()
        print(f"[TRAINER CHECKIN] Pre-check-in sync: {sync_results}")

        built = await _build_daily_checkin_briefing(location, user_id=current_user.id)
        today = built["today"]
        today_str = built["today_str"]
        briefing_data = built["briefing_data"]
        active_provider = built["active_provider"]
        workout_events = built["workout_events"]
        yesterday_sessions = built["yesterday_sessions"]
        adherence = built["adherence"]

        calendar_updated = False
        profile = get_trainer_profile(user_id=current_user.id)
        auto_adjust = bool(profile.get("auto_adjust", True)) if profile.get("auto_adjust") is not None else True
        if briefing_data.get("adjust_workout") and workout_events and auto_adjust:
            try:
                new_dur = int(briefing_data.get("adjusted_duration_minutes") or 0)
            except (TypeError, ValueError):
                new_dur = 0
            if 0 < new_dur <= MAX_WORKOUT_MINUTES:
                ev = workout_events[0]
                start_time = (ev.get("start_time") or "")[:16]
                try:
                    start_dt = datetime.datetime.strptime(start_time, "%Y-%m-%dT%H:%M")
                    end_time = (start_dt + datetime.timedelta(minutes=new_dur)).strftime("%Y-%m-%dT%H:%M")
                    from backend.routes.google_calendar import core_save_calendar_event
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
            "workout_completed_yesterday": bool(yesterday_sessions),
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


@router.post("/api/trainer/feedback")
async def trainer_feedback_only(request: Request, current_user: User = Depends(get_current_user)):
    """Read-only coaching feedback (COACH AI)."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        location = _resolve_checkin_location(body, user_id=current_user.id)

        built = await _build_daily_checkin_briefing(location, user_id=current_user.id)

        return {
            "status": "success",
            "date": built["today_str"],
            "checkin": built["briefing_data"],
            "provider": built["active_provider"],
            "has_workout_today": bool(built["workout_events"]),
            "workout_completed_yesterday": bool(built["yesterday_sessions"]),
            "adherence": built["adherence"],
            "calendar_updated": False,
            "sync": {
                "garmin": FEEDBACK_ONLY_SYNC_STATUS,
                "strava": FEEDBACK_ONLY_SYNC_STATUS,
                "withings": FEEDBACK_ONLY_SYNC_STATUS,
            },
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
