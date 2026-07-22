"""Google Calendar API route router using FastAPI."""

import asyncio
import datetime
from urllib.parse import quote, urlparse
import httpx
from backend.services.http_client import shared_client
from fastapi import APIRouter, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from backend.database import get_db_connection, get_api_key, set_api_key
from backend.origins import origin_of, is_allowed_origin
from backend.services.sync_status import set_sync_state
from backend.services.time_utils import today_local

router = APIRouter()


# The origin policy lives in backend/origins.py so CORS, the auth-failure headers and this
# OAuth redirect check cannot drift apart; a redirect target trusted here is exactly one
# that could have read the response anyway.
_origin_of = origin_of


def _is_allowed_redirect_origin(origin: str, request: Request) -> bool:
    """Whether the OAuth callback may redirect to `origin`."""
    return is_allowed_origin(origin, request)

# In-memory access-token cache, so a plan-booking run that saves/deletes dozens of events
# doesn't do a full network round-trip to oauth2.googleapis.com per event. Keyed by the
# refresh token so reconnecting a different Google account can't serve a stale token.
# `expires_at` carries a 60s safety margin under Google's real expiry.
_token_cache = {"refresh_token": None, "access_token": None, "expires_at": 0.0}


async def get_google_access_token():
    """Tries to get a fresh access token from Google API using the stored refresh token."""
    client_id = get_api_key('freja_google_calendar_client_id') or ""
    client_secret = get_api_key('freja_google_calendar_client_secret') or ""
    refresh_token = get_api_key('freja_google_calendar_refresh_token') or ""

    if not client_id or not refresh_token:
        return None

    if "mock" in client_id.lower() or "mock" in refresh_token.lower():
        return "MOCK_ACCESS_TOKEN"

    import time
    if (
        _token_cache["refresh_token"] == refresh_token
        and _token_cache["access_token"]
        and time.time() < _token_cache["expires_at"]
    ):
        return _token_cache["access_token"]

    try:
        url = "https://oauth2.googleapis.com/token"
        payload = {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }
        if client_secret:
            payload["client_secret"] = client_secret
        async with shared_client() as client:
            res = await client.post(url, data=payload, timeout=8.0)
            if res.status_code == 200:
                data = res.json()
                token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                if token:
                    _token_cache.update({
                        "refresh_token": refresh_token,
                        "access_token": token,
                        "expires_at": time.time() + max(0, int(expires_in) - 60),
                    })
                return token
            else:
                print(f"[GOOGLE CALENDAR TOKEN ERROR]: HTTP {res.status_code} - {res.text}")
                return None
    except Exception as e:
        print(f"[GOOGLE CALENDAR TOKEN EXCEPTION]: {e}")
        return None


def _google_configured_but_unreachable() -> bool:
    """True when a Google account IS configured but `get_google_access_token()` just
    returned None anyway - i.e. the refresh itself failed (revoked grant, expired token,
    network/5xx), not "no account connected". Callers that get None here must not treat it
    as the harmless "not connected" case, or a broken connection silently downgrades every
    push/delete to a local-only no-op that reports success (see issue history on
    core_save_calendar_event / core_delete_calendar_event)."""
    return bool(
        get_api_key('freja_google_calendar_client_id') and
        get_api_key('freja_google_calendar_refresh_token')
    )


