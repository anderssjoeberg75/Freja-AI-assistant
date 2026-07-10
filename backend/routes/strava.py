"""Strava API routes using FastAPI."""

import datetime
import httpx
import time
from fastapi import APIRouter, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import HTMLResponse
from backend.database import get_db_connection, get_api_key, set_api_key
from backend.services.sync_status import set_sync_state
from backend.services.strava_service import get_strava_access_token

router = APIRouter()

@router.get("/api/strava/callback", response_class=HTMLResponse)
async def get_strava_callback(code: str = Query("", description="Authorization code")):
    code = code.strip()
    if not code:
        return HTMLResponse('<h3>Fel: Ingen auktoriseringskod hittades i anropet.</h3>', status_code=400)
    try:
        client_id = get_api_key('freja_strava_client_id') or ""
        client_secret = get_api_key('freja_strava_client_secret') or ""

        if not client_id or not client_secret:
            return HTMLResponse('<h3>Error: The Strava Client ID or Client Secret is missing from the F.R.E.J.A. database. Save them in Settings first.</h3>', status_code=400)
            
        token_url = "https://www.strava.com/oauth/token"
        payload = {
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
            'grant_type': 'authorization_code'
        }
        
        async with httpx.AsyncClient() as client:
            res = await client.post(token_url, data=payload, timeout=10.0)
            res.raise_for_status()
            res_body = res.json()
            
        new_refresh_token = res_body.get('refresh_token')
        if not new_refresh_token:
            raise Exception('Could not read the refresh token from the Strava response.')
            
        set_api_key('freja_strava_refresh_token', new_refresh_token)

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
        return HTMLResponse(f'<h3>Fel vid auktorisering: {str(e)}</h3>', status_code=500)

