"""Strava API routes using FastAPI."""

import datetime
import httpx
from backend.services.http_client import shared_client
import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from backend.database import get_db_connection, get_api_key, set_api_key
from backend.services.sync_status import set_sync_state
from backend.services.strava_service import get_strava_access_token
from backend.services.oauth_state import generate_oauth_state, consume_oauth_state
from backend.models import User
from backend.routes.auth import get_current_user

router = APIRouter()

@router.get("/api/strava/oauth-state")
async def get_strava_oauth_state(user: User = Depends(get_current_user)):
    """Mints a one-time nonce binding the upcoming Strava authorization to the caller's account."""
    return {"state": generate_oauth_state(user.id)}

@router.get("/api/strava/callback", response_class=HTMLResponse)
async def get_strava_callback(
    code: str = Query("", description="Authorization code"),
    state: str = Query("", description="OAuth state nonce from /api/strava/oauth-state")
):
    code = code.strip()
    if not code:
        return HTMLResponse('<h3>Error: No authorization code was found in the request.</h3>', status_code=400)

    user_id = consume_oauth_state(state.strip()) if state else None
    if user_id is None:
        return HTMLResponse('<h3>Error: Invalid or expired authorization link. Please try connecting again from Settings.</h3>', status_code=400)

    try:
        client_id = get_api_key('freja_strava_client_id', user_id=user_id) or ""
        client_secret = get_api_key('freja_strava_client_secret', user_id=user_id) or ""

        if not client_id or not client_secret:
            return HTMLResponse('<h3>Error: The Strava Client ID or Client Secret is missing from the F.R.E.J.A. database. Save them in Settings first.</h3>', status_code=400)
            
        token_url = "https://www.strava.com/oauth/token"
        payload = {
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
            'grant_type': 'authorization_code'
        }
        
        async with shared_client() as client:
            res = await client.post(token_url, data=payload, timeout=10.0)
            res.raise_for_status()
            res_body = res.json()
            
        new_refresh_token = res_body.get('refresh_token')
        if not new_refresh_token:
            raise Exception('Could not read the refresh token from the Strava response.')
            
        set_api_key('freja_strava_refresh_token', new_refresh_token, user_id=user_id)

        success_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Auktorisering Lyckades</title>
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
                <h1>[STRAVA AUTHORIZATION SUCCEEDED]</h1>
                <p>Your refresh token with the required scopes (activity:read) has been saved.</p>
                <p>You can close this window and return to the F.R.E.J.A. Neural Interface.</p>
                <button onclick="window.close()">CLOSE WINDOW</button>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(success_html, status_code=200)
    except Exception as e:
        error_detail = str(e)
        import httpx
        if isinstance(e, httpx.HTTPStatusError):
            try:
                error_detail = f"{e} - Response body: {e.response.text}"
            except Exception:
                pass
        return HTMLResponse(f'<h3>Authorization error: {error_detail}</h3>', status_code=500)

