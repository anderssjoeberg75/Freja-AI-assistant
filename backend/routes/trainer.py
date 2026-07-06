"""AI Personal Trainer routes using FastAPI."""

import datetime
import httpx
import json
import urllib.parse
from fastapi import APIRouter, HTTPException, Query, Request
from backend.database import get_db_connection, get_api_key

router = APIRouter()

async def fetch_7day_weather_forecast(location: str = "Stockholm") -> str:
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
    garmin_rows = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT resting_hr, hrv 
                FROM garmin_health 
                ORDER BY date DESC 
                LIMIT 21
            ''')
            garmin_rows = cursor.fetchall()
    except Exception as e:
        print(f"Error fetching Garmin health data for trends: {e}")

    withings_rows = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT heart_pulse 
                FROM withings_measurements 
                ORDER BY date DESC 
                LIMIT 21
            ''')
            withings_rows = cursor.fetchall()
    except Exception as e:
        print(f"Error fetching Withings measurements for trends: {e}")
        
    recent_rhrs = [r[0] for r in garmin_rows[:7] if r[0] is not None]
    baseline_rhrs = [r[0] for r in garmin_rows[7:] if r[0] is not None]
    
    recent_hrvs = [r[1] for r in garmin_rows[:7] if r[1] is not None]
    baseline_hrvs = [r[1] for r in garmin_rows[7:] if r[1] is not None]

    if not recent_rhrs:
        recent_rhrs = [r[0] for r in withings_rows[:7] if r[0] is not None]
    if not baseline_rhrs:
        baseline_rhrs = [r[0] for r in withings_rows[7:] if r[0] is not None]
    
    rhr_recent_avg = sum(recent_rhrs) / len(recent_rhrs) if recent_rhrs else None
    rhr_baseline_avg = sum(baseline_rhrs) / len(baseline_rhrs) if baseline_rhrs else None
    hrv_recent_avg = sum(recent_hrvs) / len(recent_hrvs) if recent_hrvs else None
    hrv_baseline_avg = sum(baseline_hrvs) / len(baseline_hrvs) if baseline_hrvs else None
    
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
        goal = body.get("goal", "").strip()
        limitations = body.get("limitations", "").strip()
        if not goal:
            raise HTTPException(status_code=400, detail="Mål saknas.")
            
        # 1. Fetch Garmin health logs
        garmin_summary = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status
                FROM garmin_health
                ORDER BY date DESC
                LIMIT 7
            ''')
            garmin_rows = cursor.fetchall()
            for r in garmin_rows:
                garmin_summary.append(
                    f"Datum: {r[0]}, Steg: {r[1]}, Sömn: {r[2]}h, Vilopuls: {r[3]}, Kalorier: {r[4]}kcal, Träning: {r[5]} ({r[6]} min), Body Battery: {r[7]}, HRV: {r[8]}ms, Återhämtningstid: {r[9]}h, Status: {r[10]}"
                )

        # 2. Fetch Strava activities
        strava_summary = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT name, type, date, distance, moving_time, total_elevation_gain, average_heartrate, max_heartrate, calories
                FROM strava_activities
                ORDER BY date DESC
                LIMIT 7
            ''')
            strava_rows = cursor.fetchall()
            for r in strava_rows:
                dist_km = round(r[3] / 1000.0, 2) if r[3] else 0
                dur_min = round(r[4] / 60.0, 1) if r[4] else 0
                strava_summary.append(
                    f"Aktivitet: {r[0]}, Typ: {r[1]}, Datum: {r[2]}, Distans: {dist_km} km, Tid: {dur_min} min, Höjdmeter: {r[5]}m, Snittpuls: {r[6]}, Maxpuls: {r[7]}, Kalorier: {r[8]}kcal"
                )

        # 3. Fetch Withings measurements
        withings_summary = []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, weight, fat_ratio, bone_mass, heart_pulse, sleep_duration, steps, calories, sleep_score
                FROM withings_measurements
                ORDER BY date DESC
                LIMIT 7
            ''')
            withings_rows = cursor.fetchall()
            for r in withings_rows:
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
        trend_summary = []
        if trends["rhr_recent_avg"] is not None:
            recent_str = f"{trends['rhr_recent_avg']:.1f}"
            baseline_str = f"{trends['rhr_baseline_avg']:.1f}" if trends["rhr_baseline_avg"] is not None else "N/A"
            change_str = f"{trends['rhr_change_pct']:.1f}%" if trends["rhr_change_pct"] is not None else "N/A"
            trend_summary.append(f"Vilopuls (RHR): Senaste 7 dgr snitt: {recent_str} BPM, Baslinje (föregående 14 dgr): {baseline_str} BPM (Förändring: {change_str})")
        if trends["hrv_recent_avg"] is not None:
            recent_str = f"{trends['hrv_recent_avg']:.1f}"
            baseline_str = f"{trends['hrv_baseline_avg']:.1f}" if trends["hrv_baseline_avg"] is not None else "N/A"
            change_str = f"{trends['hrv_change_pct']:.1f}%" if trends["hrv_change_pct"] is not None else "N/A"
            trend_summary.append(f"HRV: Senaste 7 dgr snitt: {recent_str} ms, Baslinje (föregående 14 dgr): {baseline_str} ms (Förändring: {change_str})")
            
        trends_data_str = "\n".join(trend_summary) if trend_summary else "Inga tillräckliga trenddata (RHR/HRV) tillgängliga."

        # 5.5 Fetch 7-day weather forecast
        weather_forecast = await fetch_7day_weather_forecast("Stockholm")

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
        google_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
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
        today_str = datetime.date.today().strftime('%Y-%m-%d')
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
        location = (body.get("location") or "Stockholm").strip() or "Stockholm"

        today = datetime.date.today()
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

        def _is_workout(ev):
            summary = (ev.get("summary") or "")
            location_val = (ev.get("location") or "")
            return ("F.R.E.J.A. PT" in location_val) or any(
                marker in summary for marker in ("💪", "🏃", "🚶")
            )

        workout_events = [e for e in todays_events if _is_workout(e)]
        other_events = [e for e in todays_events if not _is_workout(e)]

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

        # 5. Calculated RHR/HRV trends (reuses the same logic as plan generation)
        trends = calculate_trends()
        trend_summary = []
        if trends["rhr_recent_avg"] is not None:
            recent_str = f"{trends['rhr_recent_avg']:.1f}"
            baseline_str = f"{trends['rhr_baseline_avg']:.1f}" if trends["rhr_baseline_avg"] is not None else "N/A"
            change_str = f"{trends['rhr_change_pct']:.1f}%" if trends["rhr_change_pct"] is not None else "N/A"
            trend_summary.append(f"Vilopuls (RHR): Senaste 7 dgr snitt: {recent_str} BPM, Baslinje (föregående 14 dgr): {baseline_str} BPM (Förändring: {change_str})")
        if trends["hrv_recent_avg"] is not None:
            recent_str = f"{trends['hrv_recent_avg']:.1f}"
            baseline_str = f"{trends['hrv_baseline_avg']:.1f}" if trends["hrv_baseline_avg"] is not None else "N/A"
            change_str = f"{trends['hrv_change_pct']:.1f}%" if trends["hrv_change_pct"] is not None else "N/A"
            trend_summary.append(f"HRV: Senaste 7 dgr snitt: {recent_str} ms, Baslinje (föregående 14 dgr): {baseline_str} ms (Förändring: {change_str})")
        trends_data_str = "\n".join(trend_summary) if trend_summary else "Inga tillräckliga trenddata (RHR/HRV) tillgängliga."

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
- Bedöm återhämtningen: om vilopulsen ökat markant (>5%) eller HRV sjunkit markant (<-10%), eller om sömnen var kort/dålig eller Body Battery lågt – rekommendera lägre intensitet eller aktiv vila och förklara kort varför.
- Vid god återhämtning: peppa och behåll (eller utöka lätt) dagens plan.
- Om gårdagens pass INTE genomfördes: ingen skuld – föreslå att flytta fram naturligt om det behövs.
- Om det finns dagens pass utomhus och dåligt väder (kraftigt regn, snö, åska, storm) väntas: föreslå inomhus eller vila.
- Väg in andra kalenderåtaganden som kan påverka energi/tid idag.
- Avsluta ALLTID med en tydlig fråga eller åtgärd. Var artig men extremt kunnig (F.R.E.J.A.-stil).
- Fältet 'briefing' ska vara en färdig, kort text i markdown som kan visas direkt för användaren (använd gärna emojis 📊 📅 💬 ✅ som i coach-modellen).
"""

        # 9. Call Gemini with a structured schema
        google_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
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

        return {
            "status": "success",
            "date": today_str,
            "checkin": briefing_data,
            "has_workout_today": bool(workout_events),
            "workout_completed_yesterday": bool(strava_rows)
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API fel: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
            "måndag": 0,
            "tisdag": 1,
            "onsdag": 2,
            "torsdag": 3,
            "fredag": 4,
            "lördag": 5,
            "söndag": 6
        }
        
        from backend.routes.google_calendar import core_save_calendar_event
        
        booked_count = 0
        for w in workouts:
            day_name = w.get("day", "").lower()
            offset = day_offsets.get(day_name)
            if offset is None:
                continue
                
            duration = w.get("duration_minutes", 0)
            if duration <= 0:
                continue # Skip rest day
                
            workout_date = start_date + datetime.timedelta(days=offset)
            
            # Start workout at 08:00 AM local time
            start_dt = f"{workout_date}T08:00:00"
            end_dt_obj = datetime.datetime.combine(workout_date, datetime.time(8, 0)) + datetime.timedelta(minutes=duration)
            end_dt = end_dt_obj.strftime("%Y-%m-%dT%H:%M:%S")
            
            summary = f"💪 {w.get('activity_type', 'Träning')}: {w.get('title', 'Pass')}"
            
            # Description containing detail instruction
            description = f"Träningspass genererat av COACH AI.\n\nBeskrivning:\n{w.get('description', '')}\n\nTid: {duration} minuter."
            location = "F.R.E.J.A. PT"
            
            await core_save_calendar_event(
                summary=summary,
                start_time=start_dt,
                end_time=end_dt,
                description=description,
                location=location
            )
            booked_count += 1
            
        return {"status": "success", "message": f"Bokade framgångsrikt {booked_count} träningspass i din kalender."}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
