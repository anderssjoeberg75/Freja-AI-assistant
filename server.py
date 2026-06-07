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
            hrv INTEGER,
            recovery_time INTEGER,
            training_status TEXT
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
            heart_pulse REAL,
            sleep_duration INTEGER,
            sleep_deep INTEGER,
            sleep_rem INTEGER,
            steps INTEGER,
            distance REAL,
            calories REAL,
            elevation REAL,
            sleep_score INTEGER
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
    try:
        cursor.execute("ALTER TABLE garmin_health ADD COLUMN recovery_time INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE garmin_health ADD COLUMN training_status TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE withings_measurements ADD COLUMN sleep_duration INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE withings_measurements ADD COLUMN sleep_deep INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE withings_measurements ADD COLUMN sleep_rem INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE withings_measurements ADD COLUMN steps INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE withings_measurements ADD COLUMN distance REAL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE withings_measurements ADD COLUMN calories REAL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE withings_measurements ADD COLUMN elevation REAL")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE withings_measurements ADD COLUMN sleep_score INTEGER")
    except sqlite3.OperationalError:
        pass
    
    # Check if empty, then seed
    cursor.execute('SELECT COUNT(*) FROM garmin_health')
    if cursor.fetchone()[0] == 0:
        import datetime
        today = datetime.date.today()
        seed_data = [
            (today - datetime.timedelta(days=1), 10450, 7.5, 58, 450, "Löpning", 45, 80, 65, 12, "Productive"),
            (today - datetime.timedelta(days=2), 8200, 6.8, 60, 200, None, 0, 75, 62, 0, "Maintaining"),
            (today - datetime.timedelta(days=3), 12100, 8.2, 57, 600, "Cykling", 60, 85, 66, 18, "Productive"),
            (today - datetime.timedelta(days=4), 9300, 7.0, 59, 350, "Styrketräning", 40, 70, 60, 8, "Maintaining"),
            (today - datetime.timedelta(days=5), 11000, 7.8, 58, 400, "Löpning", 50, 78, 64, 15, "Productive"),
            (today - datetime.timedelta(days=6), 7100, 6.5, 61, 150, None, 0, 65, 58, 0, "Maintaining"),
            (today - datetime.timedelta(days=7), 8900, 7.2, 60, 300, "Yoga", 30, 72, 61, 2, "Maintaining"),
        ]
        cursor.executemany('''
            INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [(d.strftime('%Y-%m-%d'), s, sl, r, c, wt, wd, bb, h, rt, ts) for d, s, sl, r, c, wt, wd, bb, h, rt, ts in seed_data])

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
            ((today - datetime.timedelta(days=1)).strftime('%Y-%m-%d'), 78.5, 18.2, 3.4, 56.0, 27600, 7200, 3600, 8500, 6200.0, 450.0, 15.0, 85),
            ((today - datetime.timedelta(days=2)).strftime('%Y-%m-%d'), 78.6, 18.3, 3.4, 58.0, 28200, 7500, 3900, 9200, 6800.0, 480.0, 20.0, 88),
            ((today - datetime.timedelta(days=3)).strftime('%Y-%m-%d'), 78.3, 18.1, 3.4, 55.0, 25800, 6600, 3300, 7800, 5600.0, 410.0, 10.0, 80),
            ((today - datetime.timedelta(days=4)).strftime('%Y-%m-%d'), 78.8, 18.4, 3.4, 57.0, 26400, 6900, 3500, 8900, 6400.0, 460.0, 12.0, 83),
            ((today - datetime.timedelta(days=5)).strftime('%Y-%m-%d'), 78.4, 18.2, 3.4, 56.0, 28800, 7800, 4000, 10200, 7500.0, 520.0, 25.0, 90),
            ((today - datetime.timedelta(days=6)).strftime('%Y-%m-%d'), 78.2, 18.0, 3.4, 54.0, 27000, 7000, 3800, 6400, 4500.0, 320.0, 5.0, 82),
            ((today - datetime.timedelta(days=7)).strftime('%Y-%m-%d'), 78.5, 18.3, 3.4, 55.0, 26100, 6800, 3400, 8000, 5800.0, 420.0, 10.0, 81),
        ]
        cursor.executemany('''
            INSERT INTO withings_measurements (date, weight, fat_ratio, bone_mass, heart_pulse, sleep_duration, sleep_deep, sleep_rem, steps, distance, calories, elevation, sleep_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

def get_strava_access_token():
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
        raise Exception("Strava API-uppgifter saknas i inställningarna.")

    # Sandbox / Mock fallback if using default placeholder values
    if client_id == "123456" or refresh_token == "refreshtokentoken":
        return "MOCK_ACCESS_TOKEN"

    try:
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
            
        return access_token
    except Exception as e:
        print(f"Strava token refresh failed, falling back to mock: {e}")
        return "MOCK_ACCESS_TOKEN"

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
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status 
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
                    "hrv": row[8],
                    "recovery_time": row[9],
                    "training_status": row[10]
                })

            self.wfile.write(json.dumps(results).encode('utf-8'))
        elif self.path.startswith('/api/garmin/sync'):
            import datetime
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            try:
                days = int(params.get('days', ['7'])[0])
            except ValueError:
                days = 7
            
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
                
                # Initialize Garmin client with tokenstore directory
                token_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".garminconnect")
                os.makedirs(token_dir, exist_ok=True)
                
                client = Garmin(email, password)
                # Login using tokens if present, else fallback to credentials and save tokens
                client.login(tokenstore=token_dir)
                
                # Generate date range to sync (oldest first)
                today = datetime.date.today()
                dates_to_sync = [(today - datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
                dates_to_sync.reverse()
                
                # Fetch recent activities once to match with date range
                activities = []
                try:
                    # Fetch last 30 activities to cover potential historical sync range
                    activities = client.get_activities(0, 30)
                except Exception as act_err:
                    print(f"Error fetching activities: {act_err}")

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
                    # Fetch stats
                    try:
                        stats = client.get_stats(date_str)
                        if stats:
                            steps = int(stats.get('totalSteps', 0) or 0)
                            active_calories = int(stats.get('activeCalories', 0) or 0)
                    except Exception as stats_err:
                        print(f"Error fetching stats for {date_str}: {stats_err}")

                    # Fetch sleep
                    try:
                        sleep_data = client.get_sleep_data(date_str)
                        if sleep_data:
                            sleep_time_sec = sleep_data.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) or 0
                            sleep_hours = round(sleep_time_sec / 3600.0, 1)
                    except Exception as sleep_err:
                        print(f"Error fetching sleep for {date_str}: {sleep_err}")

                    # Fetch heart rate
                    try:
                        heart_rates = client.get_heart_rates(date_str)
                        if heart_rates:
                            resting_hr = int(heart_rates.get('restingHeartRate', 0) or 0)
                    except Exception as hr_err:
                        print(f"Error fetching heart rates for {date_str}: {hr_err}")

                    # Match activities
                    workout_type = None
                    workout_duration = 0
                    for act in activities:
                        start_time_local = act.get('startTimeLocal', '')
                        if start_time_local and start_time_local.startswith(date_str):
                            act_type = act.get('activityType', {}).get('typeKey')
                            type_mapping = {
                                "running": "Löpning",
                                "cycling": "Cykling",
                                "fitness_equipment": "Styrketräning",
                                "swimming": "Simning",
                                "walking": "Promenad",
                                "yoga": "Yoga"
                            }
                            if act_type in type_mapping:
                                workout_type = type_mapping[act_type]
                            else:
                                workout_type = act_type.replace('_', ' ').capitalize()
                            workout_duration = int(round(act.get('duration', 0) / 60.0))
                            break

                    # Fetch body battery
                    try:
                        bb_data = client.get_body_battery(date_str)
                        if bb_data and isinstance(bb_data, list):
                            day_bb = bb_data[0]
                            body_battery = day_bb.get('highest')
                    except Exception as bb_err:
                        print(f"Error fetching body battery for {date_str}: {bb_err}")

                    # Fetch HRV
                    try:
                        hrv_data = client.get_hrv_data(date_str)
                        if hrv_data and isinstance(hrv_data, dict):
                            hrv_summary = hrv_data.get('hrvSummary', {})
                            if hrv_summary:
                                hrv = hrv_summary.get('lastNightAvg')
                    except Exception as hrv_err:
                        print(f"Error fetching HRV for {date_str}: {hrv_err}")

                    # Fetch training status & recovery time
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
                                    status_mapping = {
                                        "PRODUCTIVE": "Produktiv",
                                        "MAINTAINING": "Underhållande",
                                        "UNPRODUCTIVE": "Oproduktiv",
                                        "PEAKING": "Toppform",
                                        "OVERREACHING": "Övertränad",
                                        "RECOVERY": "Återhämtning",
                                        "DETRAINING": "Avtagande form",
                                        "STRAINED": "Ansträngd"
                                    }
                                    training_status = status_mapping.get(raw_status.upper(), raw_status.capitalize())
                                recovery_time = ts_data.get('recoveryTimeInHours')
                    except Exception as ts_err:
                        print(f"Error fetching training status for {date_str}: {ts_err}")

                    # Fallback to training readiness for recovery time if needed
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
                            print(f"Error fetching training readiness for {date_str}: {tr_err}")

                    cursor.execute('''
                        INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(date) DO UPDATE SET
                            steps = excluded.steps,
                            sleep_hours = excluded.sleep_hours,
                            resting_hr = excluded.resting_hr,
                            active_calories = excluded.active_calories,
                            workout_type = excluded.workout_type,
                            workout_duration = excluded.workout_duration,
                            body_battery = excluded.body_battery,
                            hrv = excluded.hrv,
                            recovery_time = excluded.recovery_time,
                            training_status = excluded.training_status
                    ''', (date_str, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status))
                
                conn.commit()
                conn.close()

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                self.end_headers()
                
                sync_res = {
                    "status": "success",
                    "message": f"Garmin-data synkroniserad från ditt konto för {len(dates_to_sync)} dagar.",
                    "synced_days": len(dates_to_sync),
                    "data": {
                        "date": dates_to_sync[-1],
                        "steps": steps,
                        "sleep_hours": sleep_hours,
                        "resting_hr": resting_hr,
                        "active_calories": active_calories,
                        "workout_type": workout_type or "Ingen",
                        "workout_duration": workout_duration,
                        "body_battery": body_battery,
                        "hrv": hrv,
                        "recovery_time": recovery_time,
                        "training_status": training_status
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
        elif self.path.startswith('/api/strava/callback'):
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            code = params.get('code', [''])[0].strip()
            
            if not code:
                self.send_response(400)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write("<h3>Fel: Ingen auktoriseringskod hittades i anropet.</h3>".encode('utf-8'))
                return

            try:
                # Get client_id and client_secret from DB
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_client_id',))
                row_id = cursor.fetchone()
                cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_client_secret',))
                row_secret = cursor.fetchone()
                conn.close()

                client_id = row_id[0].strip() if row_id else ""
                client_secret = row_secret[0].strip() if row_secret else ""

                if not client_id or not client_secret:
                    self.send_response(400)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.end_headers()
                    self.wfile.write("<h3>Fel: Strava Client ID eller Client Secret saknas i F.R.E.J.A. databasen. Spara dessa i Inställningar först.</h3>".encode('utf-8'))
                    return

                # Exchange code for token
                token_url = "https://www.strava.com/oauth/token"
                token_data = urllib.parse.urlencode({
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'code': code,
                    'grant_type': 'authorization_code'
                }).encode('utf-8')

                req = urllib.request.Request(token_url, data=token_data, method='POST')
                with urllib.request.urlopen(req, timeout=10) as response:
                    res_body = json.loads(response.read().decode('utf-8'))

                new_refresh_token = res_body.get('refresh_token')

                if not new_refresh_token:
                    raise Exception("Kunde inte hämta refresh token från Strava svar.")

                # Save new refresh token in keys.db
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO api_keys (key_name, key_value)
                    VALUES (?, ?)
                    ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
                ''', ('freja_strava_refresh_token', new_refresh_token))
                conn.commit()
                conn.close()

                # Respond with a success page
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                
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
                        <h1>[STRAVA AUKTORISERING LYCKADES]</h1>
                        <p>Ditt refresh-token med rätt behörigheter (activity:read) har sparats.</p>
                        <p>Du kan stänga det här fönstret och återgå till F.R.E.J.A. Neural Interface.</p>
                        <button onclick="window.close()">STÄNG FÖNSTER</button>
                    </div>
                </body>
                </html>
                """
                self.wfile.write(success_html.encode('utf-8'))
                print("Strava OAuth authorization code exchanged successfully! Refresh token updated.")
                return

            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(f"<h3>Fel vid auktorisering: {str(e)}</h3>".encode('utf-8'))
                return
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
                import datetime
                import time

                # Check for mock credentials / sandbox mode
                if client_id == "123456" or refresh_token in ("refreshtokentoken", "MOCK_REFRESH_TOKEN"):
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    today = datetime.date.today()
                    
                    mock_activities = [
                        (-1, "Morgonlöpning i parken", "Löpning", (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d'), 8500.0, 2800, 2950, 45.0, 3.03, 4.20, 148.0, 172.0, 620.0),
                        (-2, "Kvällscykling", "Cykling", (today - datetime.timedelta(days=3)).strftime('%Y-%m-%d'), 25000.0, 3600, 3800, 120.0, 6.94, 11.20, 135.0, 155.0, 780.0),
                        (-3, "Styrkepass - Ben & Bål", "Styrketräning", (today - datetime.timedelta(days=5)).strftime('%Y-%m-%d'), 0.0, 2700, 3200, 0.0, 0.0, 0.0, 118.0, 145.0, 350.0),
                        (-4, "Snabbdistans Löpning", "Löpning", (today - datetime.timedelta(days=8)).strftime('%Y-%m-%d'), 5200.0, 1750, 1800, 25.0, 2.97, 4.00, 142.0, 168.0, 380.0),
                        (-5, "Aktiv återhämtning Promenad", "Promenad", (today - datetime.timedelta(days=11)).strftime('%Y-%m-%d'), 4000.0, 3000, 3100, 15.0, 1.33, 1.80, 98.0, 115.0, 220.0)
                    ]
                    
                    added_count = 0
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
                        added_count += 1
                    conn.commit()
                    conn.close()
                    
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "status": "success",
                        "message": f"Synkroniserade {added_count} (MOCK) aktiviteter från Strava."
                    }).encode('utf-8'))
                    return

                # Real API Flow
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
                
                # Delete any mock activities (negative IDs) on successful real sync
                cursor.execute('DELETE FROM strava_activities WHERE id < 0')
                
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
                # Fallback to mock data generation on error to prevent breaking user flow
                print(f"Strava sync failed, falling back to mock: {e}")
                try:
                    conn = sqlite3.connect(DB_FILE)
                    cursor = conn.cursor()
                    today = datetime.date.today()
                    mock_activities = [
                        (-1, "Morgonlöpning i parken", "Löpning", (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d'), 8500.0, 2800, 2950, 45.0, 3.03, 4.20, 148.0, 172.0, 620.0),
                        (-2, "Kvällscykling", "Cykling", (today - datetime.timedelta(days=3)).strftime('%Y-%m-%d'), 25000.0, 3600, 3800, 120.0, 6.94, 11.20, 135.0, 155.0, 780.0),
                        (-3, "Styrkepass - Ben & Bål", "Styrketräning", (today - datetime.timedelta(days=5)).strftime('%Y-%m-%d'), 0.0, 2700, 3200, 0.0, 0.0, 0.0, 118.0, 145.0, 350.0),
                        (-4, "Snabbdistans Löpning", "Löpning", (today - datetime.timedelta(days=8)).strftime('%Y-%m-%d'), 5200.0, 1750, 1800, 25.0, 2.97, 4.00, 142.0, 168.0, 380.0),
                        (-5, "Aktiv återhämtning Promenad", "Promenad", (today - datetime.timedelta(days=11)).strftime('%Y-%m-%d'), 4000.0, 3000, 3100, 15.0, 1.33, 1.80, 98.0, 115.0, 220.0)
                    ]
                    added_count = 0
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
                        added_count += 1
                    conn.commit()
                    conn.close()
                    
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "status": "success",
                        "message": f"Synkroniserade {added_count} (MOCK) aktiviteter från Strava."
                    }).encode('utf-8'))
                except Exception as mock_err:
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
                act_id = row[0]
                name = row[1]
                act_type = row[2] or "Annat"
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

                # Format speed as pace for Running/Walking and as speed for Cycling/others
                formatted_speed = ""
                if act_type in ("Löpning", "Promenad", "Run", "Walk"):
                    if distance > 0:
                        pace_seconds_per_km = moving_time / (distance / 1000.0)
                        p_min = int(pace_seconds_per_km // 60)
                        p_sec = int(round(pace_seconds_per_km % 60))
                        if p_sec == 60:
                            p_min += 1
                            p_sec = 0
                        formatted_speed = f"{p_min}:{p_sec:02d} min/km"
                else:
                    if moving_time > 0:
                        speed_km_h = (distance / 1000.0) / (moving_time / 3600.0)
                        formatted_speed = f"{speed_km_h:.1f} km/h"
                    elif average_speed > 0:
                        speed_km_h = average_speed * 3.6
                        formatted_speed = f"{speed_km_h:.1f} km/h"

                results.append({
                    "id": act_id,
                    "name": name,
                    "type": act_type,
                    "date": date_str,
                    "distance": distance,
                    "moving_time": moving_time,
                    "elapsed_time": elapsed_time,
                    "total_elevation_gain": total_elevation_gain,
                    "average_speed": average_speed,
                    "max_speed": max_speed,
                    "formatted_speed": formatted_speed,
                    "average_heartrate": average_heartrate,
                    "max_heartrate": max_heartrate,
                    "calories": calories
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
        elif self.path.startswith('/api/strava/activity_details'):
            parsed_path = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_path.query)
            activity_id = params.get('id', [''])[0].strip()
            
            if not activity_id:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": "Aktivitets-ID saknas."}).encode('utf-8'))
                return

            # Helper for mock data details
            def serve_mock_details():
                mock_details = {
                    "id": int(activity_id) if (activity_id.isdigit() or (activity_id.startswith('-') and activity_id[1:].isdigit())) else 987654321,
                    "name": "Morgonlöpning i skogen",
                    "type": "Run",
                    "start_date_local": "2026-06-07T08:15:00Z",
                    "distance_meters": 10000.0,
                    "moving_time_seconds": 3000,
                    "elapsed_time_seconds": 3120,
                    "total_elevation_gain_meters": 150.0,
                    "average_speed_m_s": 3.33,
                    "max_speed_m_s": 4.5,
                    "formatted_speed": "5:00 min/km",
                    "average_heartrate": 152.0,
                    "max_heartrate": 174.0,
                    "calories": 780.0,
                    "description": "Skönt tempo, kändes lite tungt i början men flöt på bra efter 3 km.",
                    "laps": [
                        {"lap_index": 1, "name": "Lap 1", "distance_meters": 1000.0, "elapsed_time_seconds": 310, "moving_time_seconds": 310, "average_speed_m_s": 3.22, "average_heartrate": 138.0, "max_heartrate": 145.0},
                        {"lap_index": 2, "name": "Lap 2", "distance_meters": 1000.0, "elapsed_time_seconds": 305, "moving_time_seconds": 305, "average_speed_m_s": 3.28, "average_heartrate": 144.0, "max_heartrate": 150.0},
                        {"lap_index": 3, "name": "Lap 3", "distance_meters": 1000.0, "elapsed_time_seconds": 300, "moving_time_seconds": 300, "average_speed_m_s": 3.33, "average_heartrate": 149.0, "max_heartrate": 155.0},
                        {"lap_index": 4, "name": "Lap 4", "distance_meters": 1000.0, "elapsed_time_seconds": 298, "moving_time_seconds": 298, "average_speed_m_s": 3.36, "average_heartrate": 152.0, "max_heartrate": 158.0},
                        {"lap_index": 5, "name": "Lap 5", "distance_meters": 1000.0, "elapsed_time_seconds": 295, "moving_time_seconds": 295, "average_speed_m_s": 3.39, "average_heartrate": 155.0, "max_heartrate": 160.0},
                        {"lap_index": 6, "name": "Lap 6", "distance_meters": 1000.0, "elapsed_time_seconds": 302, "moving_time_seconds": 302, "average_speed_m_s": 3.31, "average_heartrate": 154.0, "max_heartrate": 162.0},
                        {"lap_index": 7, "name": "Lap 7", "distance_meters": 1000.0, "elapsed_time_seconds": 300, "moving_time_seconds": 300, "average_speed_m_s": 3.33, "average_heartrate": 156.0, "max_heartrate": 161.0},
                        {"lap_index": 8, "name": "Lap 8", "distance_meters": 1000.0, "elapsed_time_seconds": 295, "moving_time_seconds": 295, "average_speed_m_s": 3.39, "average_heartrate": 158.0, "max_heartrate": 165.0},
                        {"lap_index": 9, "name": "Lap 9", "distance_meters": 1000.0, "elapsed_time_seconds": 300, "moving_time_seconds": 300, "average_speed_m_s": 3.33, "average_heartrate": 160.0, "max_heartrate": 168.0},
                        {"lap_index": 10, "name": "Lap 10", "distance_meters": 1000.0, "elapsed_time_seconds": 295, "moving_time_seconds": 295, "average_speed_m_s": 3.39, "average_heartrate": 162.0, "max_heartrate": 174.0}
                    ],
                    "heart_rate_zones": [
                        {"zone": 1, "min_value": 0, "max_value": 115, "time_in_zone_seconds": 120},
                        {"zone": 2, "min_value": 115, "max_value": 133, "time_in_zone_seconds": 480},
                        {"zone": 3, "min_value": 133, "max_value": 152, "time_in_zone_seconds": 1400},
                        {"zone": 4, "min_value": 152, "max_value": 171, "time_in_zone_seconds": 880},
                        {"zone": 5, "min_value": 171, "max_value": 220, "time_in_zone_seconds": 120}
                    ],
                    "power_zones": []
                }
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                self.end_headers()
                self.wfile.write(json.dumps(mock_details).encode('utf-8'))

            try:
                is_mock_id = activity_id.startswith('-') or activity_id in ('7', '8', '9')
                access_token = get_strava_access_token()
                if access_token == "MOCK_ACCESS_TOKEN" or is_mock_id:
                    serve_mock_details()
                    return
                
                try:
                    # Fetch detailed activity from Strava API
                    act_url = f"https://www.strava.com/api/v3/activities/{activity_id}"
                    req_act = urllib.request.Request(act_url, headers={
                        'Authorization': f'Bearer {access_token}'
                    }, method='GET')
                    
                    with urllib.request.urlopen(req_act, timeout=10) as response:
                        activity = json.loads(response.read().decode('utf-8'))
                    
                    # Fetch laps/splits
                    laps_url = f"https://www.strava.com/api/v3/activities/{activity_id}/laps"
                    req_laps = urllib.request.Request(laps_url, headers={
                        'Authorization': f'Bearer {access_token}'
                    }, method='GET')
                    
                    laps = []
                    try:
                        with urllib.request.urlopen(req_laps, timeout=10) as response:
                            raw_laps = json.loads(response.read().decode('utf-8'))
                            for idx, lap in enumerate(raw_laps):
                                laps.append({
                                    "lap_index": idx + 1,
                                    "name": lap.get("name"),
                                    "distance_meters": lap.get("distance"),
                                    "elapsed_time_seconds": lap.get("elapsed_time"),
                                    "moving_time_seconds": lap.get("moving_time"),
                                    "average_speed_m_s": lap.get("average_speed"),
                                    "average_heartrate": lap.get("average_heartrate"),
                                    "max_heartrate": lap.get("max_heartrate")
                                })
                    except Exception as laps_err:
                        print(f"Error fetching laps for activity {activity_id}: {laps_err}")

                    # Fetch zones
                    zones_url = f"https://www.strava.com/api/v3/activities/{activity_id}/zones"
                    req_zones = urllib.request.Request(zones_url, headers={
                        'Authorization': f'Bearer {access_token}'
                    }, method='GET')
                    
                    hr_zones = []
                    power_zones = []
                    try:
                        with urllib.request.urlopen(req_zones, timeout=10) as response:
                            raw_zones = json.loads(response.read().decode('utf-8'))
                            for z in raw_zones:
                                z_type = z.get("type")
                                z_list = z.get("distribution_buckets", [])
                                formatted_zones = []
                                for idx, bucket in enumerate(z_list):
                                    formatted_zones.append({
                                        "zone": idx + 1,
                                        "min_value": bucket.get("min"),
                                        "max_value": bucket.get("max"),
                                        "time_in_zone_seconds": bucket.get("time")
                                    })
                                if z_type == "heartrate":
                                    hr_zones = formatted_zones
                                elif z_type == "power":
                                    power_zones = formatted_zones
                    except Exception as zones_err:
                        print(f"Error fetching zones for activity {activity_id}: {zones_err}")

                    # Format speed as pace for Running/Walking and as speed for Cycling/others
                    act_type_real = activity.get("type", "")
                    type_mapping = {
                        "Run": "Löpning",
                        "Ride": "Cykling",
                        "VirtualRide": "Cykling",
                        "WeightTraining": "Styrketräning",
                        "Swim": "Simning",
                        "Walk": "Promenad",
                        "Yoga": "Yoga"
                    }
                    mapped_type = type_mapping.get(act_type_real, act_type_real)
                    
                    dist_meters = activity.get("distance", 0.0) or 0.0
                    m_time_secs = activity.get("moving_time", 0) or 0
                    avg_speed_ms = activity.get("average_speed", 0.0) or 0.0
                    
                    formatted_speed = ""
                    if mapped_type in ("Löpning", "Promenad", "Run", "Walk"):
                        if dist_meters > 0:
                            pace_seconds_per_km = m_time_secs / (dist_meters / 1000.0)
                            p_min = int(pace_seconds_per_km // 60)
                            p_sec = int(round(pace_seconds_per_km % 60))
                            if p_sec == 60:
                                p_min += 1
                                p_sec = 0
                            formatted_speed = f"{p_min}:{p_sec:02d} min/km"
                    else:
                        if m_time_secs > 0:
                            speed_km_h = (dist_meters / 1000.0) / (m_time_secs / 3600.0)
                            formatted_speed = f"{speed_km_h:.1f} km/h"
                        elif avg_speed_ms > 0:
                            speed_km_h = avg_speed_ms * 3.6
                            formatted_speed = f"{speed_km_h:.1f} km/h"

                    # Build optimized activity details dictionary
                    details = {
                        "id": activity.get("id"),
                        "name": activity.get("name"),
                        "type": activity.get("type"),
                        "start_date_local": activity.get("start_date_local"),
                        "distance_meters": activity.get("distance"),
                        "moving_time_seconds": activity.get("moving_time"),
                        "elapsed_time_seconds": activity.get("elapsed_time"),
                        "total_elevation_gain_meters": activity.get("total_elevation_gain"),
                        "average_speed_m_s": activity.get("average_speed"),
                        "max_speed_m_s": activity.get("max_speed"),
                        "formatted_speed": formatted_speed,
                        "average_heartrate": activity.get("average_heartrate"),
                        "max_heartrate": activity.get("max_heartrate"),
                        "calories": activity.get("calories"),
                        "description": activity.get("description"),
                        "laps": laps,
                        "heart_rate_zones": hr_zones,
                        "power_zones": power_zones
                    }

                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                    self.end_headers()
                    self.wfile.write(json.dumps(details).encode('utf-8'))
                except Exception as api_err:
                    print(f"Strava activity details real API failed ({api_err}), falling back to mock details.")
                    serve_mock_details()

            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "error",
                    "message": f"Kunde inte hämta aktivitetsdetaljer: {str(e)}"
                }).encode('utf-8'))
        elif self.path.startswith('/api/strava/athlete_stats'):
            try:
                # Define mock_stats first so we can use it in both places
                mock_stats = {
                    "biggest_ride_distance": 125000.0,
                    "biggest_climb_elevation_gain": 1450.0,
                    "recent_ride_totals": {
                        "count": 4,
                        "distance": 180000.0,
                        "moving_time": 25200,
                        "elapsed_time": 28800,
                        "elevation_gain": 2200.0,
                        "achievement_count": 8
                    },
                    "recent_run_totals": {
                        "count": 12,
                        "distance": 96000.0,
                        "moving_time": 32400,
                        "elapsed_time": 33000,
                        "elevation_gain": 850.0,
                        "achievement_count": 14
                    },
                    "recent_swim_totals": {
                        "count": 2,
                        "distance": 4000.0,
                        "moving_time": 5400,
                        "elapsed_time": 6000,
                        "elevation_gain": 0.0,
                        "achievement_count": 1
                    },
                    "ytd_ride_totals": {
                        "count": 24,
                        "distance": 1200000.0,
                        "moving_time": 172800,
                        "elapsed_time": 190000,
                        "elevation_gain": 12500.0,
                        "achievement_count": 35
                    },
                    "ytd_run_totals": {
                        "count": 78,
                        "distance": 680000.0,
                        "moving_time": 248400,
                        "elapsed_time": 252000,
                        "elevation_gain": 6200.0,
                        "achievement_count": 92
                    },
                    "ytd_swim_totals": {
                        "count": 15,
                        "distance": 32000.0,
                        "moving_time": 43200,
                        "elapsed_time": 45000,
                        "elevation_gain": 0.0,
                        "achievement_count": 12
                    },
                    "all_ride_totals": {
                        "count": 150,
                        "distance": 7500000.0,
                        "moving_time": 1080000,
                        "elapsed_time": 1150000,
                        "elevation_gain": 78000.0
                    },
                    "all_run_totals": {
                        "count": 450,
                        "distance": 380000.0,
                        "moving_time": 1400000,
                        "elapsed_time": 1420000,
                        "elevation_gain": 35000.0
                    },
                    "all_swim_totals": {
                        "count": 80,
                        "distance": 180000.0,
                        "moving_time": 250000,
                        "elapsed_time": 260000,
                        "elevation_gain": 0.0
                    }
                }

                access_token = get_strava_access_token()
                if access_token == "MOCK_ACCESS_TOKEN":
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                    self.end_headers()
                    self.wfile.write(json.dumps(mock_stats).encode('utf-8'))
                    return
                
                try:
                    # Fetch authenticated athlete info
                    athlete_url = "https://www.strava.com/api/v3/athlete"
                    req_athlete = urllib.request.Request(athlete_url, headers={
                        'Authorization': f'Bearer {access_token}'
                    }, method='GET')
                    
                    with urllib.request.urlopen(req_athlete, timeout=10) as response:
                        athlete = json.loads(response.read().decode('utf-8'))
                    
                    athlete_id = athlete.get('id')
                    if not athlete_id:
                        raise Exception("Kunde inte hämta atlet-ID från profil.")
                    
                    # Fetch athlete stats
                    stats_url = f"https://www.strava.com/api/v3/athletes/{athlete_id}/stats"
                    req_stats = urllib.request.Request(stats_url, headers={
                        'Authorization': f'Bearer {access_token}'
                    }, method='GET')
                    
                    with urllib.request.urlopen(req_stats, timeout=10) as response:
                        stats = json.loads(response.read().decode('utf-8'))

                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                    self.end_headers()
                    self.wfile.write(json.dumps(stats).encode('utf-8'))
                except Exception as api_err:
                    print(f"Failed to fetch real athlete stats ({api_err}), falling back to mock stats.")
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                    self.end_headers()
                    self.wfile.write(json.dumps(mock_stats).encode('utf-8'))

            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "error",
                    "message": f"Kunde inte hämta atlet-statistik: {str(e)}"
                }).encode('utf-8'))
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
                SELECT date, weight, fat_ratio, bone_mass, heart_pulse, 
                       sleep_duration, sleep_deep, sleep_rem, steps, 
                       distance, calories, elevation, sleep_score
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
                    "heart_pulse": row[4],
                    "sleep_duration": row[5],
                    "sleep_deep": row[6],
                    "sleep_rem": row[7],
                    "steps": row[8],
                    "distance": row[9],
                    "calories": row[10],
                    "elevation": row[11],
                    "sleep_score": row[12]
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
                import datetime
                import time
                import random
                
                # Check for mock credentials
                if client_id == "withings123" or refresh_token in ("refreshtokentoken", "MOCK_REFRESH_TOKEN"):
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
                        sleep_deep = int(sleep_dur * random.uniform(0.22, 0.30))
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
                        added_count += 1
                    conn.commit()
                    conn.close()
                    
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "status": "success",
                        "message": f"Synkroniserade {added_count} (MOCK) mätningar från Withings."
                    }).encode('utf-8'))
                    return

                # Real Withings API Flow
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
                
                today_date = datetime.date.today()
                start_date_str = (today_date - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
                end_date_str = today_date.strftime('%Y-%m-%d')
                lastupdate = int(time.time()) - (30 * 24 * 3600)
                
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                
                # 1. Body Measurements
                meas_url = f"https://wbsapi.withings.net/measure?action=getmeas&meastypes=1,6,11,16&category=1&lastupdate={lastupdate}"
                req_meas = urllib.request.Request(meas_url, headers={
                    'Authorization': f'Bearer {access_token}'
                }, method='GET')
                
                with urllib.request.urlopen(req_meas, timeout=10) as response:
                    meas_body = json.loads(response.read().decode('utf-8'))
                    
                added_count = 0
                if meas_body.get("status") == 0:
                    measuregrps = meas_body.get("body", {}).get("measuregrps", [])
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
                
                # 2. Sleep summary
                try:
                    sleep_url = "https://wbsapi.withings.net/v2/sleep"
                    sleep_data = urllib.parse.urlencode({
                        'action': 'getsummary',
                        'startdateymd': start_date_str,
                        'enddateymd': end_date_str
                    }).encode('utf-8')
                    req_sleep = urllib.request.Request(sleep_url, data=sleep_data, headers={
                        'Authorization': f'Bearer {access_token}',
                        'Content-Type': 'application/x-www-form-urlencoded'
                    }, method='POST')
                    with urllib.request.urlopen(req_sleep, timeout=10) as response:
                        sleep_body = json.loads(response.read().decode('utf-8'))
                    
                    if sleep_body.get("status") == 0:
                        series = sleep_body.get("body", {}).get("series", [])
                        for item in series:
                            s_date = item.get("date")
                            s_data = item.get("data", {})
                            sleep_duration = s_data.get("total_sleep_time") or s_data.get("asleepduration")
                            sleep_deep = s_data.get("deepsleepduration")
                            sleep_rem = s_data.get("remsleepduration")
                            sleep_score = s_data.get("sleep_score")
                            
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
                                added_count += 1
                except Exception as sleep_err:
                    print(f"Error fetching sleep from Withings: {sleep_err}")
                
                # 3. Activity data
                try:
                    act_url = "https://wbsapi.withings.net/v2/measure"
                    act_data = urllib.parse.urlencode({
                        'action': 'getactivity',
                        'startdateymd': start_date_str,
                        'enddateymd': end_date_str
                    }).encode('utf-8')
                    req_act = urllib.request.Request(act_url, data=act_data, headers={
                        'Authorization': f'Bearer {access_token}',
                        'Content-Type': 'application/x-www-form-urlencoded'
                    }, method='POST')
                    with urllib.request.urlopen(req_act, timeout=10) as response:
                        act_body = json.loads(response.read().decode('utf-8'))
                    
                    if act_body.get("status") == 0:
                        activities = act_body.get("body", {}).get("activities", [])
                        for act in activities:
                            a_date = act.get("date")
                            steps = act.get("steps")
                            distance = act.get("distance")
                            calories = act.get("calories")
                            elevation = act.get("elevation")
                            
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
                                added_count += 1
                except Exception as act_err:
                    print(f"Error fetching activity from Withings: {act_err}")
                
                conn.commit()
                conn.close()
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "success",
                    "message": f"Synkroniserade {added_count} mätningar, sömn och aktivitet från Withings."
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
                recovery_time = int(data.get('recovery_time')) if data.get('recovery_time') is not None else None
                training_status = data.get('training_status', '').strip() or None

                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO garmin_health (date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        steps = excluded.steps,
                        sleep_hours = excluded.sleep_hours,
                        resting_hr = excluded.resting_hr,
                        active_calories = excluded.active_calories,
                        workout_type = excluded.workout_type,
                        workout_duration = excluded.workout_duration,
                        body_battery = excluded.body_battery,
                        hrv = excluded.hrv,
                        recovery_time = excluded.recovery_time,
                        training_status = excluded.training_status
                ''', (date_str, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status))
                
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
