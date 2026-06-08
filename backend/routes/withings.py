"""Withings HTTP route handlers."""

import datetime
import json
import random
import sqlite3
import time
import urllib.parse
import urllib.request

from backend.config import DB_FILE


def handle_get_withings_data(handler):
    parsed_path = urllib.parse.urlparse(handler.path)
    params = urllib.parse.parse_qs(parsed_path.query)
    try:
        days = int(params.get('days', ['7'])[0])
    except ValueError:
        days = 7
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
    handler.end_headers()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('\n                SELECT date, weight, fat_ratio, bone_mass, heart_pulse, \n                       sleep_duration, sleep_deep, sleep_rem, steps, \n                       distance, calories, elevation, sleep_score\n                FROM withings_measurements \n                ORDER BY date DESC \n                LIMIT ?\n            ', (days,))
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        results.append({'date': row[0], 'weight': row[1], 'fat_ratio': row[2], 'bone_mass': row[3], 'heart_pulse': row[4], 'sleep_duration': row[5], 'sleep_deep': row[6], 'sleep_rem': row[7], 'steps': row[8], 'distance': row[9], 'calories': row[10], 'elevation': row[11], 'sleep_score': row[12]})
    handler.wfile.write(json.dumps(results).encode('utf-8'))

def handle_get_withings_sync(handler):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_withings_client_id',))
    row_id = cursor.fetchone()
    cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_withings_client_secret',))
    row_secret = cursor.fetchone()
    cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_withings_refresh_token',))
    row_refresh = cursor.fetchone()
    conn.close()
    client_id = row_id[0].strip() if row_id else ''
    client_secret = row_secret[0].strip() if row_secret else ''
    refresh_token = row_refresh[0].strip() if row_refresh else ''
    if not client_id or not client_secret or (not refresh_token):
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'error', 'message': 'Withings API-uppgifter saknas. Ange Client ID, Client Secret och Refresh Token i Inställningar.'}).encode('utf-8'))
        return
    try:
        import datetime
        import time
        import random
        if client_id == 'withings123' or refresh_token in ('refreshtokentoken', 'MOCK_REFRESH_TOKEN'):
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            today = datetime.date.today()
            added_count = 0
            for i in range(30):
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
                cursor.execute('\n                            INSERT INTO withings_measurements (\n                                date, weight, fat_ratio, bone_mass, heart_pulse, \n                                sleep_duration, sleep_deep, sleep_rem, steps, \n                                distance, calories, elevation, sleep_score\n                            )\n                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                            ON CONFLICT(date) DO UPDATE SET\n                                weight = excluded.weight,\n                                fat_ratio = excluded.fat_ratio,\n                                bone_mass = excluded.bone_mass,\n                                heart_pulse = excluded.heart_pulse,\n                                sleep_duration = excluded.sleep_duration,\n                                sleep_deep = excluded.sleep_deep,\n                                sleep_rem = excluded.sleep_rem,\n                                steps = excluded.steps,\n                                distance = excluded.distance,\n                                calories = excluded.calories,\n                                elevation = excluded.elevation,\n                                sleep_score = excluded.sleep_score\n                        ', (date_str, weight, fat_ratio, bone_mass, heart_pulse, sleep_dur, sleep_deep, sleep_rem, steps, dist, cals, elev, sleep_score))
                added_count += 1
            conn.commit()
            conn.close()
            handler.send_response(200)
            handler.send_header('Content-Type', 'application/json')
            handler.end_headers()
            handler.wfile.write(json.dumps({'status': 'success', 'message': f'Synkroniserade {added_count} (MOCK) mätningar från Withings.'}).encode('utf-8'))
            return
        token_url = 'https://wbsapi.withings.net/v2/oauth2'
        token_data = urllib.parse.urlencode({'action': 'requesttoken', 'client_id': client_id, 'client_secret': client_secret, 'refresh_token': refresh_token, 'grant_type': 'refresh_token'}).encode('utf-8')
        req = urllib.request.Request(token_url, data=token_data, method='POST')
        with urllib.request.urlopen(req, timeout=10) as response:
            res_body = json.loads(response.read().decode('utf-8'))
        if res_body.get('status') != 0:
            raise Exception(f"Withings OAuth fel status: {res_body.get('status')}")
        body = res_body.get('body', {})
        access_token = body.get('access_token')
        new_refresh_token = body.get('refresh_token')
        if not access_token:
            raise Exception('Inget access_token returnerades.')
        if new_refresh_token and new_refresh_token != refresh_token:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('\n                        INSERT INTO api_keys (key_name, key_value)\n                        VALUES (?, ?)\n                        ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value\n                    ', ('freja_withings_refresh_token', new_refresh_token))
            conn.commit()
            conn.close()
        today_date = datetime.date.today()
        start_date_str = (today_date - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        end_date_str = today_date.strftime('%Y-%m-%d')
        lastupdate = int(time.time()) - 30 * 24 * 3600
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        meas_url = f'https://wbsapi.withings.net/measure?action=getmeas&meastypes=1,6,11,16&category=1&lastupdate={lastupdate}'
        req_meas = urllib.request.Request(meas_url, headers={'Authorization': f'Bearer {access_token}'}, method='GET')
        with urllib.request.urlopen(req_meas, timeout=10) as response:
            meas_body = json.loads(response.read().decode('utf-8'))
        added_count = 0
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
                if weight is not None or fat_ratio is not None or bone_mass is not None or (heart_pulse is not None):
                    cursor.execute('\n                                INSERT INTO withings_measurements (date, weight, fat_ratio, bone_mass, heart_pulse)\n                                VALUES (?, ?, ?, ?, ?)\n                                ON CONFLICT(date) DO UPDATE SET\n                                    weight = COALESCE(excluded.weight, weight),\n                                    fat_ratio = COALESCE(excluded.fat_ratio, fat_ratio),\n                                    bone_mass = COALESCE(excluded.bone_mass, bone_mass),\n                                    heart_pulse = COALESCE(excluded.heart_pulse, heart_pulse)\n                            ', (date_str, weight, fat_ratio, bone_mass, heart_pulse))
                    added_count += 1
        try:
            sleep_url = 'https://wbsapi.withings.net/v2/sleep'
            sleep_data = urllib.parse.urlencode({'action': 'getsummary', 'startdateymd': start_date_str, 'enddateymd': end_date_str}).encode('utf-8')
            req_sleep = urllib.request.Request(sleep_url, data=sleep_data, headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
            with urllib.request.urlopen(req_sleep, timeout=10) as response:
                sleep_body = json.loads(response.read().decode('utf-8'))
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
                        cursor.execute('\n                                    INSERT INTO withings_measurements (date, sleep_duration, sleep_deep, sleep_rem, sleep_score)\n                                    VALUES (?, ?, ?, ?, ?)\n                                    ON CONFLICT(date) DO UPDATE SET\n                                        sleep_duration = COALESCE(excluded.sleep_duration, sleep_duration),\n                                        sleep_deep = COALESCE(excluded.sleep_deep, sleep_deep),\n                                        sleep_rem = COALESCE(excluded.sleep_rem, sleep_rem),\n                                        sleep_score = COALESCE(excluded.sleep_score, sleep_score)\n                                ', (s_date, sleep_duration, sleep_deep, sleep_rem, sleep_score))
                        added_count += 1
        except Exception as sleep_err:
            print(f'Error fetching sleep from Withings: {sleep_err}')
        try:
            act_url = 'https://wbsapi.withings.net/v2/measure'
            act_data = urllib.parse.urlencode({'action': 'getactivity', 'startdateymd': start_date_str, 'enddateymd': end_date_str}).encode('utf-8')
            req_act = urllib.request.Request(act_url, data=act_data, headers={'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/x-www-form-urlencoded'}, method='POST')
            with urllib.request.urlopen(req_act, timeout=10) as response:
                act_body = json.loads(response.read().decode('utf-8'))
            if act_body.get('status') == 0:
                activities = act_body.get('body', {}).get('activities', [])
                for act in activities:
                    a_date = act.get('date')
                    steps = act.get('steps')
                    distance = act.get('distance')
                    calories = act.get('calories')
                    elevation = act.get('elevation')
                    if steps is not None:
                        cursor.execute('\n                                    INSERT INTO withings_measurements (date, steps, distance, calories, elevation)\n                                    VALUES (?, ?, ?, ?, ?)\n                                    ON CONFLICT(date) DO UPDATE SET\n                                        steps = COALESCE(excluded.steps, steps),\n                                        distance = COALESCE(excluded.distance, distance),\n                                        calories = COALESCE(excluded.calories, calories),\n                                        elevation = COALESCE(excluded.elevation, elevation)\n                                ', (a_date, steps, distance, calories, elevation))
                        added_count += 1
        except Exception as act_err:
            print(f'Error fetching activity from Withings: {act_err}')
        conn.commit()
        conn.close()
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'success', 'message': f'Synkroniserade {added_count} mätningar, sömn och aktivitet från Withings.'}).encode('utf-8'))
    except Exception as e:
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'error', 'message': f'Kunde inte ansluta till Withings API: {str(e)}'}).encode('utf-8'))

