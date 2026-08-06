"""Fitbit Web API routes using FastAPI."""

import base64
import datetime
import httpx
from backend.services.http_client import shared_client
import random
import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from backend.database import get_db_connection, get_api_key, set_api_key
from backend.services.sync_status import set_sync_state
from backend.services.time_utils import today_local
from backend.services.oauth_state import generate_oauth_state, consume_oauth_state
from backend.models import User
from backend.routes.auth import get_current_user

MAX_SYNC_DAYS = 3650  # 10 years historical sync limit

router = APIRouter()

@router.get("/api/fitbit/oauth-state")
async def get_fitbit_oauth_state(user: User = Depends(get_current_user)):
    """Mints a one-time nonce binding the upcoming Fitbit authorization to the caller's account."""
    return {"state": generate_oauth_state(user.id)}

async def get_fitbit_access_token() -> str | None:
    """Refreshes and returns an active Fitbit API access token using stored credentials."""
    client_id = get_api_key('freja_fitbit_client_id') or ""
    client_secret = get_api_key('freja_fitbit_client_secret') or ""
    refresh_token = get_api_key('freja_fitbit_refresh_token') or ""

    if not client_id or not client_secret or not refresh_token:
        return None

    if "mock" in client_id.lower() or "mock" in refresh_token.lower():
        return "MOCK_ACCESS_TOKEN"

    token_url = "https://api.fitbit.com/oauth2/token"
    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode('utf-8')).decode('utf-8')
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }

    try:
        async with shared_client() as client:
            res = await client.post(token_url, data=payload, headers=headers, timeout=10.0)
            if res.status_code == 200:
                data = res.json()
                new_access_token = data.get("access_token")
                new_refresh_token = data.get("refresh_token")
                if new_refresh_token and new_refresh_token != refresh_token:
                    set_api_key('freja_fitbit_refresh_token', new_refresh_token)
                return new_access_token
            else:
                print(f"[FITBIT TOKEN ERROR]: HTTP {res.status_code} - {res.text}")
                return None
    except Exception as e:
        print(f"[FITBIT TOKEN EXCEPTION]: {e}")
        return None


