"""Unified Tool Registry for F.R.E.J.A.

Defines all tool declarations in Gemini format and implements their execution in Python.
Used by both the web frontend (via API) and the Telegram bot.
"""

import datetime
import urllib.parse
import httpx
from starlette.concurrency import run_in_threadpool
from backend.database import get_db_connection, get_api_key

# Import backend business logic routines
from backend.services.search_service import perform_search
from backend.routes.garmin import run_garmin_sync_task, get_garmin_data
from backend.routes.withings import run_withings_sync_task, get_withings_data
from backend.routes.strava import (
    run_strava_sync_task,
    get_strava_data,
    get_strava_activity_details,
    get_strava_athlete_stats,
)
from backend.routes.google_calendar import (
    core_get_calendar_data,
    core_save_calendar_event,
    core_delete_calendar_event,
)
from backend.services.codex_service import (
    execute_codex_code_impl,
    codex_git_ops_impl,
    codex_audit_codebase_impl,
    codex_run_and_fix_impl,
)
from backend.services.facebook_service import download_facebook_photos_impl

# 1. TOOL DECLARATIONS (Gemini JSON format)
TOOL_DECLARATIONS = [
    {
        "name": "get_weather",
        "description": "Hämtar aktuellt väder för en viss stad eller geografisk plats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "location": {
                    "type": "STRING",
                    "description": "Namnet på staden eller platsen att söka efter, t.ex. Stockholm, Göteborg, London."
                }
            },
            "required": ["location"]
        }
    },
    {
        "name": "google_search",
        "description": "Sök på webben efter information, nyheter eller fakta.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Sökfrågan att söka efter på Google."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_garmin_health",
        "description": "Hämtar användarens senaste Garmin hälso- och träningsdata (steg, sömn, vilopuls, kalorier, body battery, HRV, återhämtningstid, träningsstatus och träningspass). Standard är 1 dag (enbart senaste dygnet) om inte användaren uttryckligen ber om en längre period som t.ex. senaste veckan.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {
                    "type": "INTEGER",
                    "description": "Antal dagar historik att hämta (standard är 1 för enbart senaste dagen)."
                }
            }
        }
    },
    {
        "name": "get_withings_health",
        "description": "Hämtar användarens senaste Withings mätningar inklusive vikt, kroppssammansättning, puls, sömnstatistik (score, duration) samt dagsaktivitet (steg, kalorier). Parameter 'days' anger antal dagar historik att hämta (standard 7).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {
                    "type": "INTEGER",
                    "description": "Antal dagar historik att hämta (standard 7)."
                }
            }
        }
    },
    {
        "name": "get_strava_data",
        "description": "Hämtar användarens senaste Strava-aktiviteter (namn, typ, distans, träningstid, höjdmeter, genomsnittlig puls, maxpuls och kalorier). Standard är 7 dagar historik om inte användaren uttryckligen ber om en längre period som t.ex. 14 eller 30 dagar.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {
                    "type": "INTEGER",
                    "description": "Antal dagar historik att hämta (standard är 7)."
                }
            }
        }
    },
    {
        "name": "get_strava_activity_analysis",
        "description": "Hämtar varvtider (laps/splits) samt puls- och kraftzoner (heartrate/power distribution) för en specifik aktivitet med angivet ID. Detta gör det möjligt att analysera tempo, pacing, samt aerob och anaerob belastning under passet.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "activity_id": {
                    "type": "STRING",
                    "description": "Det unika aktivitets-ID:t (från Strava, t.ex. hämtat via get_strava_data)."
                }
            },
            "required": ["activity_id"]
        }
    },
    {
        "name": "get_strava_athlete_stats",
        "description": "Hämtar användarens ackumulerade träningsmängder, inklusive årliga (YTD) och historiska totaler samt statistik för de senaste 4 veckorna uppdelat på löpning, cykling och simning.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "manage_google_calendar",
        "description": "Hanterar användarens kalenderhändelser. Du kan boka/skapa nya händelser, ändra/editera befintliga händelser, radera/ta bort händelser eller lista händelser under en viss tidsperiod (dagar).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Åtgärd att utföra: 'list', 'create', 'edit', eller 'delete'.",
                    "enum": ["list", "create", "edit", "delete"]
                },
                "event_id": {
                    "type": "INTEGER",
                    "description": "Det unika databas-ID:t för händelsen (krävs vid 'edit' och 'delete')."
                },
                "summary": {
                    "type": "STRING",
                    "description": "Händelsens titel eller sammanfattning (krävs vid 'create' och 'edit')."
                },
                "start_time": {
                    "type": "STRING",
                    "description": "Starttid i ISO-format (t.ex. '2026-06-12T14:00:00', krävs vid 'create' och 'edit')."
                },
                "end_time": {
                    "type": "STRING",
                    "description": "Sluttid i ISO-format (t.ex. '2026-06-12T15:00:00', krävs vid 'create' och 'edit')."
                },
                "description": {
                    "type": "STRING",
                    "description": "Detaljerad beskrivning eller mötesanteckningar (valfritt)."
                },
                "location": {
                    "type": "STRING",
                    "description": "Plats eller möteslänk (valfritt)."
                },
                "days": {
                    "type": "INTEGER",
                    "description": "Antal dagar bakåt och framåt från idag att hämta vid 'list'. Standard är 30 dagar."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "execute_codex_code",
        "description": "Kör Python-kod eller skalkommandon lokalt på värdmaskinen. Används för att köra skript, tester eller systemadministration.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "language": {
                    "type": "STRING",
                    "description": "Språket att köra: 'python' eller 'shell'.",
                    "enum": ["python", "shell"]
                },
                "code": {
                    "type": "STRING",
                    "description": "Koden eller kommandot som ska exekveras."
                }
            },
            "required": ["language", "code"]
        }
    },
    {
        "name": "run_code",
        "description": "Alias för execute_codex_code. Kör Python-kod eller skalkommandon lokalt.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "language": {
                    "type": "STRING",
                    "description": "Språket att köra: 'python' eller 'shell'.",
                    "enum": ["python", "shell"]
                },
                "code": {
                    "type": "STRING",
                    "description": "Koden eller kommandot som ska exekveras."
                }
            },
            "required": ["language", "code"]
        }
    },
    {
        "name": "codex_git_ops",
        "description": "Hanterar git-operationer i den lokala källkodskatalogen (t.ex. status, commit, log, push, checkout).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Git-åtgärden: 'status', 'log', 'push', 'checkout', 'clone' eller 'commit'.",
                    "enum": ["status", "log", "push", "checkout", "clone", "commit"]
                },
                "argument": {
                    "type": "STRING",
                    "description": "Argument för åtgärden (t.ex. branch-namn, commit-meddelande eller repolänk)."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "codex_audit_codebase",
        "description": "Genomför en självanalys (audit) av källkoden för att identifiera buggar, prestandaproblem och kodförbättringar samt sparar en utförlig rapport.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "tool_analyze_code",
        "description": "Alias för codex_audit_codebase. Genomför en självanalys (audit) av källkoden.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "codex_run_and_fix",
        "description": "Kör ett kommando och försöker automatiskt rätta källkoden i den angivna filen om kommandot/testet misslyckas.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {
                    "type": "STRING",
                    "description": "Kommandot som ska köras, t.ex. 'pytest tests/test_file.py' eller 'python3 script.py'."
                },
                "file_path": {
                    "type": "STRING",
                    "description": "Relativ sökväg till filen som ska auto-rättas vid fel, t.ex. 'backend/routes/sync.py'."
                },
                "max_retries": {
                    "type": "INTEGER",
                    "description": "Max antal försök att auto-rätta källkoden (standard 3)."
                }
            },
            "required": ["command", "file_path"]
        }
    },
    {
        "name": "download_facebook_photos",
        "description": "Laddar ner foton från en användares Facebook-profil eller fotogalleri (t.ex. .../photos_by) med hjälp av Playwright. Hämtar bilder och sparar dem lokalt.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "profile_url": {
                    "type": "STRING",
                    "description": "Den fullständiga URL:en till Facebook-profilens bilder, t.ex. https://www.facebook.com/profile.php?id=61581510724534&sk=photos_by"
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Maximalt antal bilder att ladda ner (standard är 1000)."
                }
            },
            "required": ["profile_url"]
        }
    },
    {
        "name": "get_personal_trainer_advice",
        "description": "Hämtar användarens hälsodata och träningsdata (från Garmin, Strava och Withings) och sammanställer personliga träningsråd, tips och träningsprogram baserat på användarens angivna mål.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal": {
                    "type": "STRING",
                    "description": "Användarens träningsmål eller fokusområde (t.ex. 'gå ner i vikt', 'förbättra löpning', 'styrketräning')."
                },
                "limitations": {
                    "type": "STRING",
                    "description": "Eventuella skador, sjukdomar eller fysiska begränsningar (t.ex. 'ansträngningsastma', 'känsliga knän')."
                }
            },
            "required": ["goal"]
        }
    },
    {
        "name": "learn_topic",
        "description": "Söker på nätet och lär sig allt om ett visst ämne (t.ex. odling av lök). Sparar den inhämtade kunskapen i databasen för framtida bruk.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic": {
                    "type": "STRING",
                    "description": "Ämnet eller sökfrågan som Freja ska lära sig om (t.ex. 'odling av lök')."
                }
            },
            "required": ["topic"]
        }
    },
    {
        "name": "get_learned_knowledge",
        "description": "Hämtar tidigare inlärd kunskap från databasen baserat på sökord eller ämne för att svara på användarens frågor.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Valfri sökfråga eller ämnesord för att filtrera sparad kunskap (t.ex. 'lök')."
                }
            }
        }
    }
]