def handle_get_withings_delete(handler):
    parsed_path = urllib.parse.urlparse(handler.path)
    params = urllib.parse.parse_qs(parsed_path.query)
    date_to_delete = params.get('date', [''])[0].strip()
    if not date_to_delete:
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'error', 'message': 'Datum saknas.'}).encode('utf-8'))
        return
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
    handler.end_headers()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM withings_measurements WHERE date = ?', (date_to_delete,))
    conn.commit()
    conn.close()
    handler.wfile.write(json.dumps({'status': 'success', 'message': f'Mätning för {date_to_delete} borttagen.'}).encode('utf-8'))

def handle_post_withings_data(handler):
    content_length = int(handler.headers['Content-Length'])
    post_data = handler.rfile.read(content_length)
    try:
        data = json.loads(post_data.decode('utf-8'))
        date_str = data.get('date')
        if not date_str:
            raise ValueError('Datum saknas.')
        weight = float(data.get('weight')) if data.get('weight') is not None else None
        fat_ratio = float(data.get('fat_ratio')) if data.get('fat_ratio') is not None else None
        bone_mass = float(data.get('bone_mass')) if data.get('bone_mass') is not None else None
        heart_pulse = float(data.get('heart_pulse')) if data.get('heart_pulse') is not None else None
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('\n                    INSERT INTO withings_measurements (date, weight, fat_ratio, bone_mass, heart_pulse)\n                    VALUES (?, ?, ?, ?, ?)\n                    ON CONFLICT(date) DO UPDATE SET\n                        weight = excluded.weight,\n                        fat_ratio = excluded.fat_ratio,\n                        bone_mass = excluded.bone_mass,\n                        heart_pulse = excluded.heart_pulse\n                ', (date_str, weight, fat_ratio, bone_mass, heart_pulse))
        conn.commit()
        conn.close()
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'success', 'message': 'Withings-logg sparad.'}).encode('utf-8'))
    except Exception as e:
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