async def run_google_calendar_sync_task():
    """Background task to sync calendar events from Google API or generate mock data."""
    try:
        await asyncio.sleep(1.5)  # Simulate network latency
        access_token = await get_google_access_token()

        # `get_google_access_token` returns None both when nothing is configured and when a
        # configured refresh token fails (revoked, expired, network). Only the first case is
        # demo mode; treating the second as demo reported "success" for a sync that never
        # happened, so the user saw a green tick while their calendar silently went stale.
        if not access_token and _google_configured_but_unreachable():
            raise Exception(
                "Could not refresh the Google Calendar access token. "
                "The authorization may have been revoked - reconnect the account in Settings."
            )

        if not access_token or access_token == "MOCK_ACCESS_TOKEN":
            # Mock Sync: Just ensure seed data exists and matches current date context
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM google_calendar_events")
                if cursor.fetchone()[0] == 0:
                    seed_today = today_local()
                    today_str = seed_today.strftime('%Y-%m-%d')
                    tomorrow_str = (seed_today + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                    in_three_days_str = (seed_today + datetime.timedelta(days=3)).strftime('%Y-%m-%d')
                    
                    # Demo events, only inserted when the table is empty, so the calendar
                    # dashboard is not blank before a real Google account is connected.
                    # Written in Swedish because they stand in for the user's own entries.
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
            set_sync_state("google_calendar", "success")
            return

        # Real Google API Sync
        # Fetch events for 30 days past and 30 days future
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        time_min = (now_utc - datetime.timedelta(days=30)).isoformat().replace("+00:00", "Z")
        time_max = (now_utc + datetime.timedelta(days=30)).isoformat().replace("+00:00", "Z")
        
        headers = {"Authorization": f"Bearer {access_token}"}
        base_url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events?timeMin={time_min}&timeMax={time_max}&singleEvents=true&orderBy=startTime"

        # Google paginates at 250 items by default. The cleanup below deletes every locally
        # mapped event missing from this fetch, so a truncated single-page fetch didn't just
        # miss new data - it actively deleted local rows for events still live on Google
        # (singleEvents=true expands recurring events into individual instances, which makes
        # a 60-day window exceed 250 items easily on a moderately busy calendar).
        events_data = []
        async with shared_client() as client:
            url = base_url
            while url:
                res = await client.get(url, headers=headers, timeout=10.0)
                if res.status_code != 200:
                    raise Exception(f"Google API responded with HTTP {res.status_code}: {res.text}")
                page = res.json()
                events_data.extend(page.get("items", []))
                next_token = page.get("nextPageToken")
                url = f"{base_url}&pageToken={next_token}" if next_token else None
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Pull down current list of google_event_ids to merge/upsert.
            # Scoped to the same window the fetch above covered: the cleanup below deletes
            # every mapped event missing from the response, so including rows outside the
            # window would delete events that are still perfectly alive in Google - which
            # silently removed PT sessions booked more than 30 days out (plans can run
            # several weeks ahead) while leaving their trainer_bookings rows dangling.
            window_min = time_min[:10]
            window_max = time_max[:10]
            cursor.execute(
                """SELECT google_event_id, id FROM google_calendar_events
                   WHERE google_event_id IS NOT NULL
                     AND SUBSTR(start_time, 1, 10) >= ?
                     AND SUBSTR(start_time, 1, 10) <= ?""",
                (window_min, window_max)
            )
            existing_mapping = {row[0]: row[1] for row in cursor.fetchall()}

            # Upserts must still match on any existing row, in or out of the window, so a
            # returned event never gets inserted a second time.
            cursor.execute("SELECT google_event_id FROM google_calendar_events WHERE google_event_id IS NOT NULL")
            all_known_ids = {row[0] for row in cursor.fetchall()}
            
            synced_ids = set()
            for item in events_data:
                g_id = item.get("id")
                summary = item.get("summary", "(No title)")
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
                
                if g_id in all_known_ids:
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
        "message": "Google Calendar sync started in the background."
    }

