"""AI Personal Trainer routes using FastAPI."""

import datetime
import httpx
import json
from fastapi import APIRouter, HTTPException, Query, Request
from backend.database import get_db_connection

router = APIRouter()

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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_gemini_apikey'")
            row = cursor.fetchone()
        
        api_key = row[0].strip() if row else ""
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

        # 6. Compile Prompt
        garmin_data_str = "\n".join(garmin_summary) if garmin_summary else "Ingen Garmin-data tillgänglig."
        strava_data_str = "\n".join(strava_summary) if strava_summary else "Ingen Strava-data tillgänglig."
        withings_data_str = "\n".join(withings_summary) if withings_summary else "Ingen Withings-data tillgänglig."

        limitations_prompt = f'\nSKADOR / SJUKDOMAR / BEGRÄNSNINGAR:\n"{limitations}"\nTa särskild hänsyn till dessa begränsningar, skador eller sjukdomar (t.ex. ansträngningsastma, knäskador etc.) och anpassa övningsval samt intensitet därefter.' if limitations else ""

        prompt_content = f"""
Du är en professionell personlig tränare och hälsocoach (COACH AI) integrerad i F.R.E.J.A.-systemet.
Analysera följande hälsodata, träningsdata och trender för användaren och skapa ett anpassat träningsprogram eller konkreta träningstips baserat på deras uppgivna mål.

MÅL: "{goal}"{limitations_prompt}

[BERÄKNADE HÄLSOTRENDER (RHR & HRV)]:
{trends_data_str}

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
