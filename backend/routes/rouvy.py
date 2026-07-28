"""Rouvy API routes using FastAPI."""

import datetime
import json
import sqlite3
from fastapi import APIRouter, HTTPException, Query, Request, BackgroundTasks
from backend.database import get_db_connection, get_api_key
from backend.services.sync_status import set_sync_state
from backend.services.time_utils import today_local
from backend.services.rouvy_client import RouvyClient, RouvyConfig, AuthenticationError, RouvyApiError

router = APIRouter()

def init_rouvy_db():
    """Ensure Rouvy database tables exist."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rouvy_profile (
                id INTEGER PRIMARY KEY DEFAULT 1,
                date TEXT,
                name TEXT,
                username TEXT,
                gender TEXT,
                height INTEGER,
                weight REAL,
                ftp INTEGER,
                max_hr INTEGER,
                is_premium INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rouvy_activities (
                id TEXT PRIMARY KEY,
                title TEXT,
                date TEXT,
                distance REAL,
                duration INTEGER,
                calories REAL,
                elevation REAL,
                avg_power REAL,
                normalized_power REAL,
                avg_hr REAL,
                max_hr REAL,
                tss REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rouvy_zones (
                id INTEGER PRIMARY KEY DEFAULT 1,
                date TEXT,
                power_zones TEXT,
                hr_zones TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rouvy_career (
                id INTEGER PRIMARY KEY DEFAULT 1,
                date TEXT,
                level INTEGER,
                xp INTEGER,
                coins INTEGER,
                lifetime_distance REAL,
                lifetime_elevation REAL,
                lifetime_time INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

init_rouvy_db()


def run_rouvy_sync_task_blocking(email: str, password: str):
    """Synchronous background sync for Rouvy profile, activities, zones & career."""
    if not email or not password:
        set_sync_state("rouvy", "auth_required", "Credentials missing")
        return

    set_sync_state("rouvy", "syncing")
    try:
        config = RouvyConfig(email=email, password=password)
        client = RouvyClient(config)
        client.login()

        today_str = today_local().isoformat()

        # 1. Profile & Settings
        try:
            prof = client.get_user_profile()
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO rouvy_profile (id, date, name, username, gender, height, weight, ftp, max_hr, is_premium, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        date=excluded.date,
                        name=excluded.name,
                        username=excluded.username,
                        gender=excluded.gender,
                        height=excluded.height,
                        weight=excluded.weight,
                        ftp=excluded.ftp,
                        max_hr=excluded.max_hr,
                        is_premium=excluded.is_premium,
                        updated_at=CURRENT_TIMESTAMP
                ''', (
                    today_str,
                    f"{getattr(prof, 'first_name', '')} {getattr(prof, 'last_name', '')}".strip() or getattr(prof, 'username', 'Rouvy User'),
                    getattr(prof, 'username', None),
                    getattr(prof, 'gender', None),
                    getattr(prof, 'height_cm', None) or getattr(prof, 'height', None),
                    getattr(prof, 'weight_kg', None) or getattr(prof, 'weight', None),
                    getattr(prof, 'ftp_watts', None) or getattr(prof, 'ftp', None),
                    getattr(prof, 'max_heart_rate', None) or getattr(prof, 'max_hr', None),
                    1 if getattr(prof, 'is_premium', False) else 0
                ))
                conn.commit()
        except Exception as e:
            print(f"[ROUVY SYNC] Error fetching profile: {e}")

        # 2. Training Zones
        try:
            zones = client.get_training_zones()
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO rouvy_zones (id, date, power_zones, hr_zones, updated_at)
                    VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        date=excluded.date,
                        power_zones=excluded.power_zones,
                        hr_zones=excluded.hr_zones,
                        updated_at=CURRENT_TIMESTAMP
                ''', (
                    today_str,
                    json.dumps(getattr(zones, 'power_zones', {}) or {}),
                    json.dumps(getattr(zones, 'hr_zones', {}) or {})
                ))
                conn.commit()
        except Exception as e:
            print(f"[ROUVY SYNC] Error fetching zones: {e}")

        # 3. Activity Summary / Recent Activities
        try:
            summary = client.get_activity_summary()
            if hasattr(summary, 'recent_activities') and summary.recent_activities:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    for act in summary.recent_activities:
                        act_id = str(getattr(act, 'id', '')) or str(getattr(act, 'activity_id', ''))
                        if not act_id:
                            continue
                        cursor.execute('''
                            INSERT INTO rouvy_activities (
                                id, title, date, distance, duration, calories, elevation, avg_power, normalized_power, avg_hr, max_hr, tss, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            ON CONFLICT(id) DO UPDATE SET
                                title=excluded.title,
                                date=excluded.date,
                                distance=excluded.distance,
                                duration=excluded.duration,
                                calories=excluded.calories,
                                elevation=excluded.elevation,
                                avg_power=excluded.avg_power,
                                normalized_power=excluded.normalized_power,
                                avg_hr=excluded.avg_hr,
                                max_hr=excluded.max_hr,
                                tss=excluded.tss,
                                updated_at=CURRENT_TIMESTAMP
                        ''', (
                            act_id,
                            getattr(act, 'title', 'Indoor Ride'),
                            getattr(act, 'start_utc', None) or getattr(act, 'date', today_str),
                            getattr(act, 'distance_m', None) or getattr(act, 'distance_km', None) or getattr(act, 'distance', None),
                            getattr(act, 'moving_time_seconds', None) or getattr(act, 'duration_seconds', None) or getattr(act, 'duration', None),
                            getattr(act, 'calories', None),
                            getattr(act, 'elevation_m', None) or getattr(act, 'elevation_gain_m', None) or getattr(act, 'elevation', None),
                            getattr(act, 'avg_power', None),
                            getattr(act, 'normalized_power', None),
                            getattr(act, 'avg_hr', None),
                            getattr(act, 'max_hr', None),
                            getattr(act, 'tss', None)
                        ))
                    conn.commit()
        except Exception as e:
            print(f"[ROUVY SYNC] Error fetching activity summary: {e}")

        set_sync_state("rouvy", "success")

    except AuthenticationError as auth_err:
        set_sync_state("rouvy", "auth_required", str(auth_err))
        print(f"[ROUVY SYNC AUTH ERROR]: {auth_err}")
    except Exception as e:
        set_sync_state("rouvy", "error", str(e))
        print(f"[ROUVY SYNC ERROR]: {e}")


@router.get("/api/rouvy/sync")
async def get_rouvy_sync(background_tasks: BackgroundTasks, days: int = Query(30)):
    """Triggers background sync for Rouvy data."""
    email = get_api_key('freja_rouvy_email') or ""
    password = get_api_key('freja_rouvy_password') or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="Rouvy credentials missing. Set freja_rouvy_email and freja_rouvy_password in Settings.")

    set_sync_state("rouvy", "syncing")
    background_tasks.add_task(run_rouvy_sync_task_blocking, email, password)
    return {"status": "syncing", "message": "Rouvy sync started in background."}


@router.get("/api/rouvy/profile")
async def get_rouvy_profile():
    """Returns stored Rouvy profile."""
    with get_db_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rouvy_profile WHERE id = 1")
        row = cursor.fetchone()
        if not row:
            return {"profile": None}
        return {"profile": dict(row)}


@router.get("/api/rouvy/activities")
async def get_rouvy_activities(limit: int = Query(30, ge=1, le=100)):
    """Returns stored Rouvy activities."""
    with get_db_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rouvy_activities ORDER BY date DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        return {"activities": [dict(r) for r in rows]}


@router.get("/api/rouvy/zones")
async def get_rouvy_zones():
    """Returns stored Rouvy training zones."""
    with get_db_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rouvy_zones WHERE id = 1")
        row = cursor.fetchone()
        if not row:
            return {"zones": None}
        d = dict(row)
        try:
            d["power_zones"] = json.loads(d.get("power_zones") or "{}")
        except Exception:
            pass
        try:
            d["hr_zones"] = json.loads(d.get("hr_zones") or "{}")
        except Exception:
            pass
        return {"zones": d}


@router.get("/api/rouvy/career")
async def get_rouvy_career():
    """Returns stored Rouvy career summary."""
    with get_db_connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rouvy_career WHERE id = 1")
        row = cursor.fetchone()
        if not row:
            return {"career": None}
        return {"career": dict(row)}