# Permission key mappings matching localStorage keys on frontend
TOOL_PERMISSION_KEYS = {
    "get_weather": "freja_tool_get_weather_allowed",
    "google_search": "freja_tool_google_search_allowed",
    "get_garmin_health": "freja_tool_get_garmin_health_allowed",
    "get_withings_health": "freja_tool_get_withings_health_allowed",
    "get_strava_data": "freja_tool_get_strava_data_allowed",
    "get_strava_activity_analysis": "freja_tool_get_strava_activity_analysis_allowed",
    "get_strava_athlete_stats": "freja_tool_get_strava_athlete_stats_allowed",
    "manage_google_calendar": "freja_tool_manage_google_calendar_allowed",
    "execute_codex_code": "freja_tool_execute_codex_code_allowed",
    "run_code": "freja_tool_run_code_allowed",
    "codex_git_ops": "freja_tool_codex_git_ops_allowed",
    "codex_audit_codebase": "freja_tool_codex_audit_codebase_allowed",
    "tool_analyze_code": "freja_tool_tool_analyze_code_allowed",
    "codex_run_and_fix": "freja_tool_codex_run_and_fix_allowed",
    "download_facebook_photos": "freja_tool_download_facebook_photos_allowed",
    "get_personal_trainer_advice": "freja_tool_get_personal_trainer_advice_allowed",
    "learn_topic": "freja_tool_learn_topic_allowed",
    "get_learned_knowledge": "freja_tool_get_learned_knowledge_allowed",
}

