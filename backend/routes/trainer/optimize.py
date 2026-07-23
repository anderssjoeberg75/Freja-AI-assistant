"""Reviews already-booked workouts against recovery data and adjusts them."""

import datetime
import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from backend.database import get_db_connection
from backend.services import llm_client
from backend.services.time_utils import today_local
from .shared import (
    get_trainer_profile, calculate_trends, format_trends_summary, format_active_injuries,
    is_workout_event, _event_duration_minutes, MAX_WORKOUT_MINUTES, DAY_END_HOUR,
    GEMINI_TIMEOUT_SECONDS, RHR_ALERT_PCT, HRV_ALERT_PCT, MAX_INPUT_LEN,
)

router = APIRouter()


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

    schema = {
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

    try:
        opt_data = await llm_client.generate_json(
            prompt_content, schema, temperature=0.2, max_tokens=1500, timeout=GEMINI_TIMEOUT_SECONDS
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not generate the optimization: {e}")

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


