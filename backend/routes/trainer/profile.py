"""Trainer profile, adherence, baselines, strength log, injuries and trend routes."""

import datetime
from fastapi import APIRouter, HTTPException, Query, Request
from backend.database import get_db_connection
from backend.services.time_utils import today_local
from .shared import (
    get_trainer_profile, calculate_trends, format_trends_summary, recompute_health_baselines,
    get_recent_strength_logs, format_recent_strength_logs, get_injury_logs,
    format_active_injuries, compute_adherence, build_training_load_summary,
    format_training_load_summary, get_health_series, MAX_TREND_DAYS, MAX_INPUT_LEN,
    RHR_ALERT_PCT, HRV_ALERT_PCT,
)

router = APIRouter()


@router.get("/api/trainer/trends")
async def get_trainer_trends(days: int = Query(28, description="Length of the trend window in days")):
    """Everything the PT panel's trend & adherence charts need, in one request (Issue #36).

    Bundles the plotted series, the recent-vs-baseline aggregates already used in the
    coach prompts, the profile's stored baselines (drawn as reference lines) and the
    adherence figures, so the panel renders from a single round trip."""
    try:
        days = max(1, min(int(days or 28), MAX_TREND_DAYS))
        profile = get_trainer_profile()
        return {
            "window_days": days,
            "series": get_health_series(days),
            "trends": calculate_trends(),
            "baselines": {
                "resting_hr": profile.get("baseline_resting_hr"),
                "hrv": profile.get("baseline_hrv"),
                "sleep_hours": profile.get("baseline_sleep_hours"),
                "updated_at": profile.get("baselines_updated_at"),
            },
            "adherence": compute_adherence(days),
            "alert_thresholds": {"rhr_pct": RHR_ALERT_PCT, "hrv_pct": HRV_ALERT_PCT},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/trainer/profile")
async def get_trainer_profile_endpoint():
    """Returns the stored training profile (empty object if not yet set)."""
    try:
        return get_trainer_profile()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/trainer/profile")
async def put_trainer_profile(request: Request):
    """Creates or updates the single training profile row."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    fields = [
        "event", "event_date", "fitness_level", "availability", "goals",
        "limitations", "location", "baseline_resting_hr", "baseline_sleep_hours",
        "baseline_hrv", "auto_adjust"
    ]
    text_fields = {"event", "event_date", "fitness_level", "availability", "goals", "limitations", "location"}

    values = {}
    for f in fields:
        if f in body and body[f] is not None:
            val = body[f]
            if f in text_fields:
                val = str(val).strip()[:MAX_INPUT_LEN]
            elif f == "auto_adjust":
                val = 1 if val in (True, 1, "1", "true", "True", "on") else 0
            values[f] = val

    try:
        return {"status": "success", "message": "Training profile saved.", "profile": _save_profile_values(values)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _save_profile_values(values: dict) -> dict:
    """Upserts the single training-profile row and returns it. Shared by the profile
    endpoint and onboarding so both write the row the same way."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM trainer_profile WHERE id = 1")
        exists = cursor.fetchone() is not None
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if exists:
            if values:
                set_clause = ", ".join(f"{k} = ?" for k in values)
                params = list(values.values()) + [now_str]
                cursor.execute(f"UPDATE trainer_profile SET {set_clause}, updated_at = ? WHERE id = 1", params)
            else:
                cursor.execute("UPDATE trainer_profile SET updated_at = ? WHERE id = 1", (now_str,))
        else:
            cols = ["id"] + list(values.keys()) + ["updated_at"]
            placeholders = ", ".join("?" for _ in cols)
            params = [1] + list(values.values()) + [now_str]
            cursor.execute(f"INSERT INTO trainer_profile ({', '.join(cols)}) VALUES ({placeholders})", params)
        conn.commit()
    return get_trainer_profile()