# 2. TOOL EXECUTORS IMPLEMENTATION
async def exec_weather(args):
    location = args.get("location", "Stockholm")
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=sv&format=json"
        async with httpx.AsyncClient() as client:
            res = await client.get(geo_url, timeout=8.0)
            res.raise_for_status()
            geo_data = res.json()
            
        results = geo_data.get('results')
        if not results:
            return {"error": f"Kunde inte hitta platsen: '{location}'."}
        
        first = results[0]
        lat = first['latitude']
        lon = first['longitude']
        name = first['name']
        country = first.get('country', '')
        
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m&wind_speed_unit=ms&timezone=auto"
        async with httpx.AsyncClient() as client:
            res = await client.get(weather_url, timeout=8.0)
            res.raise_for_status()
            weather_data = res.json()
            
        current = weather_data.get('current')
        if not current:
            return {"error": "Ingen väderdata returnerades."}
            
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
        desc = wmo_codes.get(current.get('weather_code', 0), "Atmosfäriska fluktuationer")
        
        return {
            "location": f"{name}, {country}",
            "temperature": f"{current.get('temperature_2m')}°C",
            "feels_like": f"{current.get('apparent_temperature')}°C",
            "description": desc,
            "humidity": f"{current.get('relative_humidity_2m')}%",
            "wind_speed": f"{current.get('wind_speed_10m')} m/s",
            "is_day": "Dag" if current.get('is_day') == 1 else "Natt"
        }
    except Exception as e:
        return {"error": f"Misslyckades att hämta väderdata: {str(e)}"}

