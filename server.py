import http.server
import socketserver
import json
import sqlite3
import os
import urllib.request
import urllib.parse
from bs4 import BeautifulSoup

PORT = 8000
DB_FILE = 'keys.db'

def init_db():
    """Initializes the SQLite database and creates the keys and garmin_health tables if they don't exist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS api_keys (
            key_name TEXT PRIMARY KEY,
            key_value TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS garmin_health (
            date TEXT PRIMARY KEY,
            steps INTEGER,
            sleep_hours REAL,
            resting_hr INTEGER,
            active_calories INTEGER,
            workout_type TEXT,
            workout_duration INTEGER,
            body_battery INTEGER,
            hrv INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS strava_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            type TEXT,
            date TEXT,
            distance REAL,
            moving_time INTEGER,
            elapsed_time INTEGER,
            total_elevation_gain REAL,
            average_speed REAL,
            max_speed REAL,
            average_heartrate REAL,
            max_heartrate REAL,
            calories REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withings_measurements (
            date TEXT PRIMARY KEY,
            weight REAL,
            fat_ratio REAL,
            bone_mass REAL,
            heart_pulse REAL
        )
    ''')
    
    # Run migrations for existing databases
    try:
        cursor.execute("ALTER TABLE garmin_health ADD COLUMN body_battery INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE garmin_health ADD COLUMN hrv INTEGER")
    except sqlite3.OperationalError:
        pass
    
    # Check if empty, then seed
    cursor.execute('SELECT COUNT(*) FROM garmin_health')
    if cursor.fetchone()[0] == 0:
        import datetime
        today = datetime.date.today()
        seed_data = [
            (today - datetime.timedelta(days=1), 10450, 7.5, 58, 450, "Löpning", 45, 80, 65),
            (today - datetime.timedelta(days=2), 8200, 6.8, 60, 200, None, 0, 75, 62),
            (today - datetime.timedelta(days=3), 12100, 8.2, 57, 600, "Cykling", 60, 85, 66),
            (today - datetime.timedelta(days=4), 9300, 7.0, 59, 350, "Styrketräning", 40, 70, 60),
            (today - datetime.timedelta(days=5), 11000, 7.8, 58, 400, "Löpning", 50, 78, 64),
            (today - datetime.timedelta(days=6), 7100, 6.5, 61, 150, None, 0, 65, 58),
            (today - datetime.timedelta(days=7), 8900, 7.2, 60, 300, "Yoga", 30, 72, 61),
        ]
        cursor.executemany('''
            INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [(d.strftime('%Y-%m-%d'), s, sl, r, c, wt, wd, bb, h) for d, s, sl, r, c, wt, wd, bb, h in seed_data])

    # Check if empty, then seed strava
    cursor.execute('SELECT COUNT(*) FROM strava_activities')
    if cursor.fetchone()[0] == 0:
        import datetime
        today = datetime.date.today()
        strava_seed = [
            ("Morgonlöpning i skogen", "Löpning", (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d'), 8200.0, 2700, 2850, 45.0, 3.04, 4.2, 145.0, 165.0, 450.0),
            ("Distanscykling landsväg", "Cykling", (today - datetime.timedelta(days=3)).strftime('%Y-%m-%d'), 22500.0, 3600, 3800, 180.0, 6.25, 9.5, 135.0, 155.0, 600.0),
            ("Intervallpass bana", "Löpning", (today - datetime.timedelta(days=5)).strftime('%Y-%m-%d'), 9100.0, 3000, 3200, 50.0, 3.03, 5.1, 148.0, 170.0, 400.0),
        ]
        cursor.executemany('''
            INSERT INTO strava_activities (name, type, date, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', strava_seed)

    # Check if empty, then seed withings
    cursor.execute('SELECT COUNT(*) FROM withings_measurements')
    if cursor.fetchone()[0] == 0:
        import datetime
        today = datetime.date.today()
        withings_seed = [
            ((today - datetime.timedelta(days=1)).strftime('%Y-%m-%d'), 78.5, 18.2, 3.4, 56.0),
            ((today - datetime.timedelta(days=2)).strftime('%Y-%m-%d'), 78.6, 18.3, 3.4, 58.0),
            ((today - datetime.timedelta(days=3)).strftime('%Y-%m-%d'), 78.3, 18.1, 3.4, 55.0),
            ((today - datetime.timedelta(days=4)).strftime('%Y-%m-%d'), 78.8, 18.4, 3.4, 57.0),
            ((today - datetime.timedelta(days=5)).strftime('%Y-%m-%d'), 78.4, 18.2, 3.4, 56.0),
            ((today - datetime.timedelta(days=6)).strftime('%Y-%m-%d'), 78.2, 18.0, 3.4, 54.0),
            ((today - datetime.timedelta(days=7)).strftime('%Y-%m-%d'), 78.5, 18.3, 3.4, 55.0),
        ]
        cursor.executemany('''
            INSERT INTO withings_measurements (date, weight, fat_ratio, bone_mass, heart_pulse)
            VALUES (?, ?, ?, ?, ?)
        ''', withings_seed)

    conn.commit()
    conn.close()

def perform_search(query):
    """Performs a web search via DuckDuckGo HTML and parses top organic results using BeautifulSoup."""
    url = 'https://html.duckduckgo.com/html/?' + urllib.parse.urlencode({'q': query})
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    )
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            html_content = response.read().decode('utf-8')
            soup = BeautifulSoup(html_content, 'html.parser')
            
            for result_div in soup.find_all('div', class_='result'):
                title_a = result_div.find('a', class_='result__a')
                snippet_a = result_div.find('a', class_='result__snippet')
                
                if title_a:
                    title = title_a.get_text(strip=True)
                    href = title_a.get('href', '')
                    
                    # Unquote and extract URL from DDG redirect redirect link
                    if 'uddg=' in href:
                        try:
                            parsed = urllib.parse.urlparse(href)
                            queries = urllib.parse.parse_qs(parsed.query)
                            if 'uddg' in queries:
                                href = queries['uddg'][0]
                        except Exception:
                            pass

                    # Skip sponsored/ad links from DDG
                    if 'duckduckgo.com/y.js' in href:
                        continue
                    
                    snippet = snippet_a.get_text(strip=True) if snippet_a else ''
                    results.append({
                        'title': title,
                        'snippet': snippet,
                        'link': href
                    })
                    if len(results) >= 5: # Limit to top 5 organic results
                        break
    except Exception as e:
        print(f"Search backend error: {e}")
        return {'error': str(e)}
    return results

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler that serves files and intercepts API calls for keys and searches."""
    
    def do_GET(self):
        if self.path == '/api/keys':
            # Send HTTP headers
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()
            
            # Fetch keys from sqlite
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('SELECT key_name, key_value FROM api_keys')
            rows = cursor.fetchall()
            conn.close()
            
            # Respond with key-value pairs
            keys = {row[0]: row[1] for row in rows}
            self.wfile.write(json.dumps(keys).encode('utf-8'))
        elif self.path.startswith('/api/search'):
            # Parse query parameter from url
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            query = params.get('q', [''])[0].strip()
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()
            
            if not query:
                self.wfile.write(json.dumps([]).encode('utf-8'))
                return
                
            results = perform_search(query)
            self.wfile.write(json.dumps(results).encode('utf-8'))
        elif self.path.startswith('/api/garmin/data'):
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            try:
                days = int(params.get('days', ['7'])[0])
            except ValueError:
                days = 7

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()

            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv 
                FROM garmin_health 
                ORDER BY date DESC 
                LIMIT ?
            ''', (days,))
            rows = cursor.fetchall()
            conn.close()

            results = []
            for row in rows:
                results.append({
                    "date": row[0],
                    "steps": row[1],
                    "sleep_hours": row[2],
                    "resting_hr": row[3],
                    "active_calories": row[4],
                    "workout_type": row[5] or "Ingen",
                    "workout_duration": row[6],
                    "body_battery": row[7],
                    "hrv": row[8]
                })

            self.wfile.write(json.dumps(results).encode('utf-8'))
        elif self.path.startswith('/api/garmin/sync'):
            import datetime
            today_str = datetime.date.today().strftime('%Y-%m-%d')
            
            # Fetch credentials from database
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_garmin_email',))
            row_email = cursor.fetchone()
            cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_garmin_password',))
            row_password = cursor.fetchone()
            conn.close()
            
            email = row_email[0].strip() if row_email else ""
            password = row_password[0] if row_password else ""
            
            if not email or not password:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "error", 
                    "message": "Garmin Connect inloggningsuppgifter saknas. Ange e-post och lösenord i Inställningar."
                }).encode('utf-8'))
                return

            try:
                from garminconnect import Garmin
                # Initialize Garmin client
                client = Garmin(email, password)
                client.login()
                
                # Fetch stats
                stats = client.get_stats(today_str)
                steps = int(stats.get('totalSteps', 0) or 0)
                active_calories = int(stats.get('activeCalories', 0) or 0)
                
                # Fetch sleep (handle case where sleep is None or throws error if no data today yet)
                sleep_hours = 0.0
                try:
                    sleep_data = client.get_sleep_data(today_str)
                    sleep_time_sec = sleep_data.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) or 0
                    sleep_hours = round(sleep_time_sec / 3600.0, 1)
                except Exception:
                    pass
                    
                # Fetch heart rate
                resting_hr = 0
                try:
                    heart_rates = client.get_heart_rates(today_str)
                    resting_hr = int(heart_rates.get('restingHeartRate', 0) or 0)
                except Exception:
                    pass
                    
                # Fetch latest workout today if any
                workout_type = None
                workout_duration = 0
                try:
                    activities = client.get_activities(0, 1)
                    if activities:
                        act = activities[0]
                        start_time_local = act.get('startTimeLocal', '')
                        if start_time_local and start_time_local.startswith(today_str):
                            workout_type = act.get('activityType', {}).get('typeKey')
                            type_mapping = {
                                "running": "Löpning",
                                "cycling": "Cykling",
                                "fitness_equipment": "Styrketräning",
                                "swimming": "Simning",
                                "walking": "Promenad",
                                "yoga": "Yoga"
                            }
                            if workout_type in type_mapping:
                                workout_type = type_mapping[workout_type]
                            else:
                                workout_type = workout_type.replace('_', ' ').capitalize()
                            workout_duration = int(round(act.get('duration', 0) / 60.0))
                except Exception:
                    pass
                
                # Fetch body battery
                body_battery = None
                try:
                    bb_data = client.get_body_battery(today_str)
                    if bb_data and isinstance(bb_data, list):
                        day_bb = bb_data[0]
                        body_battery = day_bb.get('highest')
                except Exception:
                    pass

                # Fetch HRV
                hrv = None
                try:
                    hrv_data = client.get_hrv_data(today_str)
                    if hrv_data and isinstance(hrv_data, dict):
                        hrv_summary = hrv_data.get('hrvSummary', {})
                        if hrv_summary:
                            hrv = hrv_summary.get('lastNightAvg')
                except Exception:
                    pass

                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        steps = excluded.steps,
                        sleep_hours = excluded.sleep_hours,
                        resting_hr = excluded.resting_hr,
                        active_calories = excluded.active_calories,
                        workout_type = excluded.workout_type,
                        workout_duration = excluded.workout_duration,
                        body_battery = excluded.body_battery,
                        hrv = excluded.hrv
                ''', (today_str, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv))
                
                conn.commit()
                conn.close()

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                self.end_headers()
                
                sync_res = {
                    "status": "success",
                    "message": "Garmin-data synkroniserad från ditt konto.",
                    "data": {
                        "date": today_str,
                        "steps": steps,
                        "sleep_hours": sleep_hours,
                        "resting_hr": resting_hr,
                        "active_calories": active_calories,
                        "workout_type": workout_type or "Ingen",
                        "workout_duration": workout_duration,
                        "body_battery": body_battery,
                        "hrv": hrv
                    }
                }
                self.wfile.write(json.dumps(sync_res).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "error",
                    "message": f"Kunde inte ansluta till Garmin Connect: {str(e)}"
                }).encode('utf-8'))
        elif self.path.startswith('/api/garmin/delete'):
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            date_to_delete = params.get('date', [''])[0].strip()
            
            if not date_to_delete:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Datum saknas."}).encode('utf-8'))
                return

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()

            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM garmin_health WHERE date = ?', (date_to_delete,))
            conn.commit()
            conn.close()

            self.wfile.write(json.dumps({"status": "success", "message": f"Logg för {date_to_delete} borttagen."}).encode('utf-8'))
        elif self.path.startswith('/api/strava/sync'):
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_client_id',))
            row_id = cursor.fetchone()
            cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_client_secret',))
            row_secret = cursor.fetchone()
            cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_refresh_token',))
            row_refresh = cursor.fetchone()
            conn.close()
            
            client_id = row_id[0].strip() if row_id else ""
            client_secret = row_secret[0].strip() if row_secret else ""
            refresh_token = row_refresh[0].strip() if row_refresh else ""
            
            if not client_id or not client_secret or not refresh_token:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "error", 
                    "message": "Strava API-uppgifter saknas. Ange Client ID, Client Secret och Refresh Token i Inställningar."
                }).encode('utf-8'))
                return

            try:
                import time
                
                token_url = "https://www.strava.com/oauth/token"
                token_data = urllib.parse.urlencode({
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'refresh_token': refresh_token,
                    'grant_type': 'refresh_token'
                }).encode('utf-8')
                
                req = urllib.request.Request(token_url, data=token_data, method='POST')
                with urllib.request.urlopen(req, timeout=10) as response:
                    res_body = json.loads(response.read().decode('utf-8'))
                    
                access_token = res_body.get('access_token')
                new_refresh_token = res_body.get('refresh_token')
                
                if not access_token:
                    raise Exception("Inget access_token returnerades från Strava OAuth.")
                
                if new_refresh_token and new_refresh_token != refresh_token:
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO api_keys (key_name, key_value)
                        VALUES (?, ?)
                        ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
                    ''', ('freja_strava_refresh_token', new_refresh_token))
                    conn.commit()
                    conn.close()

                after_time = int(time.time()) - (14 * 24 * 3600)
                activities_url = f"https://www.strava.com/api/v3/athlete/activities?after={after_time}&per_page=30"
                req_activities = urllib.request.Request(activities_url, headers={
                    'Authorization': f'Bearer {access_token}'
                }, method='GET')
                
                with urllib.request.urlopen(req_activities, timeout=10) as response:
                    activities = json.loads(response.read().decode('utf-8'))
                
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                
                added_count = 0
                for act in activities:
                    act_id = act.get('id')
                    name = act.get('name')
                    act_type = act.get('type')
                    type_mapping = {
                        "Run": "Löpning",
                        "Ride": "Cykling",
                        "WeightTraining": "Styrketräning",
                        "Swim": "Simning",
                        "Walk": "Promenad",
                        "Yoga": "Yoga"
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
                    # Convert default kilojoules to calories as an estimate if calories not directly present
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
                    added_count += 1
                
                conn.commit()
                conn.close()

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "success",
                    "message": f"Synkroniserade {added_count} aktiviteter från Strava."
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "error",
                    "message": f"Kunde inte ansluta till Strava API: {str(e)}"
                }).encode('utf-8'))
        elif self.path.startswith('/api/strava/data'):
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            try:
                days = int(params.get('days', ['7'])[0])
            except ValueError:
                days = 7

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()

            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, type, date, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories 
                FROM strava_activities 
                ORDER BY date DESC 
                LIMIT ?
            ''', (days,))
            rows = cursor.fetchall()
            conn.close()

            results = []
            for row in rows:
                results.append({
                    "id": row[0],
                    "name": row[1],
                    "type": row[2] or "Annat",
                    "date": row[3],
                    "distance": row[4],
                    "moving_time": row[5],
                    "elapsed_time": row[6],
                    "total_elevation_gain": row[7],
                    "average_speed": row[8],
                    "max_speed": row[9],
                    "average_heartrate": row[10],
                    "max_heartrate": row[11],
                    "calories": row[12]
                })

            self.wfile.write(json.dumps(results).encode('utf-8'))
        elif self.path.startswith('/api/strava/delete'):
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            id_to_delete = params.get('id', [''])[0].strip()
            
            if not id_to_delete:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "ID saknas."}).encode('utf-8'))
                return

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()

            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM strava_activities WHERE id = ?', (id_to_delete,))
            conn.commit()
            conn.close()

            self.wfile.write(json.dumps({"status": "success", "message": f"Aktivitet {id_to_delete} borttagen."}).encode('utf-8'))
        elif self.path.startswith('/api/withings/data'):
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            try:
                days = int(params.get('days', ['7'])[0])
            except ValueError:
                days = 7

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()

            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, weight, fat_ratio, bone_mass, heart_pulse 
                FROM withings_measurements 
                ORDER BY date DESC 
                LIMIT ?
            ''', (days,))
            rows = cursor.fetchall()
            conn.close()

            results = []
            for row in rows:
                results.append({
                    "date": row[0],
                    "weight": row[1],
                    "fat_ratio": row[2],
                    "bone_mass": row[3],
                    "heart_pulse": row[4]
                })

            self.wfile.write(json.dumps(results).encode('utf-8'))
        elif self.path.startswith('/api/withings/sync'):
            # Fetch credentials from database
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_withings_client_id',))
            row_id = cursor.fetchone()
            cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_withings_client_secret',))
            row_secret = cursor.fetchone()
            cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_withings_refresh_token',))
            row_refresh = cursor.fetchone()
            conn.close()
            
            client_id = row_id[0].strip() if row_id else ""
            client_secret = row_secret[0].strip() if row_secret else ""
            refresh_token = row_refresh[0].strip() if row_refresh else ""
            
            if not client_id or not client_secret or not refresh_token:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "error", 
                    "message": "Withings API-uppgifter saknas. Ange Client ID, Client Secret och Refresh Token i Inställningar."
                }).encode('utf-8'))
                return

            try:
                # Refresh tokens and fetch measurements
                import datetime
                import time
                
                token_url = "https://wbsapi.withings.net/v2/oauth2"
                token_data = urllib.parse.urlencode({
                    'action': 'requesttoken',
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'refresh_token': refresh_token,
                    'grant_type': 'refresh_token'
                }).encode('utf-8')
                
                req = urllib.request.Request(token_url, data=token_data, method='POST')
                with urllib.request.urlopen(req, timeout=10) as response:
                    res_body = json.loads(response.read().decode('utf-8'))
                    
                if res_body.get("status") != 0:
                    raise Exception(f"Withings OAuth fel status: {res_body.get('status')}")
                    
                body = res_body.get("body", {})
                access_token = body.get("access_token")
                new_refresh_token = body.get("refresh_token")
                
                if not access_token:
                    raise Exception("Inget access_token returnerades.")
                    
                if new_refresh_token and new_refresh_token != refresh_token:
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO api_keys (key_name, key_value)
                        VALUES (?, ?)
                        ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
                    ''', ('freja_withings_refresh_token', new_refresh_token))
                    conn.commit()
                    conn.close()
                    
                lastupdate = int(time.time()) - (30 * 24 * 3600)
                meas_url = f"https://wbsapi.withings.net/measure?action=getmeas&meastypes=1,6,11,16&category=1&lastupdate={lastupdate}"
                
                req_meas = urllib.request.Request(meas_url, headers={
                    'Authorization': f'Bearer {access_token}'
                }, method='GET')
                
                with urllib.request.urlopen(req_meas, timeout=10) as response:
                    meas_body = json.loads(response.read().decode('utf-8'))
                    
                if meas_body.get("status") != 0:
                    raise Exception(f"Withings API fel status: {meas_body.get('status')}")
                    
                measuregrps = meas_body.get("body", {}).get("measuregrps", [])
                
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                
                added_count = 0
                for grp in measuregrps:
                    grp_date = grp.get("date")
                    date_str = datetime.datetime.fromtimestamp(grp_date).strftime('%Y-%m-%d')
                    
                    weight = None
                    fat_ratio = None
                    bone_mass = None
                    heart_pulse = None
                    
                    for m in grp.get("measures", []):
                        m_type = m.get("type")
                        val = m.get("value")
                        unit = m.get("unit")
                        real_val = val * (10 ** unit)
                        
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
                        added_count += 1
                        
                conn.commit()
                conn.close()

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "success",
                    "message": f"Synkroniserade {added_count} mätningar från Withings."
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "error",
                    "message": f"Kunde inte ansluta till Withings API: {str(e)}"
                }).encode('utf-8'))
        elif self.path.startswith('/api/withings/delete'):
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            date_to_delete = params.get('date', [''])[0].strip()
            
            if not date_to_delete:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Datum saknas."}).encode('utf-8'))
                return

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()

            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM withings_measurements WHERE date = ?', (date_to_delete,))
            conn.commit()
            conn.close()

            self.wfile.write(json.dumps({"status": "success", "message": f"Mätning för {date_to_delete} borttagen."}).encode('utf-8'))
        else:
            # Fall back to standard file serving
            super().do_GET()

    def do_POST(self):
        if self.path == '/api/keys':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                
                # Upsert keys into database
                for key_name, key_value in data.items():
                    cursor.execute('''
                        INSERT INTO api_keys (key_name, key_value)
                        VALUES (?, ?)
                        ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
                    ''', (key_name, key_value))
                    
                conn.commit()
                conn.close()
                
                # Respond with success status
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            except Exception as e:
                # Handle error
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
        elif self.path == '/api/garmin/data':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                date_str = data.get('date')
                if not date_str:
                    raise ValueError("Datum saknas.")
                
                steps = int(data.get('steps', 0) or 0)
                sleep_hours = float(data.get('sleep_hours', 0.0) or 0.0)
                resting_hr = int(data.get('resting_hr', 0) or 0)
                active_calories = int(data.get('active_calories', 0) or 0)
                workout_type = data.get('workout_type', '').strip() or None
                workout_duration = int(data.get('workout_duration', 0) or 0)
                body_battery = int(data.get('body_battery')) if data.get('body_battery') is not None else None
                hrv = int(data.get('hrv')) if data.get('hrv') is not None else None

                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        steps = excluded.steps,
                        sleep_hours = excluded.sleep_hours,
                        resting_hr = excluded.resting_hr,
                        active_calories = excluded.active_calories,
                        workout_type = excluded.workout_type,
                        workout_duration = excluded.workout_duration,
                        body_battery = excluded.body_battery,
                        hrv = excluded.hrv
                ''', (date_str, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv))
                
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Garmin-logg sparad."}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
        elif self.path == '/api/strava/data':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                date_str = data.get('date')
                if not date_str:
                    raise ValueError("Datum saknas.")
                
                name = data.get('name', '').strip() or "Träningspass"
                act_type = data.get('type', '').strip() or "Löpning"
                distance = float(data.get('distance', 0.0) or 0.0)
                moving_time = int(data.get('moving_time', 0) or 0)
                elapsed_time = int(data.get('elapsed_time', 0) or 0) or moving_time
                total_elevation_gain = float(data.get('total_elevation_gain', 0.0) or 0.0)
                average_speed = float(data.get('average_speed', 0.0) or 0.0)
                max_speed = float(data.get('max_speed', 0.0) or 0.0)
                average_heartrate = float(data.get('average_heartrate')) if data.get('average_heartrate') is not None else None
                max_heartrate = float(data.get('max_heartrate')) if data.get('max_heartrate') is not None else None
                calories = float(data.get('calories')) if data.get('calories') is not None else None

                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO strava_activities (name, type, date, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (name, act_type, date_str, distance, moving_time, elapsed_time, total_elevation_gain, average_speed, max_speed, average_heartrate, max_heartrate, calories))
                
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Strava-aktivitet sparad."}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
        elif self.path == '/api/withings/data':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                date_str = data.get('date')
                if not date_str:
                    raise ValueError("Datum saknas.")
                
                weight = float(data.get('weight')) if data.get('weight') is not None else None
                fat_ratio = float(data.get('fat_ratio')) if data.get('fat_ratio') is not None else None
                bone_mass = float(data.get('bone_mass')) if data.get('bone_mass') is not None else None
                heart_pulse = float(data.get('heart_pulse')) if data.get('heart_pulse') is not None else None

                conn = sqlite3.connect(DB_FILE)
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
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Withings-logg sparad."}).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == '__main__':
    # Make sure we change directory to the script's directory so it serves files from the correct root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    
    init_db()
    
    # Configure socket server and allow port reuse
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
            print(f"===========================================================")
            print(f"  F.R.E.J.A. Neural Server running on http://localhost:{PORT}")
            print(f"  API keys database active: {os.path.join(script_dir, DB_FILE)}")
            print(f"===========================================================")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down F.R.E.J.A. Server.")
    except Exception as e:
        print(f"Server error: {e}")
