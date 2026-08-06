import datetime
import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from backend.database import get_db_connection
from backend.models import User
from backend.routes.auth import get_current_user
from backend.services import plan_export
from backend.services.time_utils import today_local
from .shared import (
    _clear_bookings, MAX_WORKOUT_MINUTES, DEFAULT_WORKOUT_HOUR, DAY_END_HOUR,
    WORKOUT_LOCATION_MARKER, _format_exercises_for_calendar,
)

router = APIRouter()


def _find_free_slot(workout_date: datetime.date, duration: int, day_events: list) -> datetime.datetime | None:
    """Finds a start time on workout_date that doesn't overlap existing events."""
    dur = datetime.timedelta(minutes=duration)
    intervals = []
    for e in day_events:
        try:
            s = datetime.datetime.strptime((e.get("start_time") or "")[:16], "%Y-%m-%dT%H:%M")
            en = datetime.datetime.strptime((e.get("end_time") or "")[:16], "%Y-%m-%dT%H:%M")
            intervals.append((s, en))
        except Exception:
            continue
    intervals.sort()

    def _search_window(start_hour: int, end_hour: int) -> datetime.datetime | None:
        start = datetime.datetime.combine(workout_date, datetime.time(start_hour, 0))
        end_limit = datetime.datetime.combine(workout_date, datetime.time(end_hour, 0))
        while start + dur <= end_limit:
            candidate_end = start + dur
            conflict_end = None
            for (s, en) in intervals:
                if start < en and candidate_end > s:
                    conflict_end = en
                    break
            if conflict_end is None:
                return start
            start = conflict_end
        return None

    slot = _search_window(DEFAULT_WORKOUT_HOUR, DAY_END_HOUR)
    if slot is not None:
        return slot

    slot_morning = _search_window(6, DEFAULT_WORKOUT_HOUR)
    if slot_morning is not None:
        return slot_morning

    return None


async def core_book_plan_internal(plan_id: int, start_date: datetime.date, skip_past: bool = True, user_id: int = 1) -> dict:
    """Books a plan's workouts into the calendar, anchored on `start_date` for user_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT advice_text FROM trainer_plans WHERE id = ? AND (user_id = ? OR user_id IS NULL)", (plan_id, user_id))
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

    day_offsets = plan_export.SWEDISH_DAY_OFFSETS

    def _bookable_offset(w) -> int | None:
        off = day_offsets.get(str(w.get("day", "")).lower())
        if off is None:
            return None
        try:
            duration = int(w.get("duration_minutes", 0) or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            return None
        try:
            wk = max(0, min(51, int(w.get("week", 0) or 0)))
        except (TypeError, ValueError):
            wk = 0
        return off + wk * 7

    bookable_offsets = [o for o in (_bookable_offset(w) for w in workouts) if o is not None]
    if not bookable_offsets:
        return {"status": "success", "message": "No bookable workouts in this plan.", "booked_count": 0, "replaced_count": 0}

    from backend.routes.google_calendar import core_save_calendar_event, core_get_calendar_data

    window_start = start_date
    if skip_past and window_start < today_local():
        window_start = today_local()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, event_id FROM trainer_bookings WHERE workout_date >= ? AND (user_id = ? OR user_id IS NULL)",
            (window_start.isoformat(), user_id)
        )
        prior = cursor.fetchall()
    rebooked = await _clear_bookings(prior)

    all_events = core_get_calendar_data(days=60)

    booked_count = 0
    skipped_past = 0
    sync_failed_count = 0
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
            continue
        duration = min(duration, MAX_WORKOUT_MINUTES)

        try:
            week = max(0, min(51, int(w.get("week", 0) or 0)))
        except (TypeError, ValueError):
            week = 0

        workout_date = start_date + datetime.timedelta(days=offset + week * 7)
        if skip_past and workout_date < today_local():
            skipped_past += 1
            continue

        day_events = [e for e in all_events if (e.get("start_time") or "")[:10] == workout_date.isoformat()]
        slot_start = _find_free_slot(workout_date, duration, day_events)
        if slot_start is None:
            print(f"[TRAINER BOOKING] No free slot found on {workout_date} for {duration}m session; skipping to avoid double-booking.")
            sync_failed_count += 1
            continue

        slot_end = slot_start + datetime.timedelta(minutes=duration)
        start_dt = slot_start.strftime("%Y-%m-%dT%H:%M")
        end_dt = slot_end.strftime("%Y-%m-%dT%H:%M")

        summary = f"💪 {w.get('activity_type') or 'Träning'}: {w.get('title') or 'Pass'}"
        exercises_block = _format_exercises_for_calendar(w.get("exercises"))
        description = (
            f"Träningspass genererat av COACH AI.\n\nBeskrivning:\n{w.get('description') or ''}"
            f"{exercises_block}\n\nTid: {duration} minuter."
        )
        location = WORKOUT_LOCATION_MARKER

        try:
            result = await core_save_calendar_event(
                summary=summary,
                start_time=start_dt,
                end_time=end_dt,
                description=description,
                location=location
            )
        except Exception as save_err:
            print(f"[TRAINER BOOK] Could not sync the session on {workout_date} to the calendar: {save_err}")
            sync_failed_count += 1
            continue
        event_id = (result.get("event") or {}).get("id")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO trainer_bookings (user_id, plan_id, event_id, workout_date, week) VALUES (?, ?, ?, ?, ?)",
                (user_id, plan_id, event_id, workout_date.isoformat(), week)
            )
            conn.commit()

        all_events.append({"start_time": f"{start_dt}:00", "end_time": f"{end_dt}:00"})
        booked_count += 1

    msg = f"Successfully booked {booked_count} workouts into your calendar."
    if rebooked:
        msg += f" ({rebooked} previously booked sessions were replaced.)"
    if skipped_past:
        msg += f" ({skipped_past} sessions fell before today and were skipped.)"
    if sync_failed_count:
        msg += f" ({sync_failed_count} sessions could not be synced to the calendar and were skipped.)"
    return {
        "status": "success",
        "message": msg,
        "booked_count": booked_count,
        "replaced_count": rebooked,
        "skipped_past_count": skipped_past,
        "sync_failed_count": sync_failed_count,
    }


@router.post("/api/trainer/plans/book")
async def book_trainer_plan(request: Request, current_user: User = Depends(get_current_user)):
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

        return await core_book_plan_internal(plan_id, start_date, skip_past=False, user_id=current_user.id)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