async def exec_google_search(args):
    query = args.get("query", "")
    if not query:
        return {"error": "Sökfråga saknas."}
    results = await perform_search(query)
    return {"results": results}

def is_sync_recent(provider: str, max_age_hours: int = 12) -> bool:
    try:
        last_sync_value = get_api_key(f"last_sync_{provider}")
        if last_sync_value:
            last_sync = datetime.datetime.strptime(last_sync_value, "%Y-%m-%d %H:%M:%S")
            age = datetime.datetime.now() - last_sync
            if age.total_seconds() < max_age_hours * 3600:
                return True
    except Exception as e:
        print(f"[tool_registry] Error checking recent sync for {provider}: {e}")
    return False

async def exec_garmin_health(args):
    days = int(args.get("days", 1) or 1)
    sync_status = "inte genomförd"
    sync_message = ""
    
    # 1. Retrieve keys and sync synchronously
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    
    if email and password:
        if is_sync_recent("garmin"):
            sync_status = "success"
            sync_message = "Garmin-synkronisering hoppades över (nyligen uppdaterad)."
            print("[Garmin Tool] Recent sync found. Skipping API sync, using cached DB data.")
        else:
            try:
                # Garmin Connect sync is CPU/network intensive sync, run in ThreadPool
                await run_in_threadpool(run_garmin_sync_task, email, password, days)
                sync_status = "success"
                sync_message = "Garmin-synkronisering slutförd."
            except Exception as sync_err:
                sync_status = "failed"
                sync_message = str(sync_err)
            
    # 2. Query database for health logs
    try:
        data = await get_garmin_data(days=days)
        if not data:
            return {
                "sync_status": sync_status,
                "sync_message": sync_message,
                "message": "Ingen Garmin-data hittades i databasen."
            }
            
        # 3. Calculate summary stats
        total_steps = 0
        total_sleep = 0.0
        total_hr = 0
        total_calories = 0
        workout_days = 0
        total_workout_min = 0
        total_bb = 0
        bb_count = 0
        total_hrv = 0
        hrv_count = 0
        total_recovery = 0
        recovery_count = 0
        
        for day in data:
            total_steps += day.get('steps', 0) or 0
            total_sleep += day.get('sleep_hours', 0.0) or 0.0
            total_hr += day.get('resting_hr', 0) or 0
            total_calories += day.get('active_calories', 0) or 0
            if day.get('workout_type') and day.get('workout_type') != "Ingen":
                workout_days += 1
                total_workout_min += day.get('workout_duration', 0) or 0
            if day.get('body_battery') is not None:
                total_bb += day['body_battery']
                bb_count += 1
            if day.get('hrv') is not None:
                total_hrv += day['hrv']
                hrv_count += 1
            if day.get('recovery_time') is not None:
                total_recovery += day['recovery_time']
                recovery_count += 1
                
        num_days = len(data)
        avg_steps = Math_round(total_steps / num_days) if num_days > 0 else 0
        avg_sleep = round(total_sleep / num_days, 1) if num_days > 0 else 0.0
        avg_hr = Math_round(total_hr / num_days) if num_days > 0 else 0
        avg_calories = Math_round(total_calories / num_days) if num_days > 0 else 0
        avg_bb = Math_round(total_bb / bb_count) if bb_count > 0 else None
        avg_hrv = Math_round(total_hrv / hrv_count) if hrv_count > 0 else None
        avg_recovery = Math_round(total_recovery / recovery_count) if recovery_count > 0 else None
        
        return {
            "sync_status": sync_status,
            "sync_message": sync_message,
            "period_days": num_days,
            "latest_metrics": {
                "training_status": data[0].get('training_status') if data else None,
                "recovery_time_hours": data[0].get('recovery_time') if data else None
            },
            "averages": {
                "avg_daily_steps": avg_steps,
                "avg_sleep_hours": avg_sleep,
                "avg_resting_heart_rate": avg_hr,
                "avg_active_calories": avg_calories,
                "avg_body_battery": avg_bb,
                "avg_hrv": avg_hrv,
                "avg_recovery_time_hours": avg_recovery,
                "total_workouts": workout_days,
                "total_workout_minutes": total_workout_min
            },
            "daily_logs": data
        }
    except Exception as e:
        return {"error": f"Kunde inte hämta Garmin-data: {str(e)}"}

