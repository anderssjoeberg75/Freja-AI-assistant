"""Google Calendar API route router using FastAPI."""

import sqlite3
import datetime
import requests
import json
import time
from fastapi import APIRouter, HTTPException, Query, Request, BackgroundTasks
from backend.config import DB_FILE
from backend.services.sync_status import set_sync_state

router = APIRouter()

def get_google_access_token():
    """Tries to get a fresh access token from Google API using the stored refresh token."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_google_calendar_client_id'")
    row_id = cursor.fetchone()
    cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_google_calendar_client_secret'")
    row_secret = cursor.fetchone()
    cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_google_calendar_refresh_token'")
    row_refresh = cursor.fetchone()
    conn.close()

    client_id = row_id[0].strip() if row_id else ""
    client_secret = row_secret[0].strip() if row_secret else ""
    refresh_token = row_refresh[0].strip() if row_refresh else ""

    if not client_id or not client_secret or not refresh_token:
        return None
        
    if "mock" in client_id.lower() or "mock" in refresh_token.lower():
        return "MOCK_ACCESS_TOKEN"

    try:
        url = "https://oauth2.googleapis.com/token"
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }
        res = requests.post(url, data=payload, timeout=8)
        if res.status_code == 200:
            data = res.json()
            return data.get("access_token")
        else:
            print(f"[GOOGLE CALENDAR TOKEN ERROR]: HTTP {res.status_code} - {res.text}")
            return None
    except Exception as e:
        print(f"[GOOGLE CALENDAR TOKEN EXCEPTION]: {e}")
        return None

def run_google_calendar_sync_task():
    """Background task to sync calendar events from Google API or generate mock data."""
    try:
        time.sleep(1.5)  # Simulate network latency
        access_token = get_google_access_token()

        if not access_token or access_token == "MOCK_ACCESS_TOKEN":
            # Mock Sync: Just ensure seed data exists and matches current date context
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM google_calendar_events")
            if cursor.fetchone()[0] == 0:
                today_str = datetime.date.today().strftime('%Y-%m-%d')
                tomorrow_str = (datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                in_three_days_str = (datetime.date.today() + datetime.timedelta(days=3)).strftime('%Y-%m-%d')
                
                calendar_seed = [
                    ("Möte med Sven", "Gå igenom kvartalsrapporten och planera nästa sprint.", f"{today_str}T10:00:00", f"{today_str}T11:00:00", "Konferensrum A"),
                    ("Lunch med Maria", "Diskutera det nya designförslaget för gränssnittet.", f"{today_str}T12:00:00", f"{today_str}T13:00:00", "Gondolen"),
                    ("Designgenomgång", "Gå igenom feedback från användartester.", f"{tomorrow_str}T14:00:00", f"{tomorrow_str}T15:30:00", "Teams-möte"),
                    ("Läkarbesök", "Årlig hälsokontroll.", f"{in_three_days_str}T08:30:00", f"{in_three_days_str}T09:15:00", "Vårdcentralen City")
                ]
                cursor.executemany('''
                    INSERT INTO google_calendar_events (summary, description, start_time, end_time, location)
                    VALUES (?, ?, ?, ?, ?)
                ''', calendar_seed)
                conn.commit()
            conn.close()
            set_sync_state("google_calendar", "success")
            return

        # Real Google API Sync
        # Fetch events for 30 days past and 30 days future
        time_min = (datetime.datetime.utcnow() - datetime.timedelta(days=30)).isoformat() + "Z"
        time_max = (datetime.datetime.utcnow() + datetime.timedelta(days=30)).isoformat() + "Z"
        
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?timeMin={time_min}&timeMax={time_max}&singleEvents=true&orderBy=startTime"
        
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            raise Exception(f"Google API responded with HTTP {res.status_code}: {res.text}")
            
        events_data = res.json().get("items", [])
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Pull down current list of google_event_ids to merge/upsert
        cursor.execute("SELECT google_event_id, id FROM google_calendar_events WHERE google_event_id IS NOT NULL")
        existing_mapping = {row[0]: row[1] for row in cursor.fetchall()}
        
        synced_ids = set()
        for item in events_data:
            g_id = item.get("id")
            summary = item.get("summary", "(Ingen titel)")
            description = item.get("description", "")
            
            # Start/End date/datetime parsing
            start = item.get("start", {})
            end = item.get("end", {})
            start_time = start.get("dateTime") or start.get("date") or ""
            end_time = end.get("dateTime") or end.get("date") or ""
            location = item.get("location", "")
            
            # Clean up ISO timezone offset for consistency (e.g. 2026-06-12T10:00:00+02:00 -> 2026-06-12T10:00:00)
            if start_time and len(start_time) > 19:
                start_time = start_time[:19]
            if end_time and len(end_time) > 19:
                end_time = end_time[:19]
                
            synced_ids.add(g_id)
            
            if g_id in existing_mapping:
                # Update
                cursor.execute('''
                    UPDATE google_calendar_events 
                    SET summary = ?, description = ?, start_time = ?, end_time = ?, location = ?
                    WHERE google_event_id = ?
                ''', (summary, description, start_time, end_time, location, g_id))
            else:
                # Insert
                cursor.execute('''
                    INSERT INTO google_calendar_events (google_event_id, summary, description, start_time, end_time, location)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (g_id, summary, description, start_time, end_time, location))
                
        # Clean up events in SQLite database that were deleted on Google Calendar
        for g_id, db_id in existing_mapping.items():
            if g_id not in synced_ids:
                cursor.execute("DELETE FROM google_calendar_events WHERE id = ?", (db_id,))
                
        conn.commit()
        conn.close()
        set_sync_state("google_calendar", "success")
        
    except Exception as e:
        print(f"[GOOGLE CALENDAR SYNC TASK ERROR]: {e}")
        set_sync_state("google_calendar", "error", str(e))

@router.get("/api/google_calendar/sync")
async def get_google_calendar_sync(background_tasks: BackgroundTasks):
    """Triggers a background sync thread for Google Calendar."""
    set_sync_state("google_calendar", "syncing")
    background_tasks.add_task(run_google_calendar_sync_task)
    return {
        "status": "syncing",
        "message": "Google Kalender-synkronisering påbörjad i bakgrunden."
    }

def core_get_calendar_data(days: int = 30) -> list[dict]:
    """Retrieves cached calendar events from the local SQLite database."""
    today = datetime.date.today()
    start_bound = (today - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    end_bound = (today + datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT id, google_event_id, summary, description, start_time, end_time, location
        FROM google_calendar_events
        WHERE SUBSTR(start_time, 1, 10) >= ? AND SUBSTR(start_time, 1, 10) <= ?
        ORDER BY start_time ASC
    ''', (start_bound, end_bound))
    
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def core_save_calendar_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    db_id: int = None
) -> dict:
    """Saves (inserts or updates) a calendar event, pushing to Google API if authorized."""
    summary = summary.strip()
    description = description.strip()
    start_time = start_time.strip()
    end_time = end_time.strip()
    location = location.strip()

    if not summary or not start_time or not end_time:
        raise ValueError("Titel, starttid och sluttid krävs.")

    # Clean ISO timezone offset if present
    if len(start_time) > 19:
        start_time = start_time[:19]
    if len(end_time) > 19:
        end_time = end_time[:19]

    access_token = get_google_access_token()
    google_event_id = None
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Fetch existing google_event_id if editing
    if db_id:
        cursor.execute("SELECT google_event_id FROM google_calendar_events WHERE id = ?", (db_id,))
        row = cursor.fetchone()
        if row:
            google_event_id = row[0]

    # Push to Google Calendar API if credentials are valid and NOT mock
    if access_token and access_token != "MOCK_ACCESS_TOKEN":
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        event_resource = {
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": f"{start_time}:00", "timeZone": "Europe/Stockholm"},
            "end": {"dateTime": f"{end_time}:00", "timeZone": "Europe/Stockholm"}
        }
        
        if google_event_id:
            # Update Google Event
            url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{google_event_id}"
            res = requests.put(url, headers=headers, json=event_resource, timeout=8)
            if res.status_code not in (200, 201):
                print(f"[GOOGLE CALENDAR UPDATE ERROR]: HTTP {res.status_code} - {res.text}")
        else:
            # Create Google Event
            url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
            res = requests.post(url, headers=headers, json=event_resource, timeout=8)
            if res.status_code in (200, 201):
                google_event_id = res.json().get("id")
            else:
                print(f"[GOOGLE CALENDAR CREATE ERROR]: HTTP {res.status_code} - {res.text}")

    # Update local database
    if db_id:
        cursor.execute('''
            UPDATE google_calendar_events
            SET summary = ?, description = ?, start_time = ?, end_time = ?, location = ?, google_event_id = ?
            WHERE id = ?
        ''', (summary, description, start_time, end_time, location, google_event_id, db_id))
    else:
        cursor.execute('''
            INSERT INTO google_calendar_events (summary, description, start_time, end_time, location, google_event_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (summary, description, start_time, end_time, location, google_event_id))
        db_id = cursor.lastrowid

    conn.commit()
    conn.close()
    
    return {
        "status": "success",
        "message": "Kalenderhändelse sparad.",
        "event": {
            "id": db_id,
            "google_event_id": google_event_id,
            "summary": summary,
            "description": description,
            "start_time": start_time,
            "end_time": end_time,
            "location": location
        }
    }

def core_delete_calendar_event(db_id: int) -> dict:
    """Deletes a calendar event by ID, deleting from Google API if authorized."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Retrieve the google_event_id first
    cursor.execute("SELECT google_event_id, summary FROM google_calendar_events WHERE id = ?", (db_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError("Händelsen hittades inte.")
        
    google_event_id, summary = row[0], row[1]
    
    # Try to delete from Google Calendar API
    if google_event_id:
        access_token = get_google_access_token()
        if access_token and access_token != "MOCK_ACCESS_TOKEN":
            headers = {"Authorization": f"Bearer {access_token}"}
            url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{google_event_id}"
            res = requests.delete(url, headers=headers, timeout=8)
            if res.status_code not in (200, 204):
                print(f"[GOOGLE CALENDAR DELETE ERROR]: HTTP {res.status_code} - {res.text}")
                
    # Delete from local DB
    cursor.execute("DELETE FROM google_calendar_events WHERE id = ?", (db_id,))
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": f"Händelsen '{summary}' borttagen."}

@router.get("/api/google_calendar/data")
async def get_google_calendar_data(days: int = Query(30, description="Range of days to retrieve events for")):
    """Retrieves cached calendar events from the local SQLite database."""
    try:
        return core_get_calendar_data(days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/google_calendar/data")
async def post_google_calendar_data(request: Request):
    """Saves (inserts or updates) a calendar event, pushing to Google API if authorized."""
    try:
        body = await request.json()
        db_id = body.get("id")
        summary = body.get("summary", "")
        description = body.get("description", "")
        start_time = body.get("start_time", "")
        end_time = body.get("end_time", "")
        location = body.get("location", "")

        return core_save_calendar_event(
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            db_id=db_id
        )
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/google_calendar/delete")
async def delete_google_calendar_event(id: int = Query(..., description="ID of the event to delete")):
    """Deletes a calendar event by ID, deleting from Google API if authorized."""
    try:
        return core_delete_calendar_event(id)
    except ValueError as val_err:
        raise HTTPException(status_code=404, detail=str(val_err))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
