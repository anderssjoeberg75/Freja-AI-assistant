"""AI Personal Trainer routes using FastAPI."""

import datetime
import httpx
import json
import urllib.parse
from fastapi import APIRouter, HTTPException, Query, Request
from backend.database import get_db_connection, get_api_key

router = APIRouter()

# --- Configuration constants -------------------------------------------------
GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_LOCATION = "Stockholm"
RHR_ALERT_PCT = 5.0        # Resting HR increase that warrants an "ease off" nudge
HRV_ALERT_PCT = -10.0      # HRV drop that warrants an "ease off" nudge
DEFAULT_WORKOUT_HOUR = 8   # Preferred workout start hour (local time)
DAY_END_HOUR = 21          # Latest a workout may be auto-scheduled to end
MAX_WORKOUT_MINUTES = 180  # Sanity cap for a single booked session
MAX_INPUT_LEN = 2000       # Cap on free-text goal/limitations sent to the LLM

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Europe/Stockholm")
except Exception:  # pragma: no cover - tzdata may be missing on some hosts
    LOCAL_TZ = None


def today_local() -> datetime.date:
    """Today's date in the app's configured timezone (falls back to server time)."""
    if LOCAL_TZ is not None:
        return datetime.datetime.now(LOCAL_TZ).date()
    return datetime.date.today()