async def exec_withings_health(args):
    days = int(args.get("days", 7) or 7)
    sync_status = "inte genomförd"
    sync_message = ""
    
    # 1. Retrieve keys and sync synchronously
    client_id = get_api_key('freja_withings_client_id') or ""
    client_secret = get_api_key('freja_withings_client_secret') or ""
    refresh_token = get_api_key('freja_withings_refresh_token') or ""

    if client_id and client_secret and refresh_token:
        if is_sync_recent("withings"):
            sync_status = "success"
            sync_message = "Withings-synkronisering hoppades över (nyligen uppdaterad)."
            print("[Withings Tool] Recent sync found. Skipping API sync, using cached DB data.")
        else:
            try:
                await run_withings_sync_task(client_id, client_secret, refresh_token, days)
                sync_status = "success"
                sync_message = "Withings-synkronisering slutförd."
            except Exception as sync_err:
                sync_status = "failed"
                sync_message = str(sync_err)
            
    # 2. Query database for metrics
    try:
        data = await get_withings_data(days=days)
        if not data:
            return {
                "sync_status": sync_status,
                "sync_message": sync_message,
                "message": "Ingen Withings-data hittades i databasen."
            }
            
        # Calculate averages
        total_weight, total_fat, total_bone, total_pulse = 0.0, 0.0, 0.0, 0.0
        count_weight, count_fat, count_bone, count_pulse = 0, 0, 0, 0
        total_sleep, count_sleep = 0, 0
        total_sleep_score, count_sleep_score = 0, 0
        total_steps, count_steps = 0, 0
        total_calories, count_calories = 0, 0
        
        for entry in data:
            if entry.get('weight') is not None:
                total_weight += entry['weight']
                count_weight += 1
            if entry.get('fat_ratio') is not None:
                total_fat += entry['fat_ratio']
                count_fat += 1
            if entry.get('bone_mass') is not None:
                total_bone += entry['bone_mass']
                count_bone += 1
            if entry.get('heart_pulse') is not None:
                total_pulse += entry['heart_pulse']
                count_pulse += 1
            if entry.get('sleep_duration') is not None:
                total_sleep += entry['sleep_duration']
                count_sleep += 1
            if entry.get('sleep_score') is not None:
                total_sleep_score += entry['sleep_score']
                count_sleep_score += 1
            if entry.get('steps') is not None:
                total_steps += entry['steps']
                count_steps += 1
            if entry.get('calories') is not None:
                total_calories += entry['calories']
                count_calories += 1
                
        avg_weight = round(total_weight / count_weight, 2) if count_weight > 0 else None
        avg_fat = round(total_fat / count_fat, 1) if count_fat > 0 else None
        avg_bone = round(total_bone / count_bone, 2) if count_bone > 0 else None
        avg_pulse = Math_round(total_pulse / count_pulse) if count_pulse > 0 else None
        avg_sleep = round((total_sleep / count_sleep) / 3600.0, 2) if count_sleep > 0 else None
        avg_sleep_score = Math_round(total_sleep_score / count_sleep_score) if count_sleep_score > 0 else None
        avg_steps = Math_round(total_steps / count_steps) if count_steps > 0 else None
        avg_calories = Math_round(total_calories / count_calories) if count_calories > 0 else None
        
        formatted_measurements = []
        for entry in data:
            formatted_measurements.append({
                'date': entry.get('date'),
                'weight': entry.get('weight'),
                'fat_ratio': entry.get('fat_ratio'),
                'bone_mass': entry.get('bone_mass'),
                'heart_pulse': entry.get('heart_pulse'),
                'sleep_hours': round(entry['sleep_duration'] / 3600.0, 2) if entry.get('sleep_duration') else None,
                'sleep_deep_hours': round(entry['sleep_deep'] / 3600.0, 2) if entry.get('sleep_deep') else None,
                'sleep_rem_hours': round(entry['sleep_rem'] / 3600.0, 2) if entry.get('sleep_rem') else None,
                'sleep_score': entry.get('sleep_score'),
                'steps': entry.get('steps'),
                'distance_km': round(entry['distance'] / 1000.0, 2) if entry.get('distance') else None,
                'calories': entry.get('calories'),
                'elevation_m': entry.get('elevation')
            })
            
        return {
            "sync_status": sync_status,
            "sync_message": sync_message,
            "period_days": len(data),
            "averages": {
                "avg_weight": avg_weight,
                "avg_fat_ratio": avg_fat,
                "avg_bone_mass": avg_bone,
                "avg_heart_pulse": avg_pulse,
                "avg_steps": avg_steps,
                "avg_sleep_hours": avg_sleep,
                "avg_sleep_score": avg_sleep_score,
                "avg_active_calories": avg_calories
            },
            "measurements": formatted_measurements
        }
    except Exception as e:
        return {"error": f"Kunde inte hämta Withings-data: {str(e)}"}