def core_get_calendar_data(days: int = 30) -> list[dict]:
    """Retrieves cached calendar events from the local SQLite database."""
    today = today_local()
    start_bound = (today - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    end_bound = (today + datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    
    with get_db_connection() as conn:
        conn.row_factory = sqlite3_row_factory
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, google_event_id, summary, description, start_time, end_time, location
            FROM google_calendar_events
            WHERE SUBSTR(start_time, 1, 10) >= ? AND SUBSTR(start_time, 1, 10) <= ?
            ORDER BY start_time ASC
        ''', (start_bound, end_bound))
        
        rows = cursor.fetchall()
    return [dict(row) for row in rows]

def sqlite3_row_factory(cursor, row):
    """Simple row factory helper since sqlite3.Row requires connection context."""
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

async def core_save_calendar_event(
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
        raise ValueError("A title, start time and end time are required.")

    # Clean ISO timezone offset if present
    if len(start_time) > 19:
        start_time = start_time[:19]
    if len(end_time) > 19:
        end_time = end_time[:19]

    access_token = await get_google_access_token()
    if not access_token and _google_configured_but_unreachable():
        # A configured-but-broken connection (revoked/expired grant, transient network/5xx)
        # must not silently downgrade to a local-only save that reports success - that's
        # exactly how a booked PT session could exist in the DB with nothing on the real
        # calendar (see issue #59).
        raise RuntimeError(
            "Could not refresh the Google Calendar access token. The authorization may "
            "have been revoked - reconnect the account in Settings."
        )
    google_event_id = None

    with get_db_connection() as conn:
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
            
            # A failed push here must not fall through to the "success" return below - a
            # caller reporting success while the event never reached Google (issue #59) is
            # what let PT sessions be marked "booked" with nothing on the user's calendar.
            try:
                async with shared_client() as client:
                    if google_event_id:
                        # Update Google Event
                        url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{google_event_id}"
                        res = await client.put(url, headers=headers, json=event_resource, timeout=8.0)
                        if res.status_code not in (200, 201):
                            raise RuntimeError(f"HTTP {res.status_code} - {res.text}")
                    else:
                        # Create Google Event
                        url = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
                        res = await client.post(url, headers=headers, json=event_resource, timeout=8.0)
                        if res.status_code in (200, 201):
                            google_event_id = res.json().get("id")
                        else:
                            raise RuntimeError(f"HTTP {res.status_code} - {res.text}")
            except Exception as e:
                print(f"[GOOGLE CALENDAR API ERROR]: {e}")
                raise RuntimeError(f"Failed to sync the event to Google Calendar: {e}") from e

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
    
    return {
        "status": "success",
        "message": "Calendar event saved.",
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

async def core_delete_calendar_event(db_id: int) -> dict:
    """Deletes a calendar event by ID, deleting from Google API if authorized."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Retrieve the google_event_id first
        cursor.execute("SELECT google_event_id, summary FROM google_calendar_events WHERE id = ?", (db_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError("The event was not found.")
            
        google_event_id, summary = row[0], row[1]
        
        # Try to delete from Google Calendar API. A failure here must raise rather than be
        # swallowed (issue #58) - callers that only remove their own tracking row once this
        # succeeds (e.g. trainer.py's replace-don't-stack booking) rely on that to avoid
        # deleting the row for an event that is still live on the user's calendar.
        if google_event_id:
            access_token = await get_google_access_token()
            if not access_token and _google_configured_but_unreachable():
                # Same rule as core_save_calendar_event: a broken (not "unconnected") Google
                # link must not be treated as "nothing to delete on Google" - that would
                # delete the local row while the event is still live (issue #58).
                raise RuntimeError(
                    "Could not refresh the Google Calendar access token. The authorization "
                    "may have been revoked - reconnect the account in Settings."
                )
            if access_token and access_token != "MOCK_ACCESS_TOKEN":
                headers = {"Authorization": f"Bearer {access_token}"}
                url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{google_event_id}"
                try:
                    async with shared_client() as client:
                        res = await client.delete(url, headers=headers, timeout=8.0)
                        # 404/410 means Google already considers it gone - that's the outcome
                        # we want, not a failure.
                        if res.status_code not in (200, 204, 404, 410):
                            raise RuntimeError(f"HTTP {res.status_code} - {res.text}")
                except RuntimeError:
                    raise
                except Exception as e:
                    print(f"[GOOGLE CALENDAR DELETE ERROR]: {e}")
                    raise RuntimeError(f"Failed to delete the event from Google Calendar: {e}") from e

        # Delete from local DB
        cursor.execute("DELETE FROM google_calendar_events WHERE id = ?", (db_id,))
        conn.commit()
    
    return {"status": "success", "message": f"The event '{summary}' was deleted."}

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

        return await core_save_calendar_event(
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

@router.delete("/api/google_calendar/delete")
@router.get("/api/google_calendar/delete")  # kept for older clients; DELETE is preferred so
# the access token can't end up in a query string (server logs, browser history, Referer)
async def delete_google_calendar_event(id: int = Query(..., description="ID of the event to delete")):
    """Deletes a calendar event by ID, deleting from Google API if authorized."""
    try:
        return await core_delete_calendar_event(id)
    except ValueError as val_err:
        raise HTTPException(status_code=404, detail=str(val_err))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class GoogleExchangeRequest(BaseModel):
    code: str
    code_verifier: str
    client_id: str
    redirect_uri: str

@router.get("/api/google_calendar/callback")
async def get_google_calendar_callback(
    request: Request,
    code: str = Query("", description="Authorization code"),
    state: str = Query(None, description="Client frontend origin")
):
    code = code.strip()
    if not code:
        return HTMLResponse('<h3>Error: No authorization code was found in the request.</h3>', status_code=400)

    if state:
        # `state` is attacker-influenceable and this endpoint is auth-exempt, so it is only
        # honoured when it resolves to an allowed origin. Otherwise a crafted consent URL
        # could bounce the victim — and the authorization code — to an arbitrary host.
        origin = _origin_of(state)
        if not _is_allowed_redirect_origin(origin, request):
            return HTMLResponse(
                '<h3>Error: The redirect target in the request is not an allowed origin, so the '
                'authorization was refused.</h3><p>If this is your own HUD on another origin, add '
                'it to the <code>freja_allowed_origins</code> key (comma-separated) in Settings.</p>',
                status_code=400
            )
        return RedirectResponse(url=f"{origin}/google_callback.html?code={quote(code, safe='')}")
    
    # We return an HTML page that does the exchange on the frontend using localStorage
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Google Kalender Auktorisering</title>
        <style>
            body {
                background-color: #0b0f19;
                color: #00f0ff;
                font-family: 'Share Tech Mono', monospace, sans-serif;
                text-align: center;
                padding-top: 100px;
            }
            .container {
                border: 1px solid #00f0ff;
                padding: 40px;
                display: inline-block;
                background-color: rgba(0, 240, 255, 0.05);
                box-shadow: 0 0 20px rgba(0, 240, 255, 0.2);
                border-radius: 8px;
            }
            h1 { font-size: 24px; margin-bottom: 20px; text-shadow: 0 0 10px #00f0ff; }
            p { color: #8892b0; font-size: 16px; }
            .status { font-size: 18px; font-weight: bold; margin-top: 20px; }
            button {
                background: transparent;
                border: 1px solid #00f0ff;
                color: #00f0ff;
                padding: 10px 20px;
                margin-top: 20px;
                cursor: pointer;
                font-family: inherit;
            }
            button:hover {
                background: #00f0ff;
                color: #0b0f19;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>[GOOGLE CALENDAR AUTHORIZATION]</h1>
            <p id="info">Exchanging the authorization code for tokens...</p>
            <div id="status" class="status">Please wait...</div>
            <button id="close-btn" style="display:none;" onclick="window.close()">CLOSE WINDOW</button>
        </div>
        <script>
            async function exchangeToken() {
                const code = new URLSearchParams(window.location.search).get('code');
                const verifier = localStorage.getItem('google_code_verifier');
                const clientId = localStorage.getItem('freja_google_calendar_client_id');
                const statusDiv = document.getElementById('status');
                const infoP = document.getElementById('info');
                const closeBtn = document.getElementById('close-btn');

                if (!code || !verifier || !clientId) {
                    statusDiv.innerHTML = '<span style="color: #ff3366;">Error: The verifier, client ID or code is missing. Make sure you started the connection in this same browser.</span>';
                    infoP.innerText = 'Failed.';
                    return;
                }

                try {
                    const redirectUri = window.location.origin + '/api/google_calendar/callback';
                    const token = localStorage.getItem('freja_access_token') || 'freja1234';
                    const response = await fetch('/api/google_calendar/exchange', {
                        method: 'POST',
                        headers: { 
                            'Content-Type': 'application/json',
                            'X-Freja-Token': token
                        },
                        body: JSON.stringify({
                            code: code,
                            code_verifier: verifier,
                            client_id: clientId,
                            redirect_uri: redirectUri
                        })
                    });
                    const resData = await response.json();
                    if (response.ok && resData.status === 'success') {
                        statusDiv.innerHTML = '<span style="color: #00ff66;">AUTHORIZATION SUCCEEDED</span>';
                        infoP.innerText = 'Your Google account has been connected. The refresh token was saved to the database.';
                        // Clean up verifier
                        localStorage.removeItem('google_code_verifier');
                    } else {
                        throw new Error(resData.detail || resData.message || 'Could not exchange the tokens.');
                    }
                } catch (err) {
                    statusDiv.innerHTML = '<span style="color: #ff3366;">TOKEN EXCHANGE FAILED: ' + err.message + '</span>';
                    infoP.innerText = 'An error occurred.';
                } finally {
                    closeBtn.style.display = 'inline-block';
                }
            }
            window.onload = exchangeToken;
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html_content, status_code=200)

@router.post("/api/google_calendar/exchange")
async def post_google_calendar_exchange(body: GoogleExchangeRequest):
    code = body.code.strip()
    code_verifier = body.code_verifier.strip()
    client_id = body.client_id.strip()
    redirect_uri = body.redirect_uri.strip()

    if not code or not code_verifier or not client_id or not redirect_uri:
        raise HTTPException(status_code=400, detail="The code, verifier, Client ID and redirect URI are required.")

    try:
        url = "https://oauth2.googleapis.com/token"
        payload = {
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"
        }
        
        async with shared_client() as client:
            res = await client.post(url, data=payload, timeout=10.0)
            if res.status_code != 200:
                print(f"[GOOGLE CALENDAR EXCHANGE ERROR]: HTTP {res.status_code} - {res.text}")
                raise Exception(f"Google responded with status {res.status_code}: {res.text}")
                
            res_body = res.json()
            
        new_refresh_token = res_body.get('refresh_token')
        if not new_refresh_token:
            raise Exception("Google returned no refresh token. If you have already connected this account once, revoke the app's access in your Google account before connecting again - that forces Google to show the consent screen and issue a new refresh token.")
            
        # Save client_id and refresh_token to the database
        set_api_key('freja_google_calendar_client_id', client_id)
        set_api_key('freja_google_calendar_refresh_token', new_refresh_token)

        # Since we are using PKCE (Desktop app), there is no client secret. We clear the stored secret.
        set_api_key('freja_google_calendar_client_secret', '')

        return {"status": "success", "message": "Google Calendar-konto har kopplats."}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