def _dict_row(cursor, row):
    """sqlite row factory returning a plain dict."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_trainer_profile() -> dict:
    """Returns the single training profile row as a dict, or {} if none is set."""
    with get_db_connection() as conn:
        conn.row_factory = _dict_row
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM trainer_profile WHERE id = 1")
            row = cursor.fetchone()
        except Exception:
            row = None
    return dict(row) if row else {}


# In-memory weather cache keyed by (location, date) so repeated check-ins/plan
# generations on the same day don't re-hit the forecast API.
_weather_cache: dict = {}


async def fetch_7day_weather_forecast(location: str = DEFAULT_LOCATION) -> str:
    """Cached wrapper around the 7-day forecast (cache lives for the current day)."""
    key = ((location or DEFAULT_LOCATION).strip().lower(), today_local().isoformat())
    cached = _weather_cache.get(key)
    if cached is not None:
        return cached
    result = await _fetch_7day_weather_forecast_raw(location)
    # Only cache successful lookups so a transient error can be retried.
    if result and not result.startswith("Misslyckades") and not result.startswith("Kunde inte hitta"):
        _weather_cache[key] = result
    return result


async def _fetch_7day_weather_forecast_raw(location: str = DEFAULT_LOCATION) -> str:
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=sv&format=json"
        async with httpx.AsyncClient() as client:
            res = await client.get(geo_url, timeout=8.0)
            res.raise_for_status()
            geo_data = res.json()
            
        results = geo_data.get('results')
        if not results:
            return f"Kunde inte hitta platsen: '{location}' för väderprognos."
        
        first = results[0]
        lat = first['latitude']
        lon = first['longitude']
        name = first['name']
        country = first.get('country', '')
        
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,precipitation_sum,relative_humidity_2m_max,relative_humidity_2m_min&timezone=auto"
        async with httpx.AsyncClient() as client:
            res = await client.get(weather_url, timeout=8.0)
            res.raise_for_status()
            weather_data = res.json()
            
        daily = weather_data.get('daily')
        if not daily:
            return "Ingen väderprognos returnerades."
            
        wmo_codes = {
            0: "Klart väder och molnfritt",
            1: "Mestadels klart",
            2: "Växlande molnighet",
            3: "Mulet",
            45: "Dimma",
            48: "Rimfrost-dimma",
            51: "Lätt duggregn",
            53: "Måttligt duggregn",
            55: "Tätt duggregn",
            61: "Lätt regn",
            63: "Måttligt regn",
            65: "Kraftigt regn",
            71: "Lätt snöfall",
            73: "Måttligt snöfall",
            75: "Kraftigt snöfall",
            77: "Snökorn",
            80: "Lätta regnskurar",
            81: "Måttliga regnskurar",
            82: "Kraftiga regnskurar",
            85: "Lätta snöskurar",
            86: "Kraftiga snöskurar",
            95: "Åska",
            96: "Åska med lätt hagel",
            99: "Åska med kraftigt hagel"
        }
        
        lines = [f"Väderprognos för {name}, {country} de kommande 7 dagarna:"]
        times = daily.get('time', [])
        for i in range(len(times)):
            date_str = times[i]
            w_code = daily.get('weather_code', [0])[i]
            temp_max = daily.get('temperature_2m_max', [0.0])[i]
            temp_min = daily.get('temperature_2m_min', [0.0])[i]
            app_max = daily.get('apparent_temperature_max', [0.0])[i]
            app_min = daily.get('apparent_temperature_min', [0.0])[i]
            precip = daily.get('precipitation_sum', [0.0])[i]
            rh_max = daily.get('relative_humidity_2m_max', [0.0])[i]
            rh_min = daily.get('relative_humidity_2m_min', [0.0])[i]
            
            desc = wmo_codes.get(w_code, "Atmosfäriska fluktuationer")
            lines.append(
                f"- {date_str}: {desc}, Temp: {temp_min}°C till {temp_max}°C (Känns som: {app_min}°C till {app_max}°C), Nederbörd: {precip}mm, Luftfuktighet: {rh_min}% till {rh_max}%"
            )
            
        return "\n".join(lines)
    except Exception as e:
        return f"Misslyckades att hämta väderprognos för {location}: {str(e)}"

def calculate_trends():
    """Compares the last 7 days vs the preceding 14 days for resting HR and HRV.

    Resting HR is read from a single consistent source (Garmin preferred, Withings
    fallback) so the recent and baseline averages are never mixed across devices,
    which would otherwise make the percentage change meaningless. HRV is Garmin-only.
    """
    garmin_rows = []
    withings_rows = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT resting_hr, hrv FROM garmin_health ORDER BY date DESC LIMIT 21')
            garmin_rows = cursor.fetchall()
        except Exception as e:
            print(f"Error fetching Garmin health data for trends: {e}")
        try:
            cursor.execute('SELECT heart_pulse FROM withings_measurements ORDER BY date DESC LIMIT 21')
            withings_rows = cursor.fetchall()
        except Exception as e:
            print(f"Error fetching Withings measurements for trends: {e}")

    def _avg(vals):
        return sum(vals) / len(vals) if vals else None

    # Resting HR: pick one source that has BOTH a recent and a baseline window.
    g_recent_rhr = [r[0] for r in garmin_rows[:7] if r[0] is not None]
    g_base_rhr = [r[0] for r in garmin_rows[7:] if r[0] is not None]
    w_recent_rhr = [r[0] for r in withings_rows[:7] if r[0] is not None]
    w_base_rhr = [r[0] for r in withings_rows[7:] if r[0] is not None]

    if g_recent_rhr and g_base_rhr:
        recent_rhrs, baseline_rhrs = g_recent_rhr, g_base_rhr
    elif w_recent_rhr and w_base_rhr:
        recent_rhrs, baseline_rhrs = w_recent_rhr, w_base_rhr
    else:
        # Not enough for a valid comparison from a single source; expose what exists.
        recent_rhrs = g_recent_rhr or w_recent_rhr
        baseline_rhrs = g_base_rhr or w_base_rhr

    # HRV: Garmin only (Withings does not provide it).
    recent_hrvs = [r[1] for r in garmin_rows[:7] if r[1] is not None]
    baseline_hrvs = [r[1] for r in garmin_rows[7:] if r[1] is not None]

    rhr_recent_avg = _avg(recent_rhrs)
    rhr_baseline_avg = _avg(baseline_rhrs)
    hrv_recent_avg = _avg(recent_hrvs)
    hrv_baseline_avg = _avg(baseline_hrvs)

    rhr_change_pct = None
    if rhr_recent_avg and rhr_baseline_avg:
        rhr_change_pct = ((rhr_recent_avg - rhr_baseline_avg) / rhr_baseline_avg) * 100

    hrv_change_pct = None
    if hrv_recent_avg and hrv_baseline_avg:
        hrv_change_pct = ((hrv_recent_avg - hrv_baseline_avg) / hrv_baseline_avg) * 100

    return {
        "rhr_recent_avg": rhr_recent_avg,
        "rhr_baseline_avg": rhr_baseline_avg,
        "rhr_change_pct": rhr_change_pct,
        "hrv_recent_avg": hrv_recent_avg,
        "hrv_baseline_avg": hrv_baseline_avg,
        "hrv_change_pct": hrv_change_pct
    }


def format_trends_summary(trends: dict) -> str:
    """Renders the RHR/HRV trend dict as the Swedish summary used in LLM prompts."""
    lines = []
    if trends["rhr_recent_avg"] is not None:
        recent_str = f"{trends['rhr_recent_avg']:.1f}"
        baseline_str = f"{trends['rhr_baseline_avg']:.1f}" if trends["rhr_baseline_avg"] is not None else "N/A"
        change_str = f"{trends['rhr_change_pct']:.1f}%" if trends["rhr_change_pct"] is not None else "N/A"
        lines.append(f"Vilopuls (RHR): Senaste 7 dgr snitt: {recent_str} BPM, Baslinje (föregående 14 dgr): {baseline_str} BPM (Förändring: {change_str})")
    if trends["hrv_recent_avg"] is not None:
        recent_str = f"{trends['hrv_recent_avg']:.1f}"
        baseline_str = f"{trends['hrv_baseline_avg']:.1f}" if trends["hrv_baseline_avg"] is not None else "N/A"
        change_str = f"{trends['hrv_change_pct']:.1f}%" if trends["hrv_change_pct"] is not None else "N/A"
        lines.append(f"HRV: Senaste 7 dgr snitt: {recent_str} ms, Baslinje (föregående 14 dgr): {baseline_str} ms (Förändring: {change_str})")
    return "\n".join(lines) if lines else "Inga tillräckliga trenddata (RHR/HRV) tillgängliga."


def compute_adherence(days: int = 14) -> dict:
    """Compares booked workout dates against completed Strava activity dates."""
    today = today_local()
    start_str = (today - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    today_str = today.strftime('%Y-%m-%d')

    planned_dates = set()
    completed_dates = set()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                'SELECT DISTINCT workout_date FROM trainer_bookings WHERE workout_date >= ? AND workout_date <= ?',
                (start_str, today_str)
            )
            planned_dates = {r[0] for r in cursor.fetchall() if r[0]}
        except Exception as e:
            print(f"Error fetching bookings for adherence: {e}")
        try:
            cursor.execute(
                'SELECT DISTINCT SUBSTR(date, 1, 10) FROM strava_activities WHERE SUBSTR(date, 1, 10) >= ? AND SUBSTR(date, 1, 10) <= ?',
                (start_str, today_str)
            )
            completed_dates = {r[0] for r in cursor.fetchall() if r[0]}
        except Exception as e:
            print(f"Error fetching activities for adherence: {e}")

    planned = len(planned_dates)
    completed = len(planned_dates & completed_dates)
    missed = sorted(planned_dates - completed_dates)
    adherence_pct = round(completed / planned * 100, 1) if planned else None
    return {
        "window_days": days,
        "planned": planned,
        "completed": completed,
        "adherence_pct": adherence_pct,
        "planned_dates": sorted(planned_dates),
        "missed_dates": missed
    }


@router.get("/api/trainer/profile")
async def get_trainer_profile_endpoint():
    """Returns the stored training profile (empty object if not yet set)."""
    try:
        return get_trainer_profile()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/trainer/profile")
async def put_trainer_profile(request: Request):
    """Creates or updates the single training profile row."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    fields = [
        "event", "event_date", "fitness_level", "availability", "goals",
        "limitations", "location", "baseline_resting_hr", "baseline_sleep_hours",
        "baseline_hrv", "auto_adjust"
    ]
    text_fields = {"event", "event_date", "fitness_level", "availability", "goals", "limitations", "location"}

    values = {}
    for f in fields:
        if f in body and body[f] is not None:
            val = body[f]
            if f in text_fields:
                val = str(val).strip()[:MAX_INPUT_LEN]
            elif f == "auto_adjust":
                val = 1 if val in (True, 1, "1", "true", "True", "on") else 0
            values[f] = val

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM trainer_profile WHERE id = 1")
            exists = cursor.fetchone() is not None
            now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            if exists:
                if values:
                    set_clause = ", ".join(f"{k} = ?" for k in values)
                    params = list(values.values()) + [now_str]
                    cursor.execute(f"UPDATE trainer_profile SET {set_clause}, updated_at = ? WHERE id = 1", params)
                else:
                    cursor.execute("UPDATE trainer_profile SET updated_at = ? WHERE id = 1", (now_str,))
            else:
                cols = ["id"] + list(values.keys()) + ["updated_at"]
                placeholders = ", ".join("?" for _ in cols)
                params = [1] + list(values.values()) + [now_str]
                cursor.execute(f"INSERT INTO trainer_profile ({', '.join(cols)}) VALUES ({placeholders})", params)
            conn.commit()
        return {"status": "success", "message": "Träningsprofil sparad.", "profile": get_trainer_profile()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/trainer/adherence")
async def get_trainer_adherence(days: int = Query(14, description="Lookback window in days")):
    """Returns planned vs completed workout adherence over the given window."""
    try:
        return compute_adherence(days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/trainer/plans")
async def get_trainer_plans(limit: int = Query(20, description="Number of plans to retrieve")):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, date, goal, advice_text, limitations 
                FROM trainer_plans 
                ORDER BY date DESC, id DESC
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
        
        results = []
        for row in rows:
            results.append({
                'id': row[0],
                'date': row[1],
                'goal': row[2],
                'advice_text': row[3],
                'limitations': row[4]
            })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/trainer/plans")
async def delete_trainer_plan(plan_id: int = Query(..., description="ID of the plan to delete")):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM trainer_plans WHERE id = ?', (plan_id,))
            conn.commit()
        return {'status': 'success', 'message': f"Träningsprogram med ID {plan_id} har raderats."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/trainer/generate")
async def generate_trainer_plan(request: Request):
    try:
        body = await request.json()
        goal = body.get("goal", "").strip()[:MAX_INPUT_LEN]
        limitations = body.get("limitations", "").strip()[:MAX_INPUT_LEN]
        if not goal:
            raise HTTPException(status_code=400, detail="Mål saknas.")

        # Fall back to the stored training profile for limitations/location.
        profile = get_trainer_profile()
        if not limitations and profile.get("limitations"):
            limitations = str(profile["limitations"]).strip()[:MAX_INPUT_LEN]
        location = (body.get("location") or profile.get("location") or DEFAULT_LOCATION)
        location = str(location).strip() or DEFAULT_LOCATION

        # 1-3. Fetch Garmin / Strava / Withings logs (single connection).
        garmin_summary = []
        strava_summary = []
        withings_summary = []
        with get_db_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('''
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status
                FROM garmin_health
                ORDER BY date DESC
                LIMIT 7
            ''')
            for r in cursor.fetchall():
                garmin_summary.append(
                    f"Datum: {r[0]}, Steg: {r[1]}, Sömn: {r[2]}h, Vilopuls: {r[3]}, Kalorier: {r[4]}kcal, Träning: {r[5]} ({r[6]} min), Body Battery: {r[7]}, HRV: {r[8]}ms, Återhämtningstid: {r[9]}h, Status: {r[10]}"
                )

            cursor.execute('''
                SELECT name, type, date, distance, moving_time, total_elevation_gain, average_heartrate, max_heartrate, calories
                FROM strava_activities
                ORDER BY date DESC
                LIMIT 7
            ''')
            for r in cursor.fetchall():
                dist_km = round(r[3] / 1000.0, 2) if r[3] else 0
                dur_min = round(r[4] / 60.0, 1) if r[4] else 0
                strava_summary.append(
                    f"Aktivitet: {r[0]}, Typ: {r[1]}, Datum: {r[2]}, Distans: {dist_km} km, Tid: {dur_min} min, Höjdmeter: {r[5]}m, Snittpuls: {r[6]}, Maxpuls: {r[7]}, Kalorier: {r[8]}kcal"
                )

            cursor.execute('''
                SELECT date, weight, fat_ratio, bone_mass, heart_pulse, sleep_duration, steps, calories, sleep_score
                FROM withings_measurements
                ORDER BY date DESC
                LIMIT 7
            ''')
            for r in cursor.fetchall():
                sleep_h = round(r[5] / 3600.0, 1) if r[5] else 0
                withings_summary.append(
                    f"Datum: {r[0]}, Vikt: {r[1]} kg, Fettprocent: {r[2]}%, Benmassa: {r[3]} kg, Puls: {r[4]} BPM, Sömn: {sleep_h}h (Score: {r[8]}), Steg: {r[6]}, Kalorier: {r[7]}kcal"
                )

        # 4. Fetch Gemini API key
        api_key = get_api_key('freja_gemini_apikey') or ""
        if not api_key:
            raise HTTPException(status_code=400, detail="Gemini API-nyckel är inte konfigurerad på serveren.")

        # 5. Calculate trends
        trends = calculate_trends()
        trends_data_str = format_trends_summary(trends)

        # 5.5 Fetch 7-day weather forecast (for the profile's location)
        weather_forecast = await fetch_7day_weather_forecast(location)

        # 6. Compile Prompt
        garmin_data_str = "\n".join(garmin_summary) if garmin_summary else "Ingen Garmin-data tillgänglig."
        strava_data_str = "\n".join(strava_summary) if strava_summary else "Ingen Strava-data tillgänglig."
        withings_data_str = "\n".join(withings_summary) if withings_summary else "Ingen Withings-data tillgänglig."

        limitations_prompt = f'\nSKADOR / SJUKDOMAR / BEGRÄNSNINGAR:\n"{limitations}"\nTa särskild hänsyn till dessa begränsningar, skador eller sjukdomar (t.ex. ansträngningsastma, knäskador etc.) och anpassa övningsval samt intensitet därefter.' if limitations else ""

        prompt_content = f"""
Du är en professionell personlig tränare och hälsocoach (COACH AI) integrerad i F.R.E.J.A.-systemet.
Analysera följande hälsodata, träningsdata, trender och väderprognos för användaren och skapa ett anpassat träningsprogram eller konkreta träningstips baserat på deras uppgivna mål.

MÅL: "{goal}"{limitations_prompt}

[BERÄKNADE HÄLSOTRENDER (RHR & HRV)]:
{trends_data_str}

[VÄDERPROGNOS FÖR DE KOMMANDE 7 DAGARNA]:
{weather_forecast}

[GARMIN HÄLSODATA (Senaste 7 dagarna)]:
{garmin_data_str}

[STRAVA TRÄNINGSPASS (Senaste 7 passen)]:
{strava_data_str}

[WITHINGS MÄTNINGAR (Senaste 7 mätningarna)]:
{withings_data_str}

Instruktioner för svaret:
- Svara på svenska.
- Skriv på ett peppande, professionellt och coachande sätt (F.R.E.J.A.-stil: artig men extremt kunnig).
- Ge konkreta, handfasta råd om träningsintensitet, återhämtning (titta på sömn och HRV/recovery om det finns), och träningsform baserat på datan.
- Analysera den kommande veckans väderprognos när du planerar träningspassen:
  - Om det väntas dåligt väder (t.ex. kraftigt regn, snöfall, åska eller storm) på en planerad träningsdag, rekommendera inomhusträning eller vila för den dagen.
  - Om användaren har astma-relaterade åkommor (som "astma" eller "ansträngningsastma" i sina begränsningar), ta extra hänsyn till dagar med extra kallt väder (t.ex. upplevd temperatur under 0°C) kombinerat med låg luftfuktighet/torr luft, och rekommendera inomhusträning eller lägre intensitet för att minska risken för astmabesvär.
- Analysera hälsotrenderna. Om vilopulsen ökat markant (>5%) eller HRV sjunkit markant (<-10%), lägg till en tydlig rekommendation om aktiv vila eller minskad intensitet.
- Skapa ett enkelt veckoprogram som användaren kan följa direkt.
"""

        # 7. Call Gemini
        google_url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt_content}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 2000,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "summary": {
                            "type": "STRING",
                            "description": "En sammanfattande analys av användarens hälsostatus och träningshistorik på svenska."
                        },
                        "resting_hr_trend": {
                            "type": "STRING",
                            "description": "Analys av vilopulsens trend (t.ex. om den ökat och tyder på trötthet, eller om den är stabil)."
                        },
                        "hrv_trend": {
                            "type": "STRING",
                            "description": "Analys av HRV trend (t.ex. om den sjunkit och indikerar under-återhämtning, eller om den är god)."
                        },
                        "weekly_focus": {
                            "type": "STRING",
                            "description": "Det övergripande fokuset för denna träningsvecka baserat på målet och eventuella begränsningar."
                        },
                        "workouts": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "day": {
                                        "type": "STRING",
                                        "description": "Dagen för passet (måste vara en av: Måndag, Tisdag, Onsdag, Torsdag, Fredag, Lördag, Söndag)."
                                    },
                                    "activity_type": {
                                        "type": "STRING",
                                        "description": "Aktivitetstyp (t.ex. Löpning, Styrketräning, Cykling, Yoga, Vila)."
                                    },
                                    "title": {
                                        "type": "STRING",
                                        "description": "Kort beskrivande titel på passet."
                                    },
                                    "description": {
                                        "type": "STRING",
                                        "description": "Detaljerade instruktioner för träningspasset på svenska."
                                    },
                                    "duration_minutes": {
                                        "type": "INTEGER",
                                        "description": "Uppskattad tid i minuter (0 för vila)."
                                    }
                                },
                                "required": ["day", "activity_type", "title", "description", "duration_minutes"]
                            }
                        }
                    },
                    "required": ["summary", "resting_hr_trend", "hrv_trend", "weekly_focus", "workouts"]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(google_url, json=payload, timeout=30.0)
            response.raise_for_status()
            res_json = response.json()

        advice_text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not advice_text:
            raise HTTPException(status_code=500, detail="Kunde inte generera svar från Gemini.")

        # 8. Save to database
        today_str = today_local().strftime('%Y-%m-%d')
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trainer_plans (date, goal, advice_text, limitations)
                VALUES (?, ?, ?, ?)
            ''', (today_str, goal, advice_text, limitations))
            conn.commit()
            plan_id = cursor.lastrowid

        return {
            "status": "success",
            "plan_id": plan_id,
            "date": today_str,
            "goal": goal,
            "limitations": limitations,
            "advice_text": advice_text
        }
        
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API fel: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/api/trainer/plans")
async def update_trainer_plan(request: Request):
    try:
        body = await request.json()
        plan_id = body.get("plan_id")
        advice_text = body.get("advice_text")
        if not plan_id or advice_text is None:
            raise HTTPException(status_code=400, detail="Plan-ID och uppdaterat träningsprogram krävs.")
            
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE trainer_plans 
                SET advice_text = ? 
                WHERE id = ?
            ''', (advice_text, plan_id))
            conn.commit()
            
        return {"status": "success", "message": "Träningsprogrammet uppdaterades framgångsrikt."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Shared workout-event helpers -------------------------------------------
# Markers that identify an auto-scheduled F.R.E.J.A. PT session in the calendar,
# so recovery-driven adjustments only ever touch training events (never meetings).
WORKOUT_LOCATION_MARKER = "F.R.E.J.A. PT"
WORKOUT_SUMMARY_MARKERS = ("💪", "🏃", "🚶", "🚴", "🧘", "🏊")


def is_workout_event(ev: dict) -> bool:
    """True if a calendar event looks like a F.R.E.J.A. PT training session."""
    summary = ev.get("summary") or ""
    location_val = ev.get("location") or ""
    return (WORKOUT_LOCATION_MARKER in location_val) or any(
        marker in summary for marker in WORKOUT_SUMMARY_MARKERS
    )


def _event_duration_minutes(ev: dict) -> int:
    """Minutes between an event's start and end (0 if unparseable / all-day)."""
    try:
        s = datetime.datetime.strptime((ev.get("start_time") or "")[:16], "%Y-%m-%dT%H:%M")
        e = datetime.datetime.strptime((ev.get("end_time") or "")[:16], "%Y-%m-%dT%H:%M")
        return max(0, int((e - s).total_seconds() // 60))
    except Exception:
        return 0


@router.post("/api/trainer/checkin")
async def trainer_daily_checkin(request: Request):
    """Daily morning check-in (COACH AI): reads last night's Garmin/Withings data,
    checks today's calendar workout, verifies if yesterday's session was completed on
    Strava, weighs in the weather, and returns a short coaching briefing in Swedish."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        profile = get_trainer_profile()
        location = (body.get("location") or profile.get("location") or DEFAULT_LOCATION)
        location = str(location).strip() or DEFAULT_LOCATION

        today = today_local()
        today_str = today.strftime('%Y-%m-%d')
        yesterday_str = (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d')

        # 1. Latest Garmin snapshot (most recent night)
        garmin_snapshot = "Ingen Garmin-data tillgänglig."
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status
                FROM garmin_health
                ORDER BY date DESC
                LIMIT 1
            ''')
            g = cursor.fetchone()
        if g:
            garmin_snapshot = (
                f"Datum: {g[0]}, Steg: {g[1]}, Sömn: {g[2]}h, Vilopuls: {g[3]}, Kalorier: {g[4]}kcal, "
                f"Träning: {g[5]} ({g[6]} min), Body Battery: {g[7]}, HRV: {g[8]}ms, "
                f"Återhämtningstid: {g[9]}h, Status: {g[10]}"
            )

        # 2. Latest Withings snapshot (fallback for sleep/RHR + body composition)
        withings_snapshot = "Ingen Withings-data tillgänglig."
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, weight, fat_ratio, bone_mass, heart_pulse, sleep_duration, steps, calories, sleep_score
                FROM withings_measurements
                ORDER BY date DESC
                LIMIT 1
            ''')
            w = cursor.fetchone()
        if w:
            sleep_h = round(w[5] / 3600.0, 1) if w[5] else 0
            withings_snapshot = (
                f"Datum: {w[0]}, Vikt: {w[1]} kg, Fettprocent: {w[2]}%, Puls: {w[4]} BPM, "
                f"Sömn: {sleep_h}h (Score: {w[8]}), Steg: {w[6]}, Kalorier: {w[7]}kcal"
            )

        # 3. Did yesterday's workout get completed? (Strava)
        completed_summary = "Inget registrerat träningspass igår på Strava."
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT name, type, distance, moving_time, average_heartrate
                FROM strava_activities
                WHERE SUBSTR(date, 1, 10) = ?
                ORDER BY date DESC
            ''', (yesterday_str,))
            strava_rows = cursor.fetchall()
        if strava_rows:
            parts = []
            for r in strava_rows:
                dist_km = round(r[2] / 1000.0, 2) if r[2] else 0
                dur_min = round(r[3] / 60.0, 1) if r[3] else 0
                parts.append(f"{r[0]} ({r[1]}, {dist_km} km, {dur_min} min, snittpuls {r[4]})")
            completed_summary = "Genomfört igår: " + "; ".join(parts)

        # 4. Today's calendar: separate planned workouts from other commitments
        from backend.routes.google_calendar import core_get_calendar_data
        todays_events = [e for e in core_get_calendar_data(days=1) if (e.get("start_time") or "")[:10] == today_str]

        workout_events = [e for e in todays_events if is_workout_event(e)]
        other_events = [e for e in todays_events if not is_workout_event(e)]

        if workout_events:
            todays_plan_str = "\n".join(
                f"- {e.get('summary', '')} ({(e.get('start_time') or '')[11:16]}–{(e.get('end_time') or '')[11:16]}): {e.get('description', '')}"
                for e in workout_events
            )
        else:
            todays_plan_str = "Inget träningspass är inbokat i kalendern för idag."

        other_events_str = "\n".join(
            f"- {e.get('summary', '')} ({(e.get('start_time') or '')[11:16]}–{(e.get('end_time') or '')[11:16]})"
            for e in other_events
        ) if other_events else "Inga andra åtaganden i kalendern idag."

        # 5. Calculated RHR/HRV trends + adherence (reuses plan-generation logic)
        trends = calculate_trends()
        trends_data_str = format_trends_summary(trends)

        adherence = compute_adherence(14)
        if adherence["adherence_pct"] is not None:
            adherence_str = (
                f"Senaste {adherence['window_days']} dgr: {adherence['completed']} av "
                f"{adherence['planned']} inbokade pass genomförda ({adherence['adherence_pct']}%)."
            )
        else:
            adherence_str = "Ingen bokad passhistorik att jämföra mot ännu."

        # 6. Fetch Gemini API key (fail fast before any external weather call)
        api_key = get_api_key('freja_gemini_apikey') or ""
        if not api_key:
            raise HTTPException(status_code=400, detail="Gemini API-nyckel är inte konfigurerad på servern.")

        # 7. Today's weather (first line of the 7-day forecast is today)
        weather_forecast = await fetch_7day_weather_forecast(location)

        # 8. Compile the check-in prompt (follows docs/FREJA_PT_COACH.md)
        prompt_content = f"""
Du är F.R.E.J.A.:s personliga tränare (COACH AI). Det är morgon och användaren gör sin dagliga incheckning.
Ge en KORT, varm och handfast morgonbriefing på svenska enligt coach-modellen. Dumpa inte rådata – tolka den.

DAGENS DATUM: {today_str}

[SENASTE GARMIN-DATA (i natt / senaste dygnet)]:
{garmin_snapshot}

[SENASTE WITHINGS-DATA (fallback för sömn/vilopuls samt kroppssammansättning)]:
{withings_snapshot}

[BERÄKNADE HÄLSOTRENDER (RHR & HRV)]:
{trends_data_str}

[TRÄNINGSFÖLJSAMHET]:
{adherence_str}

[GENOMFÖRT PASS IGÅR (Strava)]:
{completed_summary}

[DAGENS PLANERADE TRÄNINGSPASS (Google Calendar)]:
{todays_plan_str}

[ANDRA ÅTAGANDEN I KALENDERN IDAG]:
{other_events_str}

[VÄDERPROGNOS (första raden = idag)]:
{weather_forecast}

Regler för briefingen:
- Prioritera Garmin för sömn/vilopuls/HRV/body battery; använd Withings som komplement/fallback.
- Bedöm återhämtningen: om vilopulsen ökat markant (>{RHR_ALERT_PCT:.0f}%) eller HRV sjunkit markant (<{HRV_ALERT_PCT:.0f}%), eller om sömnen var kort/dålig eller Body Battery lågt – rekommendera lägre intensitet eller aktiv vila och förklara kort varför.
- Vid god återhämtning: peppa och behåll (eller utöka lätt) dagens plan.
- Om gårdagens pass INTE genomfördes: ingen skuld – föreslå att flytta fram naturligt om det behövs.
- Om det finns dagens pass utomhus och dåligt väder (kraftigt regn, snö, åska, storm) väntas: föreslå inomhus eller vila.
- Väg in andra kalenderåtaganden som kan påverka energi/tid idag.
- Om du sätter adjust_workout=true OCH det finns ett inbokat pass idag: ange 'adjusted_duration_minutes' till den nya längden i minuter (heltal, 0 = vila). F.R.E.J.A. bokar då om dagens kalenderpass automatiskt.
- Avsluta ALLTID med en tydlig fråga eller åtgärd. Var artig men extremt kunnig (F.R.E.J.A.-stil).
- Fältet 'briefing' ska vara en färdig, kort text i markdown som kan visas direkt för användaren (använd gärna emojis 📊 📅 💬 ✅ som i coach-modellen).
"""

        # 9. Call Gemini with a structured schema
        google_url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt_content}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 1200,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "sleep_summary": {"type": "STRING", "description": "Kort sammanfattning av nattens sömn."},
                        "recovery_summary": {"type": "STRING", "description": "Bedömning av vilopuls, HRV och Body Battery/återhämtning."},
                        "yesterday_status": {"type": "STRING", "description": "Om gårdagens pass genomfördes eller missades, utan skuldbeläggning."},
                        "todays_plan": {"type": "STRING", "description": "Dagens planerade träningspass i klartext."},
                        "recommendation": {"type": "STRING", "description": "Coachens rekommendation: behåll, sänk eller höj intensitet, med kort motivering."},
                        "adjust_workout": {"type": "BOOLEAN", "description": "true om dagens pass bör justeras jämfört med det som ligger i kalendern."},
                        "adjusted_duration_minutes": {"type": "INTEGER", "description": "Ny längd i minuter för dagens pass om adjust_workout=true (0 = vila). Utelämnas/0 om ingen justering."},
                        "weather_note": {"type": "STRING", "description": "Kort väderkommentar relevant för dagens pass (tom sträng om inte relevant)."},
                        "closing_question": {"type": "STRING", "description": "En tydlig avslutande fråga eller åtgärd till användaren."},
                        "briefing": {"type": "STRING", "description": "Färdig kort briefing i markdown, redo att visas direkt för användaren."}
                    },
                    "required": ["sleep_summary", "recovery_summary", "yesterday_status", "todays_plan", "recommendation", "adjust_workout", "closing_question", "briefing"]
                }
            }
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(google_url, json=payload, timeout=30.0)
            response.raise_for_status()
            res_json = response.json()

        briefing_text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not briefing_text:
            raise HTTPException(status_code=500, detail="Kunde inte generera incheckning från Gemini.")

        try:
            briefing_data = json.loads(briefing_text)
        except Exception:
            briefing_data = {"briefing": briefing_text}

        # 10. Act on the recommendation: if the coach wants to adjust today's session
        #     and there is a workout event in the calendar, re-time it automatically.
        calendar_updated = False
        if briefing_data.get("adjust_workout") and workout_events:
            try:
                new_dur = int(briefing_data.get("adjusted_duration_minutes") or 0)
            except (TypeError, ValueError):
                new_dur = 0
            if 0 < new_dur <= MAX_WORKOUT_MINUTES:
                ev = workout_events[0]
                start_time = (ev.get("start_time") or "")[:16]  # YYYY-MM-DDTHH:MM
                try:
                    start_dt = datetime.datetime.strptime(start_time, "%Y-%m-%dT%H:%M")
                    end_time = (start_dt + datetime.timedelta(minutes=new_dur)).strftime("%Y-%m-%dT%H:%M")
                    from backend.routes.google_calendar import core_save_calendar_event
                    base_desc = (ev.get("description") or "").split("\n\n[COACH AI")[0]
                    new_desc = f"{base_desc}\n\n[COACH AI justerade passet till {new_dur} min baserat på din återhämtning ({today_str}).]"
                    await core_save_calendar_event(
                        summary=ev.get("summary", "Träningspass"),
                        start_time=start_time,
                        end_time=end_time,
                        description=new_desc,
                        location=ev.get("location", ""),
                        db_id=ev.get("id")
                    )
                    calendar_updated = True
                except Exception as adj_err:
                    print(f"[TRAINER CHECKIN] Kunde inte justera kalenderpass: {adj_err}")

        return {
            "status": "success",
            "date": today_str,
            "checkin": briefing_data,
            "has_workout_today": bool(workout_events),
            "workout_completed_yesterday": bool(strava_rows),
            "adherence": adherence,
            "calendar_updated": calendar_updated
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API fel: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def core_optimize_upcoming_workouts(
    location: str = None, days_ahead: int = 7, trigger: str = "manual"
) -> dict:
    """Re-tunes the upcoming F.R.E.J.A. PT sessions in the calendar to the user's
    latest recovery data.

    Reads the most recent Garmin snapshot plus the RHR/HRV trends, pulls every
    workout event from today through ``days_ahead`` days out, and asks COACH AI
    whether each one is appropriate given sleep/HRV/recovery and the user's goal.
    Sessions that would risk injury or over-training are shortened, de-loaded, or
    turned into active rest — directly in Google Calendar. Good recovery leaves
    the plan untouched. Returns a summary of what changed (empty if nothing did).
    """
    profile = get_trainer_profile()
    goal = str(profile.get("goals") or profile.get("event") or "").strip()[:MAX_INPUT_LEN]
    limitations = str(profile.get("limitations") or "").strip()[:MAX_INPUT_LEN]

    today = today_local()
    today_str = today.strftime("%Y-%m-%d")
    horizon_str = (today + datetime.timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # Upcoming workouts (today .. horizon) that F.R.E.J.A. booked.
    from backend.routes.google_calendar import core_get_calendar_data, core_save_calendar_event
    all_events = core_get_calendar_data(days=max(days_ahead, 1))
    upcoming = [
        e for e in all_events
        if is_workout_event(e) and today_str <= (e.get("start_time") or "")[:10] <= horizon_str
    ]
    upcoming.sort(key=lambda e: (e.get("start_time") or ""))

    if not upcoming:
        return {
            "status": "no_workouts",
            "trigger": trigger,
            "assessment": "",
            "briefing": "Inga inbokade träningspass hittades för den kommande perioden, så inget behövde justeras.",
            "changes": [],
            "changes_count": 0,
            "considered": 0,
        }

    # Latest Garmin recovery snapshot + calculated trends.
    garmin_snapshot = "Ingen Garmin-data tillgänglig."
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status
            FROM garmin_health
            ORDER BY date DESC
            LIMIT 1
        ''')
        g = cursor.fetchone()
    if g:
        garmin_snapshot = (
            f"Datum: {g[0]}, Steg: {g[1]}, Sömn: {g[2]}h, Vilopuls: {g[3]}, Kalorier: {g[4]}kcal, "
            f"Träning: {g[5]} ({g[6]} min), Body Battery: {g[7]}, HRV: {g[8]}ms, "
            f"Återhämtningstid: {g[9]}h, Status: {g[10]}"
        )

    trends = calculate_trends()
    trends_data_str = format_trends_summary(trends)

    api_key = get_api_key('freja_gemini_apikey') or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API-nyckel är inte konfigurerad på servern.")

    # Compile the upcoming workouts for the prompt (id lets us map adjustments back).
    workout_lines = []
    for e in upcoming:
        dur = _event_duration_minutes(e)
        first_desc_line = ""
        if e.get("description"):
            first_desc_line = (e.get("description") or "").splitlines()[0][:120]
        workout_lines.append(
            f"- id={e.get('id')} | {(e.get('start_time') or '')[:10]} kl {(e.get('start_time') or '')[11:16]} | "
            f"\"{e.get('summary', '')}\" | {dur} min | {first_desc_line}"
        )
    workouts_str = "\n".join(workout_lines)

    limitations_prompt = (
        f'\nSKADOR / SJUKDOMAR / BEGRÄNSNINGAR: "{limitations}"\n'
        "Ta särskild hänsyn till dessa vid val av intensitet och pass."
        if limitations else ""
    )
    goal_str = goal or "Inget specifikt mål angivet – prioritera hälsa och hållbar progression."

    prompt_content = f"""
Du är F.R.E.J.A.:s personliga tränare (COACH AI). Din uppgift nu är att granska användarens
INBOKADE kommande träningspass mot deras SENASTE återhämtningsdata och avgöra om något behöver
justeras för att undvika skada eller överträning – samtidigt som passen fortsatt leder mot målet.

MÅL: "{goal_str}"{limitations_prompt}

[SENASTE GARMIN-DATA (senaste dygnet)]:
{garmin_snapshot}

[BERÄKNADE HÄLSOTRENDER (RHR & HRV)]:
{trends_data_str}

[INBOKADE KOMMANDE TRÄNINGSPASS (idag t.o.m. {horizon_str})]:
{workouts_str}

Regler:
- Bedöm återhämtningen utifrån sömn, vilopuls (RHR), HRV, Body Battery, återhämtningstid och training status.
- Om återhämtningen är DÅLIG (t.ex. RHR ökat markant >{RHR_ALERT_PCT:.0f}%, HRV sjunkit markant <{HRV_ALERT_PCT:.0f}%,
  kort/dålig sömn, lågt Body Battery, lång återhämtningstid, status Övertränad/Ansträngd):
  sänk längd och/eller intensitet på de närmaste passen, eller gör om ett hårt pass till aktiv vila.
- Om återhämtningen är GOD: behåll passen som de är (action="keep"). Sänk ALDRIG i onödan.
- Öka aldrig ett enskilt pass mer än ~10–15%. Prioritera hälsa framför att pressa målet.
- För VARJE inbokat pass ovan, returnera en post i "adjustments" med exakt samma event_id (heltal).
- action: "keep" (ingen ändring), "reduce" (kortare/lugnare pass), eller "rest" (gör om till aktiv vila/lätt rörlighet).
- new_duration_minutes: passets nya längd i minuter (för "keep" = nuvarande längd; för "rest" = kort lätt pass, t.ex. 15–25).
- new_title: valfri ny titel (t.ex. "🧘 Aktiv vila: rörlighet" vid rest, eller "🏃 Lugn löptur" vid nedtrappning). Lämna tom för att behålla.
- reason: en kort svensk mening om varför (visas i kalendern).
- briefing: en färdig KORT sammanfattning i markdown på svenska som visas direkt för användaren – vad du ändrade och varför (eller att allt får stå kvar).
"""

    google_url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt_content}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1500,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "assessment": {"type": "STRING", "description": "Kort bedömning av återhämtningsstatus."},
                    "briefing": {"type": "STRING", "description": "Färdig kort briefing i markdown, redo att visas för användaren."},
                    "adjustments": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "event_id": {"type": "INTEGER", "description": "Kalender-id för passet (från listan)."},
                                "action": {"type": "STRING", "description": "keep, reduce eller rest."},
                                "new_duration_minutes": {"type": "INTEGER", "description": "Passets nya längd i minuter."},
                                "new_title": {"type": "STRING", "description": "Valfri ny titel (tom = behåll)."},
                                "reason": {"type": "STRING", "description": "Kort motivering på svenska."}
                            },
                            "required": ["event_id", "action", "new_duration_minutes", "reason"]
                        }
                    }
                },
                "required": ["assessment", "briefing", "adjustments"]
            }
        }
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(google_url, json=payload, timeout=30.0)
        response.raise_for_status()
        res_json = response.json()

    opt_text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    if not opt_text:
        raise HTTPException(status_code=500, detail="Kunde inte generera optimering från Gemini.")

    try:
        opt_data = json.loads(opt_text)
    except Exception:
        opt_data = {"assessment": "", "briefing": opt_text, "adjustments": []}

    by_id = {e.get("id"): e for e in upcoming}
    changes = []
    for adj in (opt_data.get("adjustments") or []):
        try:
            eid = int(adj.get("event_id"))
        except (TypeError, ValueError):
            continue
        ev = by_id.get(eid)
        if not ev:
            continue

        action = str(adj.get("action") or "keep").strip().lower()
        if action == "keep":
            continue

        current_dur = _event_duration_minutes(ev)
        try:
            new_dur = int(adj.get("new_duration_minutes") or 0)
        except (TypeError, ValueError):
            new_dur = 0
        if action == "rest" and new_dur <= 0:
            new_dur = 20  # light active-recovery default
        new_dur = max(1, min(new_dur, MAX_WORKOUT_MINUTES))
        if action != "rest" and new_dur == current_dur:
            continue  # nothing to actually change

        start_time = (ev.get("start_time") or "")[:16]
        try:
            start_dt = datetime.datetime.strptime(start_time, "%Y-%m-%dT%H:%M")
        except ValueError:
            continue
        end_time = (start_dt + datetime.timedelta(minutes=new_dur)).strftime("%Y-%m-%dT%H:%M")

        orig_summary = ev.get("summary", "") or "Träningspass"
        new_title = str(adj.get("new_title") or "").strip()
        if action == "rest":
            summary = new_title or f"🧘 Aktiv vila (tidigare: {orig_summary})"
        else:
            summary = new_title or orig_summary
        reason = str(adj.get("reason") or "").strip()

        base_desc = (ev.get("description") or "").split("\n\n[COACH AI optimerade")[0]
        new_desc = (
            f"{base_desc}\n\n[COACH AI optimerade passet {current_dur}→{new_dur} min "
            f"({today_str}): {reason}]"
        )

        try:
            await core_save_calendar_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                description=new_desc,
                location=ev.get("location", ""),
                db_id=ev.get("id"),
            )
            changes.append({
                "event_id": eid,
                "date": start_time[:10],
                "action": action,
                "from_minutes": current_dur,
                "to_minutes": new_dur,
                "title": summary,
                "reason": reason,
            })
        except Exception as save_err:
            print(f"[TRAINER OPTIMIZE] Kunde inte uppdatera event {eid}: {save_err}")

    return {
        "status": "success",
        "trigger": trigger,
        "assessment": opt_data.get("assessment", ""),
        "briefing": opt_data.get("briefing", ""),
        "changes": changes,
        "changes_count": len(changes),
        "considered": len(upcoming),
    }


@router.post("/api/trainer/optimize")
async def optimize_trainer_workouts(request: Request):
    """Manually trigger COACH AI's recovery-driven re-tuning of upcoming workouts."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        location = body.get("location")
        try:
            days_ahead = int(body.get("days_ahead") or 7)
        except (TypeError, ValueError):
            days_ahead = 7
        days_ahead = max(1, min(days_ahead, 28))
        return await core_optimize_upcoming_workouts(location=location, days_ahead=days_ahead, trigger="manual")
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API fel: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _find_free_slot(workout_date: datetime.date, duration: int, day_events: list) -> datetime.datetime:
    """Finds a start time on workout_date that doesn't overlap existing events.

    Starts at the preferred hour and, on a clash, jumps to the end of the
    conflicting event, retrying until the day-end limit. Falls back to the
    preferred hour if no free slot fits.
    """
    dur = datetime.timedelta(minutes=duration)
    start = datetime.datetime.combine(workout_date, datetime.time(DEFAULT_WORKOUT_HOUR, 0))
    end_limit = datetime.datetime.combine(workout_date, datetime.time(DAY_END_HOUR, 0))

    intervals = []
    for e in day_events:
        try:
            s = datetime.datetime.strptime((e.get("start_time") or "")[:16], "%Y-%m-%dT%H:%M")
            en = datetime.datetime.strptime((e.get("end_time") or "")[:16], "%Y-%m-%dT%H:%M")
            intervals.append((s, en))
        except Exception:
            continue  # all-day / malformed events don't block scheduling
    intervals.sort()

    while start + dur <= end_limit:
        candidate_end = start + dur
        conflict_end = None
        for (s, en) in intervals:
            if start < en and candidate_end > s:  # overlap
                conflict_end = en
                break
        if conflict_end is None:
            return start
        start = conflict_end

    return datetime.datetime.combine(workout_date, datetime.time(DEFAULT_WORKOUT_HOUR, 0))


@router.post("/api/trainer/plans/book")
async def book_trainer_plan(request: Request):
    try:
        body = await request.json()
        plan_id = body.get("plan_id")
        start_date_str = body.get("start_date")

        if not plan_id or not start_date_str:
            raise HTTPException(status_code=400, detail="Plan-ID och startdatum krävs.")

        try:
            start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Felaktigt startdatumformat (använd ÅÅÅÅ-MM-DD).")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT advice_text FROM trainer_plans WHERE id = ?", (plan_id,))
            row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Träningsprogrammet hittades inte.")

        try:
            plan_data = json.loads(row[0])
        except Exception:
            raise HTTPException(status_code=400, detail="Detta träningsprogram saknar strukturerad data och kan inte bokas i kalendern.")

        workouts = plan_data.get("workouts", [])
        if not workouts:
            return {"status": "success", "message": "Inga träningspass att boka."}

        day_offsets = {
            "måndag": 0, "tisdag": 1, "onsdag": 2, "torsdag": 3,
            "fredag": 4, "lördag": 5, "söndag": 6
        }

        from backend.routes.google_calendar import (
            core_save_calendar_event, core_delete_calendar_event, core_get_calendar_data
        )

        # --- Idempotency: remove any events previously booked for THIS plan so
        #     re-booking updates instead of creating duplicates. ---
        rebooked = 0
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, event_id FROM trainer_bookings WHERE plan_id = ?", (plan_id,))
            prior = cursor.fetchall()
        for booking_id, event_id in prior:
            if event_id:
                try:
                    await core_delete_calendar_event(event_id)
                    rebooked += 1
                except Exception as del_err:
                    print(f"[TRAINER BOOK] Kunde inte ta bort tidigare event {event_id}: {del_err}")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trainer_bookings WHERE plan_id = ?", (plan_id,))
            conn.commit()

        # Existing calendar events used for conflict avoidance (mutated as we book).
        all_events = core_get_calendar_data(days=60)

        booked_count = 0
        for w in workouts:
            day_name = str(w.get("day", "")).lower()
            offset = day_offsets.get(day_name)
            if offset is None:
                continue

            try:
                duration = int(w.get("duration_minutes", 0) or 0)
            except (TypeError, ValueError):
                duration = 0
            if duration <= 0:
                continue  # Skip rest day
            duration = min(duration, MAX_WORKOUT_MINUTES)  # Sanity cap

            try:
                week = max(0, min(51, int(w.get("week", 0) or 0)))
            except (TypeError, ValueError):
                week = 0

            workout_date = start_date + datetime.timedelta(days=offset + week * 7)

            # Find a non-conflicting slot; format at minute precision so the
            # Google push (which appends ":00") produces a valid RFC3339 time.
            day_events = [e for e in all_events if (e.get("start_time") or "")[:10] == workout_date.isoformat()]
            slot_start = _find_free_slot(workout_date, duration, day_events)
            slot_end = slot_start + datetime.timedelta(minutes=duration)
            start_dt = slot_start.strftime("%Y-%m-%dT%H:%M")
            end_dt = slot_end.strftime("%Y-%m-%dT%H:%M")

            summary = f"💪 {w.get('activity_type', 'Träning')}: {w.get('title', 'Pass')}"
            description = f"Träningspass genererat av COACH AI.\n\nBeskrivning:\n{w.get('description', '')}\n\nTid: {duration} minuter."
            location = "F.R.E.J.A. PT"

            result = await core_save_calendar_event(
                summary=summary,
                start_time=start_dt,
                end_time=end_dt,
                description=description,
                location=location
            )
            event_id = (result.get("event") or {}).get("id")

            # Record the booking so it can be de-duplicated / adjusted later.
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO trainer_bookings (plan_id, event_id, workout_date, week) VALUES (?, ?, ?, ?)",
                    (plan_id, event_id, workout_date.isoformat(), week)
                )
                conn.commit()

            # Make this new event visible to the next same-day workout.
            all_events.append({"start_time": f"{start_dt}:00", "end_time": f"{end_dt}:00"})
            booked_count += 1

        msg = f"Bokade framgångsrikt {booked_count} träningspass i din kalender."
        if rebooked:
            msg += f" ({rebooked} tidigare bokade pass ersattes.)"
        return {"status": "success", "message": msg, "booked_count": booked_count, "replaced_count": rebooked}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