async def exec_strava_data(args):
    days = int(args.get("days", 7) or 7)
    sync_status = "inte genomförd"
    sync_message = ""
    
    # 1. Retrieve keys and sync synchronously
    client_id = get_api_key('freja_strava_client_id') or ""
    client_secret = get_api_key('freja_strava_client_secret') or ""
    refresh_token = get_api_key('freja_strava_refresh_token') or ""

    if client_id and client_secret and refresh_token:
        if is_sync_recent("strava"):
            sync_status = "success"
            sync_message = "Strava-synkronisering hoppades över (nyligen uppdaterad)."
            print("[Strava Tool] Recent sync found. Skipping API sync, using cached DB data.")
        else:
            try:
                await run_strava_sync_task(client_id, client_secret, refresh_token, days)
                sync_status = "success"
                sync_message = "Strava-synkronisering slutförd."
            except Exception as sync_err:
                sync_status = "failed"
                sync_message = str(sync_err)
            
    # 2. Retrieve database records
    try:
        data = await get_strava_data(days=days)
        if not data:
            return {
                "sync_status": sync_status,
                "sync_message": sync_message,
                "message": "Inga Strava-aktiviteter hittades i databasen."
            }
            
        # Calculate summary statistics
        total_distance = 0.0
        total_moving_time = 0
        total_elevation = 0.0
        total_calories = 0.0
        heart_rate_sum = 0.0
        heart_rate_count = 0
        max_heart_rate_peak = 0
        activity_count = len(data)
        
        for act in data:
            total_distance += act.get('distance', 0.0) or 0.0
            total_moving_time += act.get('moving_time', 0) or 0
            total_elevation += act.get('total_elevation_gain', 0.0) or 0.0
            total_calories += act.get('calories', 0.0) or 0.0
            
            if act.get('average_heartrate') is not None:
                heart_rate_sum += act['average_heartrate']
                heart_rate_count += 1
            if act.get('max_heartrate') is not None:
                if act['max_heartrate'] > max_heart_rate_peak:
                    max_heart_rate_peak = act['max_heartrate']
                    
        avg_heart_rate = Math_round(heart_rate_sum / heart_rate_count) if heart_rate_count > 0 else None
        
        return {
            "sync_status": sync_status,
            "sync_message": sync_message,
            "period_days": days,
            "summary": {
                "activity_count": activity_count,
                "total_distance_meters": Math_round(total_distance),
                "total_distance_km": round(total_distance / 1000.0, 2),
                "total_moving_time_seconds": total_moving_time,
                "total_moving_time_minutes": Math_round(total_moving_time / 60),
                "total_elevation_gain_meters": Math_round(total_elevation),
                "total_calories_kcal": Math_round(total_calories),
                "average_heartrate": avg_heart_rate,
                "max_heartrate_peak": max_heart_rate_peak if max_heart_rate_peak > 0 else None
            },
            "activities": data
        }
    except Exception as e:
        return {"error": f"Kunde inte hämta Strava-aktiviteter: {str(e)}"}