async def run_strava_sync_task(client_id, client_secret, refresh_token, days: int = 14, overwrite: bool = False):
    try:
        from fastapi.params import Query as FastAPIQuery
        if isinstance(days, FastAPIQuery):
            days = 14
        if isinstance(overwrite, FastAPIQuery):
            overwrite = False

        # NOTE: overwrite mode deliberately does NOT delete here. The replacement rows do
        # not exist yet at this point, so clearing the table up front means an expired
        # refresh token, a rate limit or a network blip destroys the user's entire history
        # with nothing to put back. The delete happens in the same transaction as the
        # insert below, once the activities are actually in hand.

        if client_id == '123456' or refresh_token in ('refreshtokentoken', 'MOCK_REFRESH_TOKEN'):
            if overwrite:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM strava_activities")
                    conn.commit()
            set_sync_state("strava", "success")
            return
            
        token_url = 'https://www.strava.com/oauth/token'
        payload = {
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token'
        }
        
        async with shared_client() as client:
            res = await client.post(token_url, data=payload, timeout=10.0)
            res.raise_for_status()
            res_body = res.json()
            
        access_token = res_body.get('access_token')
        new_refresh_token = res_body.get('refresh_token')
        if not access_token:
            raise Exception('No access_token was returned from Strava OAuth.')
            
        if new_refresh_token and new_refresh_token != refresh_token:
            set_api_key('freja_strava_refresh_token', new_refresh_token)

        after_time = int(time.time()) - days * 24 * 3600
        activities = []
        page = 1
        per_page = 200
        async with shared_client() as client:
            while True:
                activities_url = f"https://www.strava.com/api/v3/athlete/activities?after={after_time}&page={page}&per_page={per_page}"
                res = await client.get(activities_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                res.raise_for_status()
                page_data = res.json()
                if not page_data:
                    break
                activities.extend(page_data)
                if len(page_data) < per_page or page >= 5: # Limit to 5 pages (1000 items) to prevent API rate limits
                    break
                page += 1
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Overwrite replaces the history atomically: the delete only lands if the
            # inserts below commit with it, so a failure part-way leaves the old rows intact.
            if overwrite:
                cursor.execute("DELETE FROM strava_activities")
            for act in activities:
                act_id = act.get('id')
                name = act.get('name')
                act_type = act.get('type')
                type_mapping = {
                    'Run': 'Löpning',
                    'Ride': 'Cykling',
                    'WeightTraining': 'Styrketräning',
                    'Swim': 'Simning',
                    'Walk': 'Promenad',
                    'Yoga': 'Yoga'
                }
                if act_type in type_mapping:
                    act_type = type_mapping[act_type]
                start_date_local = act.get('start_date_local', '')
                date_str = start_date_local[:10] if start_date_local else ""
                distance = act.get('distance', 0.0)
                moving_time = act.get('moving_time', 0)
                elapsed_time = act.get('elapsed_time', 0)
                total_elevation_gain = act.get('total_elevation_gain', 0.0)
                average_speed = act.get('average_speed', 0.0)
                max_speed = act.get('max_speed', 0.0)
                average_heartrate = act.get('average_heartrate')
                max_heartrate = act.get('max_heartrate')
                calories = act.get('calories')
                if calories is None and act.get('kilojoules') is not None:
                    calories = float(act.get('kilojoules')) * 1.1
                cursor.execute('''
                    INSERT INTO strava_activities (id, name, type, date, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        type = excluded.type,
                        date = excluded.date,
                        distance = excluded.distance,
                        moving_time = excluded.moving_time,
                        elapsed_time = excluded.elapsed_time,
                        total_elevation_gain = excluded.total_elevation_gain,
                        average_speed = excluded.average_speed,
                        max_speed = excluded.max_speed,
                        average_heartrate = excluded.average_heartrate,
                        max_heartrate = excluded.max_heartrate,
                        calories = excluded.calories
                ''', (act_id, name, act_type, date_str, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories))
                
            cursor.execute("DELETE FROM strava_activities WHERE id < 0")
            conn.commit()
        set_sync_state("strava", "success")
    except Exception as e:
        # A failed sync must surface as a failure. This used to seed the five demo
        # activities as a "fallback" and then report success, which meant an expired
        # refresh token silently replaced the user's real training history with
        # fabricated workouts - and those fabrications then fed the PT coach's adherence
        # figures and training advice. Demo seeding now belongs solely to the explicit
        # demo-credential path above, where no real data is at stake.
        print(f"[STRAVA SYNC TASK ERROR]: {e}")
        set_sync_state("strava", "error", str(e))

@router.get("/api/strava/sync")
async def get_strava_sync(
    background_tasks: BackgroundTasks,
    days: int = Query(14, description="Number of days to sync"),
    overwrite: bool = Query(False, description="Clear existing activities before syncing")
):
    from fastapi.params import Query as FastAPIQuery
    if isinstance(days, FastAPIQuery):
        days = 14
    if isinstance(overwrite, FastAPIQuery):
        overwrite = False

    client_id = get_api_key('freja_strava_client_id') or ""
    client_secret = get_api_key('freja_strava_client_secret') or ""
    refresh_token = get_api_key('freja_strava_refresh_token') or ""

    if not client_id or not client_secret or not refresh_token:
        raise HTTPException(
            status_code=400,
            detail="Strava API credentials are missing. Enter the Client ID, Client Secret and Refresh Token in Settings."
        )
        
    set_sync_state("strava", "syncing")
    background_tasks.add_task(run_strava_sync_task, client_id, client_secret, refresh_token, days, overwrite)
    return {'status': 'syncing', 'message': "Strava sync started in the background."}

@router.get("/api/strava/data")
async def get_strava_data(
    days: int = Query(7, description="Number of days to retrieve"),
    limit: int = Query(None, description="Number of activities to retrieve. If provided, overrides days and works as a record limit.")
):
    try:
        from fastapi.params import Query as FastAPIQuery
        if isinstance(limit, FastAPIQuery):
            limit = None
        if isinstance(days, FastAPIQuery):
            days = 7

        with get_db_connection() as conn:
            cursor = conn.cursor()
            if limit is not None:
                cursor.execute('''
                    SELECT id, name, type, date, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories
                    FROM strava_activities
                    ORDER BY date DESC, id DESC
                    LIMIT ?
                ''', (limit,))
            else:
                cutoff = (datetime.date.today() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
                cursor.execute('''
                    SELECT id, name, type, date, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories
                    FROM strava_activities
                    WHERE date >= ?
                    ORDER BY date DESC, id DESC
                ''', (cutoff,))
            rows = cursor.fetchall()
        
        results = []
        for row in rows:
            act_id = row[0]
            name = row[1]
            act_type = row[2] or 'Annat'
            date_str = row[3]
            distance = row[4] or 0.0
            moving_time = row[5] or 0
            elapsed_time = row[6] or 0
            total_elevation_gain = row[7] or 0.0
            average_speed = row[8] or 0.0
            max_speed = row[9] or 0.0
            average_heartrate = row[10]
            max_heartrate = row[11]
            calories = row[12]
            
            # Foot-based activities are reported as pace (min/km), everything else as speed
            # (km/h). Both the Swedish labels (written by the sync's type mapping) and the raw
            # Strava type names are matched, since older rows may hold either.
            formatted_speed = ""
            if act_type in ('Löpning', 'Promenad', 'Run', 'Walk'):
                if distance > 0:
                    pace_seconds_per_km = moving_time / (distance / 1000.0)
                    p_min = int(pace_seconds_per_km // 60)
                    p_sec = int(round(pace_seconds_per_km % 60))
                    if p_sec == 60:
                        p_min += 1
                        p_sec = 0
                    formatted_speed = f"{p_min}:{p_sec:02d} min/km"
            elif moving_time > 0:
                speed_km_h = distance / 1000.0 / (moving_time / 3600.0)
                formatted_speed = f"{speed_km_h:.1f} km/h"
            elif average_speed > 0:
                speed_km_h = average_speed * 3.6
                formatted_speed = f"{speed_km_h:.1f} km/h"
                
            results.append({
                'id': act_id,
                'name': name,
                'type': act_type,
                'date': date_str,
                'distance': distance,
                'moving_time': moving_time,
                'elapsed_time': elapsed_time,
                'total_elevation_gain': total_elevation_gain,
                'average_speed': average_speed,
                'max_speed': max_speed,
                'formatted_speed': formatted_speed,
                'average_heartrate': average_heartrate,
                'max_heartrate': max_heartrate,
                'calories': calories
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/strava/delete")
async def delete_strava_log(id: str = Query(..., description="ID of activity to delete")):
    id_to_delete = id.strip()
    if not id_to_delete:
        raise HTTPException(status_code=400, detail="ID is missing.")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM strava_activities WHERE id = ?', (id_to_delete,))
            deleted = cursor.rowcount
            conn.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail=f"No activity with ID {id_to_delete} was found.")
        return {'status': 'success', 'message': f"Activity {id_to_delete} was deleted."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/strava/activity_details")
async def get_strava_activity_details(id: str = Query(..., description="ID of activity")):
    activity_id = id.strip()
    if not activity_id:
        raise HTTPException(status_code=400, detail="Activity ID is missing.")
        
    try:
        access_token = await get_strava_access_token()
        if not access_token or access_token == 'MOCK_ACCESS_TOKEN':
            raise HTTPException(status_code=400, detail="Strava access token is not available.")
            
        try:
            act_url = f"https://www.strava.com/api/v3/activities/{activity_id}"
            
            async with shared_client() as client:
                res = await client.get(act_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                res.raise_for_status()
                activity = res.json()
                
            laps_url = f"https://www.strava.com/api/v3/activities/{activity_id}/laps"
            laps = []
            try:
                async with shared_client() as client:
                    res = await client.get(laps_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                    res.raise_for_status()
                    raw_laps = res.json()
                    for idx, lap in enumerate(raw_laps):
                        laps.append({
                            'lap_index': idx + 1,
                            'name': lap.get('name'),
                            'distance_meters': lap.get('distance'),
                            'elapsed_time_seconds': lap.get('elapsed_time'),
                            'moving_time_seconds': lap.get('moving_time'),
                            'average_speed_m_s': lap.get('average_speed'),
                            'average_heartrate': lap.get('average_heartrate'),
                            'max_heartrate': lap.get('max_heartrate')
                        })
            except Exception as laps_err:
                print(f"Error fetching laps for activity {activity_id}: {laps_err}")
                
            zones_url = f"https://www.strava.com/api/v3/activities/{activity_id}/zones"
            hr_zones = []
            power_zones = []
            try:
                async with shared_client() as client:
                    res = await client.get(zones_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                    res.raise_for_status()
                    raw_zones = res.json()
                    for z in raw_zones:
                        z_type = z.get('type')
                        z_list = z.get('distribution_buckets', [])
                        formatted_zones = []
                        for idx, bucket in enumerate(z_list):
                            formatted_zones.append({
                                'zone': idx + 1,
                                'min_value': bucket.get('min'),
                                'max_value': bucket.get('max'),
                                'time_in_zone_seconds': bucket.get('time')
                            })
                        if z_type == 'heartrate':
                            hr_zones = formatted_zones
                        elif z_type == 'power':
                            power_zones = formatted_zones
            except Exception as zones_err:
                print(f"Error fetching zones for activity {activity_id}: {zones_err}")
                
            act_type_real = activity.get('type', '')
            type_mapping = {
                'Run': 'Löpning',
                'Ride': 'Cykling',
                'VirtualRide': 'Cykling',
                'WeightTraining': 'Styrketräning',
                'Swim': 'Simning',
                'Walk': 'Promenad',
                'Yoga': 'Yoga'
            }
            mapped_type = type_mapping.get(act_type_real, act_type_real)
            dist_meters = activity.get('distance', 0.0) or 0.0
            m_time_secs = activity.get('moving_time', 0) or 0
            avg_speed_ms = activity.get('average_speed', 0.0) or 0.0
            
            # Same pace-vs-speed rule as get_strava_data(); see the comment there.
            formatted_speed = ""
            if mapped_type in ('Löpning', 'Promenad', 'Run', 'Walk'):
                if dist_meters > 0:
                    pace_seconds_per_km = m_time_secs / (dist_meters / 1000.0)
                    p_min = int(pace_seconds_per_km // 60)
                    p_sec = int(round(pace_seconds_per_km % 60))
                    if p_sec == 60:
                        p_min += 1
                        p_sec = 0
                    formatted_speed = f"{p_min}:{p_sec:02d} min/km"
            elif m_time_secs > 0:
                speed_km_h = dist_meters / 1000.0 / (m_time_secs / 3600.0)
                formatted_speed = f"{speed_km_h:.1f} km/h"
            elif avg_speed_ms > 0:
                speed_km_h = avg_speed_ms * 3.6
                formatted_speed = f"{speed_km_h:.1f} km/h"
                
            details = {
                'id': activity.get('id'),
                'name': activity.get('name'),
                'type': activity.get('type'),
                'start_date_local': activity.get('start_date_local'),
                'distance_meters': activity.get('distance'),
                'moving_time_seconds': activity.get('moving_time'),
                'elapsed_time_seconds': activity.get('elapsed_time'),
                'total_elevation_gain_meters': activity.get('total_elevation_gain'),
                'average_speed_m_s': activity.get('average_speed'),
                'max_speed_m_s': activity.get('max_speed'),
                'formatted_speed': formatted_speed,
                'average_heartrate': activity.get('average_heartrate'),
                'max_heartrate': activity.get('max_heartrate'),
                'calories': activity.get('calories'),
                'description': activity.get('description'),
                'laps': laps,
                'heart_rate_zones': hr_zones,
                'power_zones': power_zones
            }
            return details
        except Exception as api_err:
            # A real API failure (429 rate-limit, network error, Strava outage) must not be
            # masked as a fabricated activity with a 200 OK - that hid a broken connection
            # behind plausible-looking fake training data (mock detail data is for the
            # explicit MOCK_ACCESS_TOKEN/demo-id path above, not for real-token failures).
            raise Exception(f"Strava activity details request failed: {api_err}") from api_err
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch the activity details: {str(e)}")

@router.get("/api/strava/athlete_stats")
async def get_strava_athlete_stats():
    try:
        access_token = await get_strava_access_token()
        if not access_token or access_token == 'MOCK_ACCESS_TOKEN':
            raise HTTPException(status_code=400, detail="Strava access token is not available.")
        try:
            athlete_url = "https://www.strava.com/api/v3/athlete"
            async with shared_client() as client:
                res = await client.get(athlete_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                res.raise_for_status()
                athlete = res.json()
            athlete_id = athlete.get('id')
            if not athlete_id:
                raise Exception('Could not read the athlete ID from the profile.')
            stats_url = f"https://www.strava.com/api/v3/athletes/{athlete_id}/stats"
            async with shared_client() as client:
                res = await client.get(stats_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                res.raise_for_status()
                stats = res.json()
            return stats
        except Exception as api_err:
            # Same rule as get_strava_activity_details: mock_stats is for the explicit
            # MOCK_ACCESS_TOKEN demo path only - a real-token failure must not be masked as
            # fabricated lifetime stats with a 200 OK.
            raise Exception(f"Strava athlete stats request failed: {api_err}") from api_err
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch the athlete statistics: {str(e)}")

@router.get("/api/strava/credentials")
async def get_strava_credentials():
    client_id = get_api_key('freja_strava_client_id') or ""
    client_secret = get_api_key('freja_strava_client_secret') or ""
    refresh_token = get_api_key('freja_strava_refresh_token') or ""
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token
    }

@router.post("/api/strava/data")
@router.post("/api/strava/save")
async def post_strava_data(request: Request):
    try:
        data = await request.json()
        date_str = data.get('date')
        if not date_str:
            raise ValueError('Date is missing.')
        name = data.get('name', '').strip() or 'Träningspass'
        act_type = data.get('type', '').strip() or 'Löpning'
        distance = float(data.get('distance', 0.0) or 0.0)
        moving_time = int(data.get('moving_time', 0) or 0)
        elapsed_time = int(data.get('elapsed_time', 0) or 0) or moving_time
        total_elevation_gain = float(data.get('total_elevation_gain', 0.0) or 0.0)
        average_speed = float(data.get('average_speed', 0.0) or 0.0)
        max_speed = float(data.get('max_speed', 0.0) or 0.0)
        average_heartrate = float(data.get('average_heartrate')) if data.get('average_heartrate') is not None else None
        max_heartrate = float(data.get('max_heartrate')) if data.get('max_heartrate') is not None else None
        calories = float(data.get('calories')) if data.get('calories') is not None else None
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO strava_activities (name, type, date, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (name, act_type, date_str, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories))
            conn.commit()
        return {'status': 'success', 'message': 'Strava activity saved.'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


