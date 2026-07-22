"""Trainer plan CRUD, the booked-workouts list, chat context and plan export routes."""

import datetime
import json
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from backend.database import get_db_connection
from backend.services import plan_export
from backend.services.time_utils import today_local
from .shared import (
    get_trainer_profile, _clear_bookings, MAX_WORKOUT_MINUTES, WORKOUT_LOCATION_MARKER,
    _format_exercises_for_calendar, TRAINING_LOAD_DAYS, build_training_load_summary,
    format_active_injuries,
)

router = APIRouter()


@router.get("/api/trainer/plans")
async def get_trainer_plans(limit: int = Query(20, description="Number of plans to retrieve")):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, date, goal, advice_text, limitations 
                FROM trainer_plans 
                ORDER BY date DESC, id DESC
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
        
        results = []
        for row in rows:
            results.append({
                'id': row[0],
                'date': row[1],
                'goal': row[2],
                'advice_text': row[3],
                'limitations': row[4]
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/trainer/plans")
async def delete_trainer_plan(plan_id: int = Query(..., description="ID of the plan to delete")):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, event_id FROM trainer_bookings WHERE plan_id = ?", (plan_id,))
            bookings = cursor.fetchall()

        # Clear this plan's booked sessions and their calendar events before deleting the
        # plan row - otherwise the bookings become invisible orphans that still occupy the
        # user's calendar with no way to see or manage them (issue #60).
        await _clear_bookings(bookings)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM trainer_plans WHERE id = ?', (plan_id,))
            conn.commit()
        return {'status': 'success', 'message': f"The training plan with ID {plan_id} has been deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _workout_end_time(date_str: str, duration_minutes: int) -> str:
    """End timestamp for a listed workout, derived from its duration (default start 08:00)."""
    try:
        minutes = max(1, min(int(duration_minutes or 0), MAX_WORKOUT_MINUTES))
    except (TypeError, ValueError):
        minutes = 60
    start = datetime.datetime.strptime(f"{date_str}T08:00", "%Y-%m-%dT%H:%M")
    return (start + datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


@router.get("/api/trainer/workouts")
async def get_trainer_workouts(days: int = Query(14, description="Lookback/lookahead window in days")):
    """Returns scheduled PT workouts directly from local SQLite database (trainer_bookings or latest trainer_plan).
    Works independently of Google Calendar."""
    try:
        today = today_local()
        current_dow = today.weekday() # 0 = Monday
        monday = today - datetime.timedelta(days=current_dow)
        days = max(1, min(int(days or 14), 365))
        window_start = today - datetime.timedelta(days=days)
        window_end = today + datetime.timedelta(days=days)

        results = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # `days` bounds the window in both directions. It used to be accepted and then
            # ignored, so the list grew without limit and every plan ever booked came back.
            cursor.execute('''
                SELECT b.id, b.plan_id, b.event_id, b.workout_date, b.week, p.advice_text
                FROM trainer_bookings b
                JOIN trainer_plans p ON b.plan_id = p.id
                WHERE b.workout_date >= ? AND b.workout_date <= ?
                ORDER BY b.workout_date ASC
            ''', (window_start.isoformat(), window_end.isoformat()))
            rows = cursor.fetchall()

        day_offsets = plan_export.SWEDISH_DAY_OFFSETS

        for row in rows:
            booking_id, plan_id, event_id, w_date_str, week_num, advice_text = row
            try:
                plan_data = json.loads(advice_text) if advice_text else {}
                workouts = plan_data.get("workouts", [])
                w_date = datetime.datetime.strptime(w_date_str, "%Y-%m-%d").date()

                matching_w = None
                for w in workouts:
                    day_name = str(w.get("day", "")).lower()
                    offset = day_offsets.get(day_name)
                    if offset is not None:
                        w_day_num = w_date.weekday()
                        if offset == w_day_num:
                            matching_w = w
                            break
                if not matching_w and workouts:
                    matching_w = workouts[0]

                if matching_w:
                    duration = int(matching_w.get("duration_minutes", 0) or 0)
                    summary = f"💪 {matching_w.get('activity_type', 'Träning')}: {matching_w.get('title', 'Pass')}"
                    exercises_block = _format_exercises_for_calendar(matching_w.get("exercises"))
                    description = (
                        f"Träningspass genererat av COACH AI.\n\nBeskrivning:\n{matching_w.get('description', '')}"
                        f"{exercises_block}\n\nTid: {duration} minuter."
                    )
                    results.append({
                        "id": booking_id,
                        "plan_id": plan_id,
                        "summary": summary,
                        "description": description,
                        "start_time": f"{w_date_str}T08:00:00",
                        # Derived from the session's own duration. A fixed 09:00 made every
                        # card read "(60 min)" regardless of what the plan actually said.
                        "end_time": _workout_end_time(w_date_str, duration),
                        "location": WORKOUT_LOCATION_MARKER,
                        "duration_minutes": duration,
                        "activity_type": matching_w.get("activity_type", "Träning"),
                        "title": matching_w.get("title", "Pass"),
                        "exercises": matching_w.get("exercises", [])
                    })
            except Exception as parse_err:
                print(f"[TRAINER WORKOUTS] Error parsing booking {booking_id}: {parse_err}")

        # Fallback to the most recent plan in trainer_plans if trainer_bookings is empty
        if not results:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, advice_text, date FROM trainer_plans
                    ORDER BY id DESC LIMIT 1
                ''')
                row = cursor.fetchone()

            if row:
                plan_id, advice_text, p_date_str = row
                try:
                    plan_data = json.loads(advice_text) if advice_text else {}
                    workouts = plan_data.get("workouts", [])
                    for w in workouts:
                        day_name = str(w.get("day", "")).lower()
                        offset = day_offsets.get(day_name)
                        if offset is None:
                            continue
                        duration = int(w.get("duration_minutes", 0) or 0)
                        if duration <= 0:
                            continue
                        w_date = monday + datetime.timedelta(days=offset)
                        w_date_str = w_date.isoformat()
                        summary = f"💪 {w.get('activity_type', 'Träning')}: {w.get('title', 'Pass')}"
                        exercises_block = _format_exercises_for_calendar(w.get("exercises"))
                        description = (
                            f"Träningspass genererat av COACH AI.\n\nBeskrivning:\n{w.get('description', '')}"
                            f"{exercises_block}\n\nTid: {duration} minuter."
                        )
                        results.append({
                            "id": f"plan_{plan_id}_{offset}",
                            "plan_id": plan_id,
                            "summary": summary,
                            "description": description,
                            "start_time": f"{w_date_str}T08:00:00",
                            "end_time": _workout_end_time(w_date_str, duration),
                            "location": WORKOUT_LOCATION_MARKER,
                            "duration_minutes": duration,
                            "activity_type": w.get("activity_type", "Träning"),
                            "title": w.get("title", "Pass"),
                            "exercises": w.get("exercises", [])
                        })
                except Exception as fb_err:
                    print(f"[TRAINER WORKOUTS] Fallback parse error: {fb_err}")

        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Chat context ------------------------------------------------------------
# Freja is the user's coach in ordinary conversation too, not only when a tool fires. The
# block below is injected into her system prompt on every turn so "hur ser dagens pass ut"
# is answered from the actual plan even if the model never decides to call a tool - which is
# what used to make her improvise a walk that was nowhere in the schedule.


SWEDISH_WEEKDAYS = ["Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag", "Lördag", "Söndag"]
CHAT_CONTEXT_MAX_WORKOUTS = 14   # Sessions listed before the block is truncated
CHAT_CONTEXT_DESC_LEN = 400      # Per-session description budget


def build_chat_context_block() -> str:
    """Renders the active plan, this week's schedule and today's session as prompt text.

    Swedish, because it is quoted more or less verbatim back to the user; everything else in
    this file is English. Returns "" when there is nothing to say, so the caller can skip the
    injection entirely rather than pasting an empty header."""
    profile = get_trainer_profile()
    today = today_local()
    today_str = today.isoformat()

    plan = None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT id, date, goal, advice_text, limitations FROM trainer_plans ORDER BY id DESC LIMIT 1"
            )
            plan = cursor.fetchone()
        except Exception as e:
            print(f"[TRAINER CONTEXT] Could not read the active plan: {e}")

    lines = [f"Dagens datum: {today_str} ({SWEDISH_WEEKDAYS[today.weekday()]})."]

    if profile:
        prof_bits = []
        for label, key in (("Mål", "goals"), ("Nivå", "fitness_level"), ("Tillgänglighet", "availability"),
                           ("Tävling/mål-event", "event"), ("Eventdatum", "event_date"),
                           ("Begränsningar", "limitations")):
            val = str(profile.get(key) or "").strip()
            if val:
                prof_bits.append(f"{label}: {val}")
        if prof_bits:
            lines.append("Träningsprofil - " + "; ".join(prof_bits) + ".")

    plan_data = {}
    if plan:
        plan_id, plan_date, goal, advice_text, limitations = plan
        try:
            plan_data = json.loads(str(advice_text or "").replace("```json", "").replace("```", "").strip())
        except Exception:
            plan_data = {}
        lines.append(f"Aktivt träningsprogram (skapat {plan_date}, mål: \"{goal}\").")
        if plan_data.get("weekly_focus"):
            lines.append(f"Veckans fokus: {plan_data['weekly_focus']}")
        if plan_data.get("summary"):
            lines.append(f"Coachens analys: {str(plan_data['summary'])[:CHAT_CONTEXT_DESC_LEN]}")

    # Booked sessions for the current week, newest plan first.
    workouts = []
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''SELECT b.workout_date, p.advice_text
                   FROM trainer_bookings b JOIN trainer_plans p ON b.plan_id = p.id
                   WHERE b.workout_date >= ? AND b.workout_date <= ?
                   ORDER BY b.workout_date ASC''',
                (monday.isoformat(), sunday.isoformat())
            )
            booking_rows = cursor.fetchall()
        except Exception as e:
            print(f"[TRAINER CONTEXT] Could not read bookings: {e}")
            booking_rows = []

    day_offsets = plan_export.SWEDISH_DAY_OFFSETS
    for w_date_str, advice_text in booking_rows:
        try:
            w_date = datetime.datetime.strptime(w_date_str, "%Y-%m-%d").date()
            booked_plan = json.loads(str(advice_text or "").replace("```json", "").replace("```", "").strip())
        except Exception:
            continue
        for w in booked_plan.get("workouts", []):
            if day_offsets.get(str(w.get("day", "")).lower()) == w_date.weekday():
                workouts.append((w_date, w))
                break

    # No bookings (e.g. calendar never connected): fall back to the plan's own weekdays,
    # mapped onto the current week, so the schedule is still known in conversation.
    if not workouts and plan_data.get("workouts"):
        for w in plan_data["workouts"]:
            offset = day_offsets.get(str(w.get("day", "")).lower())
            if offset is not None:
                workouts.append((monday + datetime.timedelta(days=offset), w))
        workouts.sort(key=lambda item: item[0])

    if workouts:
        lines.append("Inbokade träningspass denna vecka:")
        for w_date, w in workouts[:CHAT_CONTEXT_MAX_WORKOUTS]:
            marker = " <-- IDAG" if w_date == today else ""
            dur = w.get("duration_minutes", 0)
            desc = str(w.get("description", "") or "").strip()[:CHAT_CONTEXT_DESC_LEN]
            exercises = w.get("exercises") or []
            ex_str = ""
            if isinstance(exercises, list) and exercises:
                ex_str = " Övningar: " + "; ".join(
                    f"{e.get('name', 'Övning')} {e.get('sets', 0)}x{e.get('reps', 0)}"
                    + (f" @ {e.get('target_weight')} kg" if e.get("target_weight") else "")
                    for e in exercises[:8]
                )
            lines.append(
                f"- {w_date.isoformat()} ({SWEDISH_WEEKDAYS[w_date.weekday()]}): "
                f"{w.get('activity_type', 'Träning')} - {w.get('title', 'Pass')}, {dur} min.{marker}"
                f" {desc}{ex_str}".rstrip()
            )
        if not any(w_date == today for w_date, _ in workouts):
            lines.append(f"Inget pass är inbokat idag ({today_str}) - det är en vilodag enligt programmet.")
    else:
        lines.append("Inga inbokade träningspass hittades för den här veckan.")

    injuries = format_active_injuries()
    if injuries and not injuries.startswith("No active"):
        lines.append("Aktiva skador/besvär (senast loggade):\n" + injuries)

    load = build_training_load_summary(TRAINING_LOAD_DAYS)
    if load.get("session_count"):
        lines.append(
            f"Faktiskt genomförd träning senaste {load['window_days']} dagarna: {load['session_count']} pass, "
            f"i snitt {load['avg_weekly_minutes']} min/vecka, längsta pass {load['longest_session_minutes']} min."
        )

    return "\n".join(lines) if len(lines) > 1 else ""


@router.get("/api/trainer/context")
async def get_trainer_chat_context():
    """The PT context block injected into Freja's system prompt on every chat turn."""
    try:
        block = build_chat_context_block()
        return {"status": "success", "has_context": bool(block), "context": block}
    except Exception as e:
        # Never fail the chat over this: an empty context degrades to tool-call behaviour.
        print(f"[TRAINER CONTEXT] Build error: {e}")
        return {"status": "error", "has_context": False, "context": "", "detail": str(e)}


def _next_monday(from_date: datetime.date = None) -> datetime.date:
    """The next upcoming Monday - the default start date for an exported plan."""
    d = from_date or today_local()
    return d + datetime.timedelta(days=(7 - d.weekday()) or 7)


def _current_week_monday(from_date: datetime.date = None) -> datetime.date:
    """The Monday of the week `from_date` falls in.

    A plan's `day` field is a Swedish weekday name that book_plan_to_calendar turns into an
    absolute offset from the start date (Måndag = 0). The start date must therefore BE a
    Monday, or every session lands on the wrong weekday - generating a plan on a Wednesday
    used to book "Måndag" on Wednesday, "Tisdag" on Thursday and so on."""
    d = from_date or today_local()
    return d - datetime.timedelta(days=d.weekday())


# Swedish (and common accented) letters folded to ASCII so a filename built from a goal
# like "Träna inför Göteborgsvarvet" stays readable instead of becoming a row of dashes.
_FILENAME_TRANSLITERATIONS = str.maketrans({
    "å": "a", "ä": "a", "ö": "o", "é": "e", "è": "e", "ü": "u", "ø": "o", "æ": "ae",
})


def _safe_filename(text: str, fallback: str = "traningsplan") -> str:
    """ASCII-safe slug for a Content-Disposition filename.

    Must be strictly ASCII: header values are latin-1 encoded on the way out, so a goal
    containing Cyrillic or CJK would raise mid-response, and even latin-1-representable
    Swedish letters come back mojibake. `str.isalnum()` is Unicode-aware and would happily
    keep all of those, so the ASCII check below is what actually does the filtering."""
    folded = str(text or "").lower().translate(_FILENAME_TRANSLITERATIONS)
    slug = "".join(c if (c.isascii() and c.isalnum()) else "-" for c in folded)
    slug = "-".join(part for part in slug.split("-") if part)[:60]
    return slug or fallback


@router.get("/api/trainer/plans/export")
async def export_trainer_plan(
    plan_id: int = Query(..., description="ID of the plan to export"),
    format: str = Query("ics", description="Export format: 'ics' or 'pdf'"),
    start_date: str = Query(None, description="Date the plan's week starts (YYYY-MM-DD, default: next Monday)"),
):
    """Exports a saved plan as a calendar file or a printable PDF (Issue #39).

    The plan itself only names weekdays, so `start_date` is what turns it into dated
    sessions - it defaults to the next Monday, matching the booking widget's default."""
    fmt = (format or "ics").strip().lower()
    if fmt not in ("ics", "pdf"):
        raise HTTPException(status_code=400, detail="Unsupported format (use 'ics' or 'pdf').")

    if start_date:
        try:
            start = datetime.datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start date format (use YYYY-MM-DD).")
    else:
        start = _next_monday()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, date, goal, advice_text, limitations FROM trainer_plans WHERE id = ?", (plan_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="The training plan was not found.")

    plan = {"id": row[0], "date": row[1], "goal": row[2], "advice_text": row[3], "limitations": row[4]}
    plan_data = plan_export.parse_plan_text(plan["advice_text"])
    base_name = f"freja-{_safe_filename(plan['goal'])}-{plan['date'] or start.isoformat()}"

    try:
        if fmt == "ics":
            if not plan_data:
                raise HTTPException(
                    status_code=400,
                    detail="This training plan has no structured data and cannot be exported to a calendar."
                )
            body = plan_export.build_ics(plan, plan_data, start).encode("utf-8")
            media_type = "text/calendar; charset=utf-8"
            filename = f"{base_name}.ics"
        else:
            # A plan without structured JSON still exports as a PDF of its raw advice text.
            body = plan_export.build_pdf(plan, plan_data, start)
            media_type = "application/pdf"
            filename = f"{base_name}.pdf"
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not build the export: {e}")

    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/api/trainer/plans")
async def update_trainer_plan(request: Request):
    try:
        body = await request.json()
        plan_id = body.get("plan_id")
        advice_text = body.get("advice_text")
        if not plan_id or advice_text is None:
            raise HTTPException(status_code=400, detail="A plan ID and the updated training plan are required.")
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE trainer_plans 
                SET advice_text = ? 
                WHERE id = ?
            ''', (advice_text, plan_id))
            conn.commit()
            
        return {"status": "success", "message": "The training plan was updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Shared workout-event helpers -------------------------------------------
# Markers that identify an auto-scheduled F.R.E.J.A. PT session in the calendar,
# so recovery-driven adjustments only ever touch training events (never meetings).