async def exec_strava_activity_analysis(args):
    activity_id = args.get("activity_id", "")
    if not activity_id:
        return {"error": "Aktivitets-ID saknas."}
    return await get_strava_activity_details(id=activity_id)

async def exec_strava_athlete_stats(args):
    return await get_strava_athlete_stats()

async def exec_manage_google_calendar(args):
    action = args.get("action", "").lower()
    if not action:
        return {"error": "Calendar action is missing."}
        
    try:
        if action == "list":
            days = int(args.get("days", 30) or 30)
            events = core_get_calendar_data(days=days)
            return {
                "message": f"Hittade {len(events)} kalenderhändelser.",
                "events": events
            }
        elif action in ("create", "edit"):
            summary = args.get("summary", "")
            start_time = args.get("start_time", "")
            end_time = args.get("end_time", "")
            description = args.get("description", "")
            location = args.get("location", "")
            db_id = args.get("event_id")
            
            if not summary or not start_time or not end_time:
                return {"error": "Titel (summary), starttid och sluttid krävs."}
                
            return await core_save_calendar_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                description=description,
                location=location,
                db_id=db_id
            )
        elif action == "delete":
            db_id = args.get("event_id")
            if not db_id:
                return {"error": "Händelse-ID (event_id) krävs för att radera."}
            return await core_delete_calendar_event(db_id=db_id)
        else:
            return {"error": f"Okänd åtgärd: {action}"}
    except Exception as e:
        return {"error": f"Fel vid kalenderhantering: {str(e)}"}

async def exec_download_facebook_photos(args, progress_callback=None):
    profile_url = args.get("profile_url", "")
    limit = int(args.get("limit", 1000) or 1000)
    if not profile_url:
        return {"error": "Facebook-profilens URL saknas."}
    return await download_facebook_photos_impl(profile_url, limit, progress_callback)

