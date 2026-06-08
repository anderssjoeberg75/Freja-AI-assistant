"""Garmin HTTP route handlers."""

import datetime
import json
import os
import sqlite3
import urllib.parse

from backend.config import DB_FILE, PROJECT_ROOT


def handle_get_garmin_data(handler):
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
    cursor.execute('\n                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status \n                FROM garmin_health \n                ORDER BY date DESC \n                LIMIT ?\n            ', (days,))
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        results.append({'date': row[0], 'steps': row[1], 'sleep_hours': row[2], 'resting_hr': row[3], 'active_calories': row[4], 'workout_type': row[5] or 'Ingen', 'workout_duration': row[6], 'body_battery': row[7], 'hrv': row[8], 'recovery_time': row[9], 'training_status': row[10]})
    handler.wfile.write(json.dumps(results).encode('utf-8'))

def handle_get_garmin_sync(handler):
    import datetime
    parsed_path = urllib.parse.urlparse(handler.path)
    params = urllib.parse.parse_qs(parsed_path.query)
    try:
        days = int(params.get('days', ['7'])[0])
    except ValueError:
        days = 7
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_garmin_email',))
    row_email = cursor.fetchone()
    cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_garmin_password',))
    row_password = cursor.fetchone()
    conn.close()
    email = row_email[0].strip() if row_email else ''
    password = row_password[0] if row_password else ''
    if not email or not password:
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'error', 'message': 'Garmin Connect inloggningsuppgifter saknas. Ange e-post och lösenord i Inställningar.'}).encode('utf-8'))
        return
    try:
        from garminconnect import Garmin
        token_dir = os.path.join(os.path.dirname(os.path.abspath(PROJECT_ROOT)), '.garminconnect')
        os.makedirs(token_dir, exist_ok=True)
        client = Garmin(email, password)
        client.login(tokenstore=token_dir)
        today = datetime.date.today()
        dates_to_sync = [(today - datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
        dates_to_sync.reverse()
        activities = []
        try:
            activities = client.get_activities(0, 30)
        except Exception as act_err:
            print(f'Error fetching activities: {act_err}')
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        steps = 0
        active_calories = 0
        sleep_hours = 0.0
        resting_hr = 0
        workout_type = None
        workout_duration = 0
        body_battery = None
        hrv = None
        recovery_time = None
        training_status = None
        for date_str in dates_to_sync:
            try:
                stats = client.get_stats(date_str)
                if stats:
                    steps = int(stats.get('totalSteps', 0) or 0)
                    active_calories = int(stats.get('activeCalories', 0) or 0)
            except Exception as stats_err:
                print(f'Error fetching stats for {date_str}: {stats_err}')
            try:
                sleep_data = client.get_sleep_data(date_str)
                if sleep_data:
                    sleep_time_sec = sleep_data.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) or 0
                    sleep_hours = round(sleep_time_sec / 3600.0, 1)
            except Exception as sleep_err:
                print(f'Error fetching sleep for {date_str}: {sleep_err}')
            try:
                heart_rates = client.get_heart_rates(date_str)
                if heart_rates:
                    resting_hr = int(heart_rates.get('restingHeartRate', 0) or 0)
            except Exception as hr_err:
                print(f'Error fetching heart rates for {date_str}: {hr_err}')
            workout_type = None
            workout_duration = 0
            for act in activities:
                start_time_local = act.get('startTimeLocal', '')
                if start_time_local and start_time_local.startswith(date_str):
                    act_type = act.get('activityType', {}).get('typeKey')
                    type_mapping = {'running': 'Löpning', 'cycling': 'Cykling', 'fitness_equipment': 'Styrketräning', 'swimming': 'Simning', 'walking': 'Promenad', 'yoga': 'Yoga'}
                    if act_type in type_mapping:
                        workout_type = type_mapping[act_type]
                    else:
                        workout_type = act_type.replace('_', ' ').capitalize()
                    workout_duration = int(round(act.get('duration', 0) / 60.0))
                    break
            try:
                bb_data = client.get_body_battery(date_str)
                if bb_data and isinstance(bb_data, list):
                    day_bb = bb_data[0]
                    body_battery = day_bb.get('highest')
            except Exception as bb_err:
                print(f'Error fetching body battery for {date_str}: {bb_err}')
            try:
                hrv_data = client.get_hrv_data(date_str)
                if hrv_data and isinstance(hrv_data, dict):
                    hrv_summary = hrv_data.get('hrvSummary', {})
                    if hrv_summary:
                        hrv = hrv_summary.get('lastNightAvg')
            except Exception as hrv_err:
                print(f'Error fetching HRV for {date_str}: {hrv_err}')
            recovery_time = None
            training_status = None
            try:
                ts_data = client.get_training_status(date_str)
                if ts_data:
                    if isinstance(ts_data, list) and len(ts_data) > 0:
                        ts_data = ts_data[0]
                    if isinstance(ts_data, dict):
                        raw_status = ts_data.get('trainingStatus')
                        if raw_status:
                            status_mapping = {'PRODUCTIVE': 'Produktiv', 'MAINTAINING': 'Underhållande', 'UNPRODUCTIVE': 'Oproduktiv', 'PEAKING': 'Toppform', 'OVERREACHING': 'Övertränad', 'RECOVERY': 'Återhämtning', 'DETRAINING': 'Avtagande form', 'STRAINED': 'Ansträngd'}
                            training_status = status_mapping.get(raw_status.upper(), raw_status.capitalize())
                        recovery_time = ts_data.get('recoveryTimeInHours')
            except Exception as ts_err:
                print(f'Error fetching training status for {date_str}: {ts_err}')
            if recovery_time is None:
                try:
                    tr_data = client.get_training_readiness(date_str)
                    if tr_data:
                        if isinstance(tr_data, list) and len(tr_data) > 0:
                            tr_data = tr_data[0]
                        if isinstance(tr_data, dict):
                            recovery_time = tr_data.get('recoveryTime') or tr_data.get('recoveryTimeInHours')
                            if not recovery_time and 'trainingReadinessDTO' in tr_data:
                                recovery_time = tr_data['trainingReadinessDTO'].get('recoveryTime')
                except Exception as tr_err:
                    print(f'Error fetching training readiness for {date_str}: {tr_err}')
            cursor.execute('\n                        INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status)\n                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                        ON CONFLICT(date) DO UPDATE SET\n                            steps = excluded.steps,\n                            sleep_hours = excluded.sleep_hours,\n                            resting_hr = excluded.resting_hr,\n                            active_calories = excluded.active_calories,\n                            workout_type = excluded.workout_type,\n                            workout_duration = excluded.workout_duration,\n                            body_battery = excluded.body_battery,\n                            hrv = excluded.hrv,\n                            recovery_time = excluded.recovery_time,\n                            training_status = excluded.training_status\n                    ', (date_str, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status))
        conn.commit()
        conn.close()
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        handler.end_headers()
        sync_res = {'status': 'success', 'message': f'Garmin-data synkroniserad från ditt konto för {len(dates_to_sync)} dagar.', 'synced_days': len(dates_to_sync), 'data': {'date': dates_to_sync[-1], 'steps': steps, 'sleep_hours': sleep_hours, 'resting_hr': resting_hr, 'active_calories': active_calories, 'workout_type': workout_type or 'Ingen', 'workout_duration': workout_duration, 'body_battery': body_battery, 'hrv': hrv, 'recovery_time': recovery_time, 'training_status': training_status}}
        handler.wfile.write(json.dumps(sync_res).encode('utf-8'))
    except Exception as e:
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'error', 'message': f'Kunde inte ansluta till Garmin Connect: {str(e)}'}).encode('utf-8'))