@router.get("/api/fitbit/callback", response_class=HTMLResponse)
async def get_fitbit_callback(
    request: Request,
    code: str = Query("", description="Authorization code"),
    state: str = Query("", description="OAuth state nonce from /api/fitbit/oauth-state")
):
    code = code.strip()
    if not code:
        return HTMLResponse('<h3>Error: No authorization code was found in the request.</h3>', status_code=400)

    user_id = consume_oauth_state(state.strip()) if state else None
    if user_id is None:
        return HTMLResponse('<h3>Error: Invalid or expired authorization link. Please try connecting again from Settings.</h3>', status_code=400)

    try:
        client_id = get_api_key('freja_fitbit_client_id', user_id=user_id) or ""
        client_secret = get_api_key('freja_fitbit_client_secret', user_id=user_id) or ""

        if not client_id or not client_secret:
            return HTMLResponse('<h3>Error: Fitbit Client ID or Client Secret is missing from F.R.E.J.A. database. Save them in Settings first.</h3>', status_code=400)

        forwarded_host = request.headers.get("x-forwarded-host")
        if not forwarded_host:
            host_hdr = request.headers.get("host", "")
            if "8000" in host_hdr or not host_hdr:
                forwarded_host = "localhost:5000"
            else:
                forwarded_host = host_hdr

        forwarded_proto = request.headers.get("x-forwarded-proto") or "http"
        redirect_uri = f"{forwarded_proto}://{forwarded_host}{request.url.path}"
        token_url = 'https://api.fitbit.com/oauth2/token'

        auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode('utf-8')).decode('utf-8')
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri
        }

        async with shared_client() as client:
            res = await client.post(token_url, data=payload, headers=headers, timeout=10.0)
            res.raise_for_status()
            res_data = res.json()

        new_refresh_token = res_data.get('refresh_token')
        if not new_refresh_token:
            raise Exception('Could not read refresh_token from Fitbit response.')

        set_api_key('freja_fitbit_refresh_token', new_refresh_token, user_id=user_id)

        success_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Fitbit Authorization Success</title>
            <style>
                body {
                    background-color: #0b0f19;
                    color: #00f0ff;
                    font-family: 'Share Tech Mono', monospace;
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
                <h1>SYSTEM UPLINK: FITBIT CONNECTED</h1>
                <p>Fitbit authorization succeeded and Refresh Token saved to F.R.E.J.A.</p>
                <button onclick="window.close()">CLOSE TAB</button>
            </div>
            <script>
                try {
                    if (window.opener && window.opener.syncKeysFromServer) {
                        window.opener.syncKeysFromServer();
                    }
                } catch(e) {}
            </script>
        </body>
        </html>
        """
        return HTMLResponse(success_html)
    except Exception as e:
        return HTMLResponse(f'<h3>Fitbit Auth Error: {str(e)}</h3>', status_code=400)


async def run_fitbit_sync_task(client_id: str, client_secret: str, refresh_token: str, days: int = 30):
    """Sync task for fetching daily activity, sleep, and heart rate data from Fitbit REST API."""
    try:
        set_sync_state("fitbit", "syncing")
        if client_id == 'fitbit123' or refresh_token in ('refreshtokentoken', 'MOCK_REFRESH_TOKEN'):
            set_sync_state("fitbit", "success")
            return

        access_token = await get_fitbit_access_token()
        if not access_token:
            raise Exception("Could not retrieve a valid Fitbit access token.")

        today = today_local()
        headers = {"Authorization": f"Bearer {access_token}"}

        with get_db_connection() as conn:
            cursor = conn.cursor()
            async with shared_client() as client:
                for i in range(days):
                    day_date = today - datetime.timedelta(days=i)
                    date_str = day_date.strftime('%Y-%m-%d')

                    # 1. Daily Activity (steps, calories, distance)
                    steps = 0
                    cals = 0.0
                    distance_km = 0.0
                    try:
                        act_url = f"https://api.fitbit.com/1/user/-/activities/date/{date_str}.json"
                        res = await client.get(act_url, headers=headers, timeout=8.0)
                        if res.status_code == 200:
                            data = res.json()
                            summary = data.get("summary", {})
                            steps = int(summary.get("steps", 0) or 0)
                            cals = float(summary.get("caloriesOut", 0.0) or 0.0)
                            distances = summary.get("distances", [])
                            for dist in distances:
                                if dist.get("activity") == "total":
                                    distance_km = float(dist.get("distance", 0.0) or 0.0)
                    except Exception as err:
                        print(f"[FITBIT SYNC ACTIVITY ERROR] {date_str}: {err}")

                    # 2. Sleep Data
                    sleep_hours = None
                    deep_hours = None
                    light_hours = None
                    rem_hours = None
                    awake_hours = None
                    sleep_score = None
                    try:
                        sleep_url = f"https://api.fitbit.com/1.2/user/-/sleep/date/{date_str}.json"
                        res = await client.get(sleep_url, headers=headers, timeout=8.0)
                        if res.status_code == 200:
                            data = res.json()
                            sleep_list = data.get("sleep", [])
                            summary = data.get("summary", {})
                            if sleep_list:
                                main_sleep = sleep_list[0]
                                sleep_hours = round(main_sleep.get("minutesAsleep", 0) / 60.0, 2)
                                sleep_score = main_sleep.get("efficiency")
                                levels = main_sleep.get("levels", {}).get("summary", {})
                                if levels:
                                    deep_hours = round((levels.get("deep", {}).get("minutes", 0) or 0) / 60.0, 2)
                                    light_hours = round((levels.get("light", {}).get("minutes", 0) or 0) / 60.0, 2)
                                    rem_hours = round((levels.get("rem", {}).get("minutes", 0) or 0) / 60.0, 2)
                                    awake_hours = round((levels.get("wake", {}).get("minutes", 0) or 0) / 60.0, 2)
                            elif summary.get("totalMinutesAsleep"):
                                sleep_hours = round(summary.get("totalMinutesAsleep", 0) / 60.0, 2)
                    except Exception as err:
                        print(f"[FITBIT SYNC SLEEP ERROR] {date_str}: {err}")

                    # 3. Heart Rate (Resting HR)
                    resting_hr = None
                    try:
                        hr_url = f"https://api.fitbit.com/1/user/-/activities/heart/date/{date_str}/1d.json"
                        res = await client.get(hr_url, headers=headers, timeout=8.0)
                        if res.status_code == 200:
                            data = res.json()
                            hr_list = data.get("activities-heart", [])
                            if hr_list:
                                value = hr_list[0].get("value", {})
                                resting_hr = value.get("restingHeartRate")
                    except Exception as err:
                        print(f"[FITBIT SYNC HR ERROR] {date_str}: {err}")

                    cursor.execute('''
                        INSERT INTO fitbit_health (
                            date, steps, sleep_hours, sleep_deep_hours, sleep_light_hours,
                            sleep_rem_hours, sleep_awake_hours, sleep_score, resting_hr,
                            active_calories, distance_km
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(date) DO UPDATE SET
                            steps = excluded.steps,
                            sleep_hours = excluded.sleep_hours,
                            sleep_deep_hours = excluded.sleep_deep_hours,
                            sleep_light_hours = excluded.sleep_light_hours,
                            sleep_rem_hours = excluded.sleep_rem_hours,
                            sleep_awake_hours = excluded.sleep_awake_hours,
                            sleep_score = excluded.sleep_score,
                            resting_hr = excluded.resting_hr,
                            active_calories = excluded.active_calories,
                            distance_km = excluded.distance_km
                    ''', (
                        date_str, steps, sleep_hours, deep_hours, light_hours,
                        rem_hours, awake_hours, sleep_score, resting_hr,
                        int(cals), distance_km
                    ))

            conn.commit()
        set_sync_state("fitbit", "success")
    except Exception as e:
        print(f"[FITBIT SYNC TASK ERROR]: {e}")
        set_sync_state("fitbit", "error", str(e))


@router.get("/api/fitbit/sync")
@router.post("/api/fitbit/sync")
async def trigger_fitbit_sync(background_tasks: BackgroundTasks, days: int = 30, current_user: User = Depends(get_current_user)):
    client_id = get_api_key('freja_fitbit_client_id', user_id=current_user.id) or ""
    client_secret = get_api_key('freja_fitbit_client_secret', user_id=current_user.id) or ""
    refresh_token = get_api_key('freja_fitbit_refresh_token', user_id=current_user.id) or ""

    if not client_id or not client_secret or not refresh_token:
        raise HTTPException(
            status_code=400,
            detail="Fitbit API credentials missing. Please configure Client ID, Secret, and connect account in Settings first."
        )

    days = max(1, min(days, MAX_SYNC_DAYS))
    set_sync_state("fitbit", "syncing")
    background_tasks.add_task(run_fitbit_sync_task, client_id, client_secret, refresh_token, days)
    return {"status": "syncing", "message": f"Fitbit sync started in the background for {days} days."}


@router.get("/api/fitbit/health")
async def get_fitbit_health_data(days: int = 30):
    days = max(1, min(days, MAX_SYNC_DAYS))
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT date, steps, sleep_hours, sleep_deep_hours, sleep_light_hours,
                       sleep_rem_hours, sleep_awake_hours, sleep_score, resting_hr,
                       active_calories, distance_km
                FROM fitbit_health
                ORDER BY date DESC
                LIMIT ?
            """, (days,))
            rows = cursor.fetchall()

        results = []
        for r in rows:
            results.append({
                'date': r[0],
                'steps': r[1],
                'sleep_hours': r[2],
                'sleep_deep_hours': r[3],
                'sleep_light_hours': r[4],
                'sleep_rem_hours': r[5],
                'sleep_awake_hours': r[6],
                'sleep_score': r[7],
                'resting_hr': r[8],
                'active_calories': r[9],
                'distance_km': r[10]
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/fitbit/credentials")
async def get_fitbit_credentials():
    client_id = get_api_key('freja_fitbit_client_id') or ""
    client_secret = get_api_key('freja_fitbit_client_secret') or ""
    refresh_token = get_api_key('freja_fitbit_refresh_token') or ""
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }


@router.post("/api/fitbit/data")
async def post_fitbit_data(request: Request):
    try:
        data = await request.json()
        date_str = data.get('date')
        if not date_str:
            raise ValueError('Date is missing.')

        steps = int(data.get('steps', 0) or 0)
        sleep_hours = float(data.get('sleep_hours')) if data.get('sleep_hours') is not None else None
        resting_hr = int(data.get('resting_hr')) if data.get('resting_hr') is not None else None
        active_calories = int(data.get('active_calories')) if data.get('active_calories') is not None else None

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO fitbit_health (date, steps, sleep_hours, resting_hr, active_calories)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    steps = excluded.steps,
                    sleep_hours = excluded.sleep_hours,
                    resting_hr = excluded.resting_hr,
                    active_calories = excluded.active_calories
            ''', (date_str, steps, sleep_hours, resting_hr, active_calories))
            conn.commit()

        return {"status": "success", "message": f"Fitbit data saved for {date_str}."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