async def exec_trainer_advice(args):
    goal = args.get("goal", "hälsa och motion")
    limitations = args.get("limitations", "")
    
    from backend.routes.trainer import fetch_7day_weather_forecast
    weather_forecast = await fetch_7day_weather_forecast("Stockholm")

    garmin_data = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status
                FROM garmin_health ORDER BY date DESC LIMIT 7
            ''')
            garmin_data = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error fetching Garmin data for trainer: {e}")

    strava_data = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT name, type, date, distance, moving_time, total_elevation_gain, average_heartrate, max_heartrate, calories
                FROM strava_activities ORDER BY date DESC LIMIT 7
            ''')
            strava_data = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error fetching Strava data for trainer: {e}")

    withings_data = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT date, weight, fat_ratio, bone_mass, heart_pulse, sleep_duration, steps, calories, sleep_score
                FROM withings_measurements ORDER BY date DESC LIMIT 7
            ''')
            withings_data = [dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error fetching Withings data for trainer: {e}")

    return {
        "goal": goal,
        "limitations": limitations,
        "weather_forecast_next_7_days": weather_forecast,
        "garmin_health_last_7_days": garmin_data,
        "strava_activities_last_7_activities": strava_data,
        "withings_measurements_last_7_days": withings_data
    }

async def exec_learn_topic(args, progress_callback=None):
    topic = args.get("topic", "")
    if not topic:
        return {"error": "Ämne saknas."}
    from backend.services.learning_service import learn_topic_impl
    return await learn_topic_impl(topic, progress_callback=progress_callback)

async def exec_get_learned_knowledge(args):
    query = args.get("query", "")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if query:
                cursor.execute('''
                    SELECT topic, summary, detailed_notes, sources, timestamp 
                    FROM learned_knowledge 
                    WHERE topic LIKE ? OR summary LIKE ? OR detailed_notes LIKE ?
                    ORDER BY timestamp DESC
                ''', (f"%{query}%", f"%{query}%", f"%{query}%"))
            else:
                cursor.execute('''
                    SELECT topic, summary, detailed_notes, sources, timestamp 
                    FROM learned_knowledge 
                    ORDER BY timestamp DESC
                ''')
            rows = cursor.fetchall()
            
        results = []
        for row in rows:
            sources_list = []
            try:
                if row[3]:
                    sources_list = json.loads(row[3])
            except Exception:
                pass
            results.append({
                "topic": row[0],
                "summary": row[1],
                "detailed_notes": row[2],
                "sources": sources_list,
                "timestamp": row[4]
            })
        return {"learned_knowledge": results}
    except Exception as e:
        return {"error": f"Misslyckades att hämta inlärd kunskap: {str(e)}"}

# 3. DISPATCH EXECUTOR MAP
EXECUTOR_MAP = {
    "get_weather": exec_weather,
    "google_search": exec_google_search,
    "get_garmin_health": exec_garmin_health,
    "get_withings_health": exec_withings_health,
    "get_strava_data": exec_strava_data,
    "get_strava_activity_analysis": exec_strava_activity_analysis,
    "get_strava_athlete_stats": exec_strava_athlete_stats,
    "manage_google_calendar": exec_manage_google_calendar,
    "execute_codex_code": execute_codex_code_impl,
    "run_code": execute_codex_code_impl,
    "codex_git_ops": codex_git_ops_impl,
    "codex_audit_codebase": codex_audit_codebase_impl,
    "tool_analyze_code": codex_audit_codebase_impl,
    "codex_run_and_fix": codex_run_and_fix_impl,
    "download_facebook_photos": exec_download_facebook_photos,
    "get_personal_trainer_advice": exec_trainer_advice,
    "learn_topic": exec_learn_topic,
    "get_learned_knowledge": exec_get_learned_knowledge,
}

async def execute_tool(name: str, args: dict, progress_callback=None) -> dict:
    """Invokes the appropriate executor function for the given tool name."""
    executor = EXECUTOR_MAP.get(name)
    if not executor:
        return {"error": f"Tool '{name}' is not registered in the system registry."}
    
    import inspect
    sig = inspect.signature(executor)
    if "progress_callback" in sig.parameters:
        return await executor(args, progress_callback=progress_callback)
    return await executor(args)

# HELPER MATHEMATICAL ROUNDING FUNCTION
def Math_round(val):
    if val is None:
        return None
    return int(val + 0.5) if val >= 0 else int(val - 0.5)
