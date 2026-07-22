"""Books a generated plan's workouts into the calendar (replace, don't stack)."""

import datetime
import json
from fastapi import APIRouter, HTTPException, Query, Request
from backend.database import get_db_connection
from backend.services import plan_export
from backend.services.time_utils import today_local
from .shared import (
    _clear_bookings, MAX_WORKOUT_MINUTES, DEFAULT_WORKOUT_HOUR, DAY_END_HOUR,
    WORKOUT_LOCATION_MARKER, _format_exercises_for_calendar,
)

router = APIRouter()


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

    def _bookable_offset(w) -> int | None:
        """The day offset for `w`, or None if it has no valid day or is a rest day."""
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
        # Nothing in this plan can actually be booked (unparseable day names, or every entry
        # is a rest day). Bail out before touching any existing booking - a plan that books
        # nothing must not delete something that was already booked (issue #63).
        return {"status": "success", "message": "No bookable workouts in this plan.", "booked_count": 0, "replaced_count": 0}

    from backend.routes.google_calendar import core_save_calendar_event, core_get_calendar_data

    # --- Replace, don't stack: remove every PT session already booked from window_start
    #     onward, regardless of which plan created it or how far into the future it runs.
    #     Booking a *different* (or shorter) plan onto overlapping dates used to only clear
    #     bookings within the new plan's own span, so a shorter replacement plan left the old
    #     plan's later weeks dangling (issue #61) - there is no upper bound here on purpose.
    #     Only future days are cleared when skip_past is set, so completed/past sessions stay
    #     as history. Only PT bookings (this table) and their events are touched - the user's
    #     own calendar entries are never removed. ---
    window_start = start_date
    if skip_past and window_start < today_local():
        window_start = today_local()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, event_id FROM trainer_bookings WHERE workout_date >= ?",
            (window_start.isoformat(),)
        )
        prior = cursor.fetchall()
    rebooked = await _clear_bookings(prior)

    # Existing calendar events used for conflict avoidance (mutated as we book).
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

        # A failed sync must not leave a "booked" row with nothing on the real calendar
        # (issue #59) - skip this workout and keep going rather than aborting the whole
        # plan and losing sessions that would have booked fine (issue #62).
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