@router.get("/api/trainer/adherence")
async def get_trainer_adherence(days: int = Query(14, description="Lookback window in days")):
    """Returns planned vs completed workout adherence over the given window."""
    try:
        return compute_adherence(days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/trainer/baselines/refresh")
async def refresh_trainer_baselines(request: Request):
    """Recomputes the RHR/sleep/HRV baselines now (Issue #35).

    Normally the baselines refresh themselves at most weekly off the Garmin sync;
    this endpoint lets the user force an immediate recompute. Pass {"force": false}
    to honour the weekly cadence instead."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        force = body.get("force", True)
        force = force in (True, 1, "1", "true", "True", "on")
        return recompute_health_baselines(force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/trainer/strength/log")
async def get_strength_logs(limit: int = Query(40, description="Number of logged sets to return")):
    """Returns recent logged strength sets (Issue #34), newest first."""
    try:
        return {"logs": get_recent_strength_logs(limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/trainer/strength/log")
async def add_strength_log(request: Request):
    """Records one completed strength set (name, sets, reps, weight, RPE).

    These logs feed progressive overload: the coach reads the latest load per
    exercise when generating the next plan."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    name = str(body.get("exercise_name") or "").strip()[:120]
    if not name:
        raise HTTPException(status_code=400, detail="An exercise name is required.")

    def _to_int(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def _to_float(v):
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    sets = max(0, _to_int(body.get("sets")))
    reps = max(0, _to_int(body.get("reps")))
    weight = _to_float(body.get("weight"))
    rpe = _to_float(body.get("rpe"))
    if rpe is not None:
        rpe = max(1.0, min(10.0, rpe))  # RPE is a 1-10 scale
    notes = str(body.get("notes") or "").strip()[:MAX_INPUT_LEN]
    plan_id = body.get("plan_id")
    try:
        plan_id = int(plan_id) if plan_id is not None else None
    except (TypeError, ValueError):
        plan_id = None

    date_str = str(body.get("date") or "").strip()[:10]
    if not date_str:
        date_str = today_local().strftime('%Y-%m-%d')
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO trainer_strength_logs
                   (date, exercise_name, sets, reps, weight, rpe, notes, plan_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (date_str, name, sets, reps, weight, rpe, notes, plan_id, now_str)
            )
            conn.commit()
            log_id = cursor.lastrowid
        return {"status": "success", "id": log_id, "message": "Strength set logged."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/trainer/strength/log")
async def delete_strength_log(log_id: int = Query(..., description="ID of the strength log to delete")):
    """Deletes a single logged strength set."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM trainer_strength_logs WHERE id = ?', (log_id,))
            conn.commit()
        return {"status": "success", "message": f"Strength log {log_id} deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/trainer/injuries")
async def get_injuries(
    status: str = Query(None, description="Filter by 'active' or 'resolved' (omit for all)"),
    limit: int = Query(50, description="Number of entries to return"),
):
    """Returns logged injury/pain entries (Issue #38), newest first."""
    try:
        return {"injuries": get_injury_logs(status=status, limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/trainer/injuries")
async def add_injury(request: Request):
    """Logs an injury or pain entry (area, severity, note).

    Active entries are fed into plan generation and the recovery optimizer, so the coach
    eases or swaps sessions that would load the affected area."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    area = str(body.get("area") or "").strip()[:120]
    if not area:
        raise HTTPException(status_code=400, detail="A body area is required.")

    try:
        severity = int(body.get("severity") or 0)
    except (TypeError, ValueError):
        severity = 0
    severity = max(0, min(10, severity)) or None  # 1-10 scale; 0/absent stores NULL

    note = str(body.get("note") or "").strip()[:MAX_INPUT_LEN]
    date_str = str(body.get("date") or "").strip()[:10] or today_local().strftime('%Y-%m-%d')
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO trainer_injury_logs
                   (date, area, severity, note, status, resolved_date, created_at)
                   VALUES (?, ?, ?, ?, 'active', NULL, ?)''',
                (date_str, area, severity, note, now_str)
            )
            conn.commit()
            injury_id = cursor.lastrowid
        return {"status": "success", "id": injury_id, "message": "Injury logged."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/trainer/injuries")
async def update_injury(request: Request):
    """Updates an injury entry - typically to mark it resolved once it stops hurting.

    Resolving stamps `resolved_date` and drops the entry out of the coach prompts, while
    keeping it in the log so a recurring niggle stays visible as history."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    try:
        injury_id = int(body.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="An injury ID is required.")

    values = {}
    if body.get("status") in ("active", "resolved"):
        values["status"] = body["status"]
        # Resolving stamps the date; reopening clears it again.
        values["resolved_date"] = today_local().strftime('%Y-%m-%d') if body["status"] == "resolved" else None
    if "severity" in body:
        try:
            values["severity"] = max(0, min(10, int(body.get("severity") or 0))) or None
        except (TypeError, ValueError):
            pass
    if "note" in body:
        values["note"] = str(body.get("note") or "").strip()[:MAX_INPUT_LEN]
    if "area" in body and str(body.get("area") or "").strip():
        values["area"] = str(body["area"]).strip()[:120]

    if not values:
        raise HTTPException(status_code=400, detail="No fields to update were supplied.")

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            set_clause = ", ".join(f"{k} = ?" for k in values)
            cursor.execute(
                f"UPDATE trainer_injury_logs SET {set_clause} WHERE id = ?",
                list(values.values()) + [injury_id]
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="The injury entry was not found.")
        return {"status": "success", "message": f"Injury {injury_id} updated."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/trainer/injuries")
async def delete_injury(injury_id: int = Query(..., description="ID of the injury entry to delete")):
    """Deletes a single injury/pain entry."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM trainer_injury_logs WHERE id = ?', (injury_id,))
            conn.commit()
        return {"status": "success", "message": f"Injury {injury_id} deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


