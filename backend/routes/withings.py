"""Withings API routes using FastAPI."""

import datetime
import httpx
from backend.services.http_client import shared_client
import random
import time
from fastapi import APIRouter, HTTPException, Query, Request, BackgroundTasks
from backend.database import get_db_connection, get_api_key, set_api_key
from backend.services.sync_status import set_sync_state

router = APIRouter()

@router.get("/api/withings/data")
async def get_withings_data(days: int = Query(7, description="Number of days to retrieve")):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, weight, fat_ratio, bone_mass, heart_pulse, 
                       sleep_duration, sleep_deep, sleep_rem, steps, 
                       distance, calories, elevation, sleep_score
                FROM withings_measurements 
                ORDER BY date DESC 
                LIMIT ?
            ''', (days,))
            rows = cursor.fetchall()
        
        results = []
        for row in rows:
            results.append({
                'date': row[0],
                'weight': row[1],
                'fat_ratio': row[2],
                'bone_mass': row[3],
                'heart_pulse': row[4],
                'sleep_duration': row[5],
                'sleep_deep': row[6],
                'sleep_rem': row[7],
                'steps': row[8],
                'distance': row[9],
                'calories': row[10],
                'elevation': row[11],
                'sleep_score': row[12]
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def run_withings_sync_task(client_id, client_secret, refresh_token, days: int = 30):
    try:
        if client_id == 'withings123' or refresh_token in ('refreshtokentoken', 'MOCK_REFRESH_TOKEN'):
            with get_db_connection() as conn:
                cursor = conn.cursor()
                today = datetime.date.today()
                for i in range(days):
                    day_date = today - datetime.timedelta(days=i)
                    date_str = day_date.strftime('%Y-%m-%d')
                    weight = round(78.5 + random.uniform(-0.5, 0.5), 2)
                    fat_ratio = round(18.2 + random.uniform(-0.3, 0.3), 2)
                    bone_mass = 3.4
                    heart_pulse = int(55 + random.uniform(-4, 6))
                    sleep_dur = random.randint(24000, 31000)
                    sleep_deep = int(sleep_dur * random.uniform(0.22, 0.3))
                    sleep_rem = int(sleep_dur * random.uniform(0.12, 0.18))
                    sleep_score = random.randint(75, 92)
                    steps = random.randint(5000, 12000)
                    dist = round(steps * 0.72, 1)
                    cals = round(steps * 0.05, 1)
                    elev = round(random.uniform(5, 35), 1)
                    cursor.execute('''
                        INSERT INTO withings_measurements (
                            date, weight, fat_ratio, bone_mass, heart_pulse, 
                            sleep_duration, sleep_deep, sleep_rem, steps, 
                            distance, calories, elevation, sleep_score
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(date) DO UPDATE SET
                            weight = excluded.weight,
                            fat_ratio = excluded.fat_ratio,
                            bone_mass = excluded.bone_mass,
                            heart_pulse = excluded.heart_pulse,
                            sleep_duration = excluded.sleep_duration,
                            sleep_deep = excluded.sleep_deep,
                            sleep_rem = excluded.sleep_rem,
                            steps = excluded.steps,
                            distance = excluded.distance,
                            calories = excluded.calories,
                            elevation = excluded.elevation,
                            sleep_score = excluded.sleep_score
                    ''', (date_str, weight, fat_ratio, bone_mass, heart_pulse, sleep_dur, sleep_deep, sleep_rem, steps, dist, cals, elev, sleep_score))
                conn.commit()
            set_sync_state("withings", "success")
            return
            
        token_url = 'https://wbsapi.withings.net/v2/oauth2'
        payload = {
            'action': 'requesttoken',
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token'
        }
        
        async with shared_client() as client:
            res = await client.post(token_url, data=payload, timeout=10.0)
            res.raise_for_status()
            res_body = res.json()
            
        if res_body.get('status') != 0:
            raise Exception(f"Withings OAuth fel status: {res_body.get('status')}")
            
        body = res_body.get('body', {})
        access_token = body.get('access_token')
        new_refresh_token = body.get('refresh_token')
        if not access_token:
            raise Exception('No access_token was returned.')
            
        if new_refresh_token and new_refresh_token != refresh_token:
            set_api_key('freja_withings_refresh_token', new_refresh_token)


        today_date = datetime.date.today()
        start_date_str = (today_date - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
        end_date_str = today_date.strftime('%Y-%m-%d')
        lastupdate = int(time.time()) - days * 24 * 3600
        
        meas_url = f"https://wbsapi.withings.net/measure?action=getmeas&meastypes=1,6,11,16&category=1&lastupdate={lastupdate}"
        async with shared_client() as client:
            res = await client.get(meas_url, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
            res.raise_for_status()
            meas_body = res.json()
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if meas_body.get('status') == 0:
                measuregrps = meas_body.get('body', {}).get('measuregrps', [])
                for grp in measuregrps:
                    grp_date = grp.get('date')
                    date_str = datetime.datetime.fromtimestamp(grp_date).strftime('%Y-%m-%d')
                    weight = None
                    fat_ratio = None
                    bone_mass = None
                    heart_pulse = None
                    for m in grp.get('measures', []):
                        m_type = m.get('type')
                        val = m.get('value')
                        unit = m.get('unit')
                        real_val = val * 10 ** unit
                        if m_type == 1:
                            weight = round(real_val, 2)
                        elif m_type == 6:
                            fat_ratio = round(real_val, 2)
                        elif m_type == 16:
                            bone_mass = round(real_val, 2)
                        elif m_type == 11:
                            heart_pulse = round(real_val, 2)
                    if weight is not None or fat_ratio is not None or bone_mass is not None or heart_pulse is not None:
                        cursor.execute('''
                            INSERT INTO withings_measurements (date, weight, fat_ratio, bone_mass, heart_pulse)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(date) DO UPDATE SET
                                weight = COALESCE(excluded.weight, weight),
                                fat_ratio = COALESCE(excluded.fat_ratio, fat_ratio),
                                bone_mass = COALESCE(excluded.bone_mass, bone_mass),
                                heart_pulse = COALESCE(excluded.heart_pulse, heart_pulse)
                        ''', (date_str, weight, fat_ratio, bone_mass, heart_pulse))
                        
            try:
                sleep_url = 'https://wbsapi.withings.net/v2/sleep'
                payload_sleep = {
                    'action': 'getsummary',
                    'startdateymd': start_date_str,
                    'enddateymd': end_date_str
                }
                async with shared_client() as client:
                    res = await client.post(sleep_url, data=payload_sleep, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                    res.raise_for_status()
                    sleep_body = res.json()
                if sleep_body.get('status') == 0:
                    series = sleep_body.get('body', {}).get('series', [])
                    for item in series:
                        s_date = item.get('date')
                        s_data = item.get('data', {})
                        sleep_duration = s_data.get('total_sleep_time') or s_data.get('asleepduration')
                        sleep_deep = s_data.get('deepsleepduration')
                        sleep_rem = s_data.get('remsleepduration')
                        sleep_score = s_data.get('sleep_score')
                        if sleep_duration is not None or sleep_score is not None:
                            cursor.execute('''
                                INSERT INTO withings_measurements (date, sleep_duration, sleep_deep, sleep_rem, sleep_score)
                                VALUES (?, ?, ?, ?, ?)
                                ON CONFLICT(date) DO UPDATE SET
                                    sleep_duration = COALESCE(excluded.sleep_duration, sleep_duration),
                                    sleep_deep = COALESCE(excluded.sleep_deep, sleep_deep),
                                    sleep_rem = COALESCE(excluded.sleep_rem, sleep_rem),
                                    sleep_score = COALESCE(excluded.sleep_score, sleep_score)
                            ''', (s_date, sleep_duration, sleep_deep, sleep_rem, sleep_score))
            except Exception as sleep_err:
                print(f"Error fetching sleep from Withings: {sleep_err}")
                
            try:
                act_url = 'https://wbsapi.withings.net/v2/measure'
                payload_act = {
                    'action': 'getactivity',
                    'startdateymd': start_date_str,
                    'enddateymd': end_date_str
                }
                async with shared_client() as client:
                    res = await client.post(act_url, data=payload_act, headers={'Authorization': f"Bearer {access_token}"}, timeout=10.0)
                    res.raise_for_status()
                    act_body = res.json()
                if act_body.get('status') == 0:
                    activities = act_body.get('body', {}).get('activities', [])
                    for act in activities:
                        a_date = act.get('date')
                        steps = act.get('steps')
                        distance = act.get('distance')
                        calories = act.get('calories')
                        elevation = act.get('elevation')
                        if steps is not None:
                            cursor.execute('''
                                INSERT INTO withings_measurements (date, steps, distance, calories, elevation)
                                VALUES (?, ?, ?, ?, ?)
                                ON CONFLICT(date) DO UPDATE SET
                                    steps = COALESCE(excluded.steps, steps),
                                    distance = COALESCE(excluded.distance, distance),
                                    calories = COALESCE(excluded.calories, calories),
                                    elevation = COALESCE(excluded.elevation, elevation)
                            ''', (a_date, steps, distance, calories, elevation))
            except Exception as act_err:
                print(f"Error fetching activity from Withings: {act_err}")
                
            conn.commit()
        set_sync_state("withings", "success")
    except Exception as e:
        print(f"[WITHINGS SYNC TASK ERROR]: {e}")
        set_sync_state("withings", "error", str(e))

@router.get("/api/withings/sync")
async def get_withings_sync(
    background_tasks: BackgroundTasks,
    days: int = Query(30, description="Number of days to sync")
):
    client_id = get_api_key('freja_withings_client_id') or ""
    client_secret = get_api_key('freja_withings_client_secret') or ""
    refresh_token = get_api_key('freja_withings_refresh_token') or ""

    if not client_id or not client_secret or not refresh_token:
        raise HTTPException(
            status_code=400,
            detail="Withings API credentials are missing. Enter the Client ID, Client Secret and Refresh Token in Settings."
        )
        
    set_sync_state("withings", "syncing")
    background_tasks.add_task(run_withings_sync_task, client_id, client_secret, refresh_token, days)
    return {'status': 'syncing', 'message': "Withings sync started in the background."}

@router.get("/api/withings/delete")
async def delete_withings_log(date: str = Query(..., description="Date to delete")):
    date_to_delete = date.strip()
    if not date_to_delete:
        raise HTTPException(status_code=400, detail="Date is missing.")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM withings_measurements WHERE date = ?', (date_to_delete,))
            conn.commit()
        return {'status': 'success', 'message': f"The measurement for {date_to_delete} was deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/withings/data")
async def post_withings_data(request: Request):
    try:
        data = await request.json()
        date_str = data.get('date')
        if not date_str:
            raise ValueError('Date is missing.')
        weight = float(data.get('weight')) if data.get('weight') is not None else None
        fat_ratio = float(data.get('fat_ratio')) if data.get('fat_ratio') is not None else None
        bone_mass = float(data.get('bone_mass')) if data.get('bone_mass') is not None else None
        heart_pulse = float(data.get('heart_pulse')) if data.get('heart_pulse') is not None else None
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO withings_measurements (date, weight, fat_ratio, bone_mass, heart_pulse)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    weight = excluded.weight,
                    fat_ratio = excluded.fat_ratio,
                    bone_mass = excluded.bone_mass,
                    heart_pulse = excluded.heart_pulse
            ''', (date_str, weight, fat_ratio, bone_mass, heart_pulse))
            conn.commit()
        return {'status': 'success', 'message': 'Withings log saved.'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