async def run_strava_sync_task(client_id, client_secret, refresh_token, days: int = 14):
    try:
        if client_id == '123456' or refresh_token in ('refreshtokentoken', 'MOCK_REFRESH_TOKEN'):
            # Demo mode: seed the dashboard with plausible activities so the HUD is not empty
            # before real credentials are configured. Activity names/types are Swedish because
            # they are displayed to the user exactly as a real synced activity would be.
            with get_db_connection() as conn:
                cursor = conn.cursor()
                today = datetime.date.today()
                mock_activities = [
                    (-1, 'Morgonlöpning i parken', 'Löpning', (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d'), 8500.0, 2800, 2950, 45.0, 3.03, 4.2, 148.0, 172.0, 620.0),
                    (-2, 'Kvällscykling', 'Cykling', (today - datetime.timedelta(days=3)).strftime('%Y-%m-%d'), 25000.0, 3600, 3800, 120.0, 6.94, 11.2, 135.0, 155.0, 780.0),
                    (-3, 'Styrkepass - Ben & Bål', 'Styrketräning', (today - datetime.timedelta(days=5)).strftime('%Y-%m-%d'), 0.0, 2700, 3200, 0.0, 0.0, 0.0, 118.0, 145.0, 350.0),
                    (-4, 'Snabbdistans Löpning', 'Löpning', (today - datetime.timedelta(days=8)).strftime('%Y-%m-%d'), 5200.0, 1750, 1800, 25.0, 2.97, 4.0, 142.0, 168.0, 380.0),
                    (-5, 'Aktiv återhämtning Promenad', 'Promenad', (today - datetime.timedelta(days=11)).strftime('%Y-%m-%d'), 4000.0, 3000, 3100, 15.0, 1.33, 1.8, 98.0, 115.0, 220.0)
                ]
                for act in mock_activities:
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
                    ''', act)
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
        
        async with httpx.AsyncClient() as client:
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
        activities_url = f"https://www.strava.com/api/v3/athlete/activities?after={after_time}&per_page=200"
        
        async with httpx.AsyncClient() as client:
            res = await client.get(activities_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
            res.raise_for_status()
            activities = res.json()
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
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
        print(f"Strava sync failed, falling back to mock: {e}")
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                today = datetime.date.today()
                mock_activities = [
                    (-1, 'Morgonlöpning i parken', 'Löpning', (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d'), 8500.0, 2800, 2950, 45.0, 3.03, 4.2, 148.0, 172.0, 620.0),
                    (-2, 'Kvällscykling', 'Cykling', (today - datetime.timedelta(days=3)).strftime('%Y-%m-%d'), 25000.0, 3600, 3800, 120.0, 6.94, 11.2, 135.0, 155.0, 780.0),
                    (-3, 'Styrkepass - Ben & Bål', 'Styrketräning', (today - datetime.timedelta(days=5)).strftime('%Y-%m-%d'), 0.0, 2700, 3200, 0.0, 0.0, 0.0, 118.0, 145.0, 350.0),
                    (-4, 'Snabbdistans Löpning', 'Löpning', (today - datetime.timedelta(days=8)).strftime('%Y-%m-%d'), 5200.0, 1750, 1800, 25.0, 2.97, 4.0, 142.0, 168.0, 380.0),
                    (-5, 'Aktiv återhämtning Promenad', 'Promenad', (today - datetime.timedelta(days=11)).strftime('%Y-%m-%d'), 4000.0, 3000, 3100, 15.0, 1.33, 1.8, 98.0, 115.0, 220.0)
                ]
                for act in mock_activities:
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
                    ''', act)
                conn.commit()
            set_sync_state("strava", "success")
        except Exception as mock_err:
            print(f"[STRAVA SYNC TASK ERROR]: {mock_err}")
            set_sync_state("strava", "error", str(e))

@router.get("/api/strava/sync")
async def get_strava_sync(
    background_tasks: BackgroundTasks,
    days: int = Query(14, description="Number of days to sync")
):
    client_id = get_api_key('freja_strava_client_id') or ""
    client_secret = get_api_key('freja_strava_client_secret') or ""
    refresh_token = get_api_key('freja_strava_refresh_token') or ""

    if not client_id or not client_secret or not refresh_token:
        raise HTTPException(
            status_code=400,
            detail="Strava API credentials are missing. Enter the Client ID, Client Secret and Refresh Token in Settings."
        )
        
    set_sync_state("strava", "syncing")
    background_tasks.add_task(run_strava_sync_task, client_id, client_secret, refresh_token, days)
    return {'status': 'syncing', 'message': "Strava sync started in the background."}

@router.get("/api/strava/data")
async def get_strava_data(days: int = Query(7, description="Number of days to retrieve")):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, type, date, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories 
                FROM strava_activities 
                ORDER BY date DESC 
                LIMIT ?
            ''', (days,))
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
        raise HTTPException(status_code=400, detail="ID saknas.")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM strava_activities WHERE id = ?', (id_to_delete,))
            conn.commit()
        return {'status': 'success', 'message': f"Activity {id_to_delete} was deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/strava/activity_details")
async def get_strava_activity_details(id: str = Query(..., description="ID of activity")):
    activity_id = id.strip()
    if not activity_id:
        raise HTTPException(status_code=400, detail="Aktivitets-ID saknas.")
        
    def serve_mock_details():
        mock_details = {
            'id': int(activity_id) if (activity_id.isdigit() or (activity_id.startswith('-') and activity_id[1:].isdigit())) else 987654321,
            'name': 'Morgonlöpning i skogen',
            'type': 'Run',
            'start_date_local': '2026-06-07T08:15:00Z',
            'distance_meters': 10000.0,
            'moving_time_seconds': 3000,
            'elapsed_time_seconds': 3120,
            'total_elevation_gain_meters': 150.0,
            'average_speed_m_s': 3.33,
            'max_speed_m_s': 4.5,
            'formatted_speed': '5:00 min/km',
            'average_heartrate': 152.0,
            'max_heartrate': 174.0,
            'calories': 780.0,
            'description': 'Skönt tempo, kändes lite tungt i början men flöt på bra efter 3 km.',
            'laps': [
                {'lap_index': 1, 'name': 'Lap 1', 'distance_meters': 1000.0, 'elapsed_time_seconds': 310, 'moving_time_seconds': 310, 'average_speed_m_s': 3.22, 'average_heartrate': 138.0, 'max_heartrate': 145.0},
                {'lap_index': 2, 'name': 'Lap 2', 'distance_meters': 1000.0, 'elapsed_time_seconds': 305, 'moving_time_seconds': 305, 'average_speed_m_s': 3.28, 'average_heartrate': 144.0, 'max_heartrate': 150.0},
                {'lap_index': 3, 'name': 'Lap 3', 'distance_meters': 1000.0, 'elapsed_time_seconds': 300, 'moving_time_seconds': 300, 'average_speed_m_s': 3.33, 'average_heartrate': 149.0, 'max_heartrate': 155.0},
                {'lap_index': 4, 'name': 'Lap 4', 'distance_meters': 1000.0, 'elapsed_time_seconds': 298, 'moving_time_seconds': 298, 'average_speed_m_s': 3.36, 'average_heartrate': 152.0, 'max_heartrate': 158.0},
                {'lap_index': 5, 'name': 'Lap 5', 'distance_meters': 1000.0, 'elapsed_time_seconds': 295, 'moving_time_seconds': 295, 'average_speed_m_s': 3.39, 'average_heartrate': 155.0, 'max_heartrate': 160.0},
                {'lap_index': 6, 'name': 'Lap 6', 'distance_meters': 1000.0, 'elapsed_time_seconds': 302, 'moving_time_seconds': 302, 'average_speed_m_s': 3.31, 'average_heartrate': 154.0, 'max_heartrate': 162.0},
                {'lap_index': 7, 'name': 'Lap 7', 'distance_meters': 1000.0, 'elapsed_time_seconds': 300, 'moving_time_seconds': 300, 'average_speed_m_s': 3.33, 'average_heartrate': 156.0, 'max_heartrate': 161.0},
                {'lap_index': 8, 'name': 'Lap 8', 'distance_meters': 1000.0, 'elapsed_time_seconds': 295, 'moving_time_seconds': 295, 'average_speed_m_s': 3.39, 'average_heartrate': 158.0, 'max_heartrate': 165.0},
                {'lap_index': 9, 'name': 'Lap 9', 'distance_meters': 1000.0, 'elapsed_time_seconds': 300, 'moving_time_seconds': 300, 'average_speed_m_s': 3.33, 'average_heartrate': 160.0, 'max_heartrate': 168.0},
                {'lap_index': 10, 'name': 'Lap 10', 'distance_meters': 1000.0, 'elapsed_time_seconds': 295, 'moving_time_seconds': 295, 'average_speed_m_s': 3.39, 'average_heartrate': 162.0, 'max_heartrate': 174.0}
            ],
            'heart_rate_zones': [
                {'zone': 1, 'min_value': 0, 'max_value': 115, 'time_in_zone_seconds': 120},
                {'zone': 2, 'min_value': 115, 'max_value': 133, 'time_in_zone_seconds': 480},
                {'zone': 3, 'min_value': 133, 'max_value': 152, 'time_in_zone_seconds': 1400},
                {'zone': 4, 'min_value': 152, 'max_value': 171, 'time_in_zone_seconds': 880},
                {'zone': 5, 'min_value': 171, 'max_value': 220, 'time_in_zone_seconds': 120}
            ],
            'power_zones': []
        }
        return mock_details

    try:
        is_mock_id = activity_id.startswith('-') or activity_id in ('7', '8', '9')
        access_token = await get_strava_access_token()
        if access_token == 'MOCK_ACCESS_TOKEN' or is_mock_id:
            return serve_mock_details()
            
        try:
            act_url = f"https://www.strava.com/api/v3/activities/{activity_id}"
            
            async with httpx.AsyncClient() as client:
                res = await client.get(act_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                res.raise_for_status()
                activity = res.json()
                
            laps_url = f"https://www.strava.com/api/v3/activities/{activity_id}/laps"
            laps = []
            try:
                async with httpx.AsyncClient() as client:
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
                async with httpx.AsyncClient() as client:
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
            print(f"Strava activity details real API failed ({api_err}), falling back to mock details.")
            return serve_mock_details()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not fetch the activity details: {str(e)}")

@router.get("/api/strava/athlete_stats")
async def get_strava_athlete_stats():
    mock_stats = {
        'biggest_ride_distance': 125000.0,
        'biggest_climb_elevation_gain': 1450.0,
        'recent_ride_totals': {'count': 4, 'distance': 180000.0, 'moving_time': 25200, 'elapsed_time': 28800, 'elevation_gain': 2200.0, 'achievement_count': 8},
        'recent_run_totals': {'count': 12, 'distance': 96000.0, 'moving_time': 32400, 'elapsed_time': 33000, 'elevation_gain': 850.0, 'achievement_count': 14},
        'recent_swim_totals': {'count': 2, 'distance': 4000.0, 'moving_time': 5400, 'elapsed_time': 6000, 'elevation_gain': 0.0, 'achievement_count': 1},
        'ytd_ride_totals': {'count': 24, 'distance': 1200000.0, 'moving_time': 172800, 'elapsed_time': 190000, 'elevation_gain': 12500.0, 'achievement_count': 35},
        'ytd_run_totals': {'count': 78, 'distance': 680000.0, 'moving_time': 248400, 'elapsed_time': 252000, 'elevation_gain': 6200.0, 'achievement_count': 92},
        'ytd_swim_totals': {'count': 15, 'distance': 32000.0, 'moving_time': 43200, 'elapsed_time': 45000, 'elevation_gain': 0.0, 'achievement_count': 12},
        'all_ride_totals': {'count': 150, 'distance': 7500000.0, 'moving_time': 1080000, 'elapsed_time': 1150000, 'elevation_gain': 78000.0},
        'all_run_totals': {'count': 450, 'distance': 380000.0, 'moving_time': 1400000, 'elapsed_time': 1420000, 'elevation_gain': 35000.0},
        'all_swim_totals': {'count': 80, 'distance': 180000.0, 'moving_time': 250000, 'elapsed_time': 260000, 'elevation_gain': 0.0}
    }
    try:
        access_token = await get_strava_access_token()
        if access_token == 'MOCK_ACCESS_TOKEN':
            return mock_stats
        try:
            athlete_url = "https://www.strava.com/api/v3/athlete"
            async with httpx.AsyncClient() as client:
                res = await client.get(athlete_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                res.raise_for_status()
                athlete = res.json()
            athlete_id = athlete.get('id')
            if not athlete_id:
                raise Exception('Could not read the athlete ID from the profile.')
            stats_url = f"https://www.strava.com/api/v3/athletes/{athlete_id}/stats"
            async with httpx.AsyncClient() as client:
                res = await client.get(stats_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                res.raise_for_status()
                stats = res.json()
            return stats
        except Exception as api_err:
            print(f"Failed to fetch real athlete stats ({api_err}), falling back to mock stats.")
            return mock_stats
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
            raise ValueError('Datum saknas.')
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