def handle_get_garmin_delete(handler):
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
    cursor.execute('DELETE FROM garmin_health WHERE date = ?', (date_to_delete,))
    conn.commit()
    conn.close()
    handler.wfile.write(json.dumps({'status': 'success', 'message': f'Logg för {date_to_delete} borttagen.'}).encode('utf-8'))

def handle_post_garmin_data(handler):
    content_length = int(handler.headers['Content-Length'])
    post_data = handler.rfile.read(content_length)
    try:
        data = json.loads(post_data.decode('utf-8'))
        date_str = data.get('date')
        if not date_str:
            raise ValueError('Datum saknas.')
        steps = int(data.get('steps', 0) or 0)
        sleep_hours = float(data.get('sleep_hours', 0.0) or 0.0)
        resting_hr = int(data.get('resting_hr', 0) or 0)
        active_calories = int(data.get('active_calories', 0) or 0)
        workout_type = data.get('workout_type', '').strip() or None
        workout_duration = int(data.get('workout_duration', 0) or 0)
        body_battery = int(data.get('body_battery')) if data.get('body_battery') is not None else None
        hrv = int(data.get('hrv')) if data.get('hrv') is not None else None
        recovery_time = int(data.get('recovery_time')) if data.get('recovery_time') is not None else None
        training_status = data.get('training_status', '').strip() or None
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('\n                    INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status)\n                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                    ON CONFLICT(date) DO UPDATE SET\n                        steps = excluded.steps,\n                        sleep_hours = excluded.sleep_hours,\n                        resting_hr = excluded.resting_hr,\n                        active_calories = excluded.active_calories,\n                        workout_type = excluded.workout_type,\n                        workout_duration = excluded.workout_duration,\n                        body_battery = excluded.body_battery,\n                        hrv = excluded.hrv,\n                        recovery_time = excluded.recovery_time,\n                        training_status = excluded.training_status\n                ', (date_str, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status))
        conn.commit()
        conn.close()
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'success', 'message': 'Garmin-logg sparad.'}).encode('utf-8'))
    except Exception as e:
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
