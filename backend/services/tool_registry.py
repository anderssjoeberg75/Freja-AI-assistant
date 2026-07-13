"""Unified Tool Registry for F.R.E.J.A.

Defines all tool declarations in Gemini format and implements their execution in Python.
Used by both the web frontend (via API) and the Telegram bot.

Layout of this module:
  1. TOOL_DECLARATIONS    - the JSON schema Gemini sees when deciding which tool to call.
  2. TOOL_PERMISSION_KEYS - maps each tool name to the settings flag that enables it.
  3. Tool executors        - one `exec_*` coroutine per tool, doing the actual work.
  4. EXECUTOR_MAP          - name -> executor lookup used by `execute_tool`.
  5. execute_tool          - dispatch entry point used by the chat route and the Telegram bot.

Note that `execute_tool` itself does NOT enforce permissions. The permission gate lives in
`backend/routes/tools.py` (`is_tool_execution_authorized`), which runs before dispatch on the
HTTP path. The Telegram bot calls `execute_tool` directly and therefore bypasses that gate.

Language convention: every string in this file is English, including tool descriptions and
tool results. Freja still answers the user in Swedish - that is enforced by the system prompts
(see `client/gemini.js` and `backend/services/telegram_service.py`), which instruct the model to
translate tool output into Swedish before replying.
"""

import datetime
import json
import urllib.parse
import httpx
from starlette.concurrency import run_in_threadpool
from backend.database import get_db_connection, get_api_key

# Import backend business logic routines
from backend.services.search_service import perform_search
from backend.routes.garmin import run_garmin_sync_flow, get_garmin_data
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
    run_subprocess_exec,
)
from backend.services.facebook_service import download_facebook_photos_impl
from backend.services.weather_codes import describe_weather_code

# ---------------------------------------------------------------------------
# 1. TOOL DECLARATIONS (Gemini JSON format)
#
# This list is sent verbatim to Gemini as `tools[0].functionDeclarations`. The model
# picks a tool purely from the `name` and `description` fields, so keep descriptions
# precise and mention sensible defaults - the model copies them when the user is vague.
# Adding a tool here is not enough: it also needs an entry in EXECUTOR_MAP (otherwise
# `execute_tool` returns "not registered") and normally one in TOOL_PERMISSION_KEYS
# (a tool missing from that dict runs without any permission gate).
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {
        "name": "get_weather",
        "description": "Gets the current weather for a given city or geographic location.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "location": {
                    "type": "STRING",
                    "description": "Name of the city or place to look up, e.g. Stockholm, Gothenburg, London."
                }
            },
            "required": ["location"]
        }
    },
    {
        "name": "google_search",
        "description": "Searches the web for information, news or facts.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "The query to search for on Google."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_garmin_health",
        "description": "Gets the user's latest Garmin health and training data (steps, sleep, resting heart rate, calories, body battery, HRV, recovery time, training status and workouts). Defaults to 1 day (only the last 24 hours) unless the user explicitly asks for a longer period such as the last week.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {
                    "type": "INTEGER",
                    "description": "Number of days of history to fetch (default is 1, i.e. only the most recent day)."
                }
            }
        }
    },
    {
        "name": "get_withings_health",
        "description": "Gets the user's latest Withings measurements including weight, body composition, heart rate, sleep statistics (score, duration) and daily activity (steps, calories). The 'days' parameter sets how many days of history to fetch (default 7).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {
                    "type": "INTEGER",
                    "description": "Number of days of history to fetch (default 7)."
                }
            }
        }
    },
    {
        "name": "get_strava_data",
        "description": "Gets the user's latest Strava activities (name, type, distance, moving time, elevation gain, average heart rate, max heart rate and calories). Defaults to 7 days of history unless the user explicitly asks for a longer period such as 14 or 30 days.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {
                    "type": "INTEGER",
                    "description": "Number of days of history to fetch (default is 7)."
                }
            }
        }
    },
    {
        "name": "get_strava_activity_analysis",
        "description": "Gets lap times (laps/splits) plus heart rate and power zone distributions for one specific activity ID. This makes it possible to analyse tempo, pacing and aerobic/anaerobic load during the session.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "activity_id": {
                    "type": "STRING",
                    "description": "The unique activity ID (from Strava, e.g. obtained via get_strava_data)."
                }
            },
            "required": ["activity_id"]
        }
    },
    {
        "name": "get_strava_athlete_stats",
        "description": "Gets the user's accumulated training volume, including year-to-date (YTD) and all-time totals plus statistics for the last 4 weeks broken down by running, cycling and swimming.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "manage_google_calendar",
        "description": "Manages the user's calendar events. You can create new events, edit existing events, delete events, or list events within a given time window (days).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "Action to perform: 'list', 'create', 'edit' or 'delete'.",
                    "enum": ["list", "create", "edit", "delete"]
                },
                "event_id": {
                    "type": "INTEGER",
                    "description": "The unique database ID of the event (required for 'edit' and 'delete')."
                },
                "summary": {
                    "type": "STRING",
                    "description": "The event title or summary (required for 'create' and 'edit')."
                },
                "start_time": {
                    "type": "STRING",
                    "description": "Start time in ISO format (e.g. '2026-06-12T14:00:00', required for 'create' and 'edit')."
                },
                "end_time": {
                    "type": "STRING",
                    "description": "End time in ISO format (e.g. '2026-06-12T15:00:00', required for 'create' and 'edit')."
                },
                "description": {
                    "type": "STRING",
                    "description": "Detailed description or meeting notes (optional)."
                },
                "location": {
                    "type": "STRING",
                    "description": "Location or meeting link (optional)."
                },
                "days": {
                    "type": "INTEGER",
                    "description": "Number of days before and after today to fetch when using 'list'. Default is 30 days."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "execute_codex_code",
        "description": "Runs Python code or shell commands locally on the host machine. Used to run scripts, tests or system administration tasks.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "language": {
                    "type": "STRING",
                    "description": "The language to run: 'python' or 'shell'.",
                    "enum": ["python", "shell"]
                },
                "code": {
                    "type": "STRING",
                    "description": "The code or command to execute."
                }
            },
            "required": ["language", "code"]
        }
    },
    {
        "name": "run_code",
        "description": "Alias for execute_codex_code. Runs Python code or shell commands locally.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "language": {
                    "type": "STRING",
                    "description": "The language to run: 'python' or 'shell'.",
                    "enum": ["python", "shell"]
                },
                "code": {
                    "type": "STRING",
                    "description": "The code or command to execute."
                }
            },
            "required": ["language", "code"]
        }
    },
    {
        "name": "codex_git_ops",
        "description": "Performs git operations in the local source directory (e.g. status, log, diff, branch, pull, commit, push, checkout).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "The git action: 'status', 'log', 'diff', 'branch', 'pull', 'push', 'checkout' (existing local branches only), 'clone' (https only, clones to separate workspace) or 'commit'.",
                    "enum": ["status", "log", "diff", "branch", "pull", "push", "checkout", "clone", "commit"]
                },
                "argument": {
                    "type": "STRING",
                    "description": "Argument for the action (e.g. branch name, commit message or https repository URL)."
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "codex_audit_codebase",
        "description": "Performs a self-analysis (audit) of the source code to identify bugs, performance problems and code improvements, and saves a detailed report.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "tool_analyze_code",
        "description": "Alias for codex_audit_codebase. Performs a self-analysis (audit) of the source code.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "codex_run_and_fix",
        "description": "Runs a command and automatically tries to repair the source code in the given file if the command/test fails.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {
                    "type": "STRING",
                    "description": "The test command to run, e.g. 'pytest tests/test_file.py'. For security reasons, direct python/shell interpreter invocations (python, python3, py) are blocked in this channel - use a test runner such as pytest."
                },
                "file_path": {
                    "type": "STRING",
                    "description": "Relative path to the file to auto-repair on failure, e.g. 'backend/routes/sync.py'."
                },
                "max_retries": {
                    "type": "INTEGER",
                    "description": "Maximum number of auto-repair attempts (default 3)."
                }
            },
            "required": ["command", "file_path"]
        }
    },
    {
        "name": "download_facebook_photos",
        "description": "Downloads photos from a user's Facebook profile or photo gallery (e.g. .../photos_by) using Playwright. Fetches the images and saves them locally.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "profile_url": {
                    "type": "STRING",
                    "description": "The full URL to the Facebook profile's photos, e.g. https://www.facebook.com/profile.php?id=61581510724534&sk=photos_by"
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Maximum number of images to download (default is 1000)."
                }
            },
            "required": ["profile_url"]
        }
    },
    {
        "name": "get_personal_trainer_advice",
        "description": "Fetches the user's health and training data (from Garmin, Strava and Withings) and compiles personal training advice, tips and a training plan based on the user's stated goal.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal": {
                    "type": "STRING",
                    "description": "The user's training goal or focus area (e.g. 'lose weight', 'improve running', 'strength training')."
                },
                "limitations": {
                    "type": "STRING",
                    "description": "Any injuries, illnesses or physical limitations (e.g. 'exercise-induced asthma', 'sensitive knees')."
                }
            },
            "required": ["goal"]
        }
    },
    {
        "name": "learn_topic",
        "description": "Searches the web and learns everything about a given topic (e.g. growing onions). Stores the acquired knowledge in the database for future use.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic": {
                    "type": "STRING",
                    "description": "The topic or search query Freja should learn about (e.g. 'growing onions')."
                }
            },
            "required": ["topic"]
        }
    },
    {
        "name": "get_learned_knowledge",
        "description": "Retrieves previously learned knowledge from the database, filtered by keyword or topic, in order to answer the user's questions.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Optional search query or topic keyword used to filter stored knowledge (e.g. 'onions')."
                }
            }
        }
    },
    {
        "name": "system_update",
        "description": "Downloads the latest code from GitHub (git pull) and restarts F.R.E.J.A. to apply the updates.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "read_project_file",
        "description": "Reads the contents of a source file or audit report inside the project (e.g. 'docs/code_audit_20260709.md' or 'backend/routes/settings.py'). Blocked for files holding sensitive data such as databases or .env files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path": {
                    "type": "STRING",
                    "description": "Relative path to the file inside the project directory."
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "run_windows_command",
        "description": "Performs system actions on the user's Windows computer, such as launching applications (open_app), opening web addresses (open_url), opening folders in Explorer (open_folder) or running Windows commands (run_cmd).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action_type": {
                    "type": "STRING",
                    "description": "The type of action to perform.",
                    "enum": ["open_app", "open_url", "open_folder", "run_cmd"]
                },
                "target": {
                    "type": "STRING",
                    "description": "The target of the action (e.g. 'notepad.exe', 'https://google.com', 'C:\\Pictures' or 'ipconfig')."
                }
            },
            "required": ["action_type", "target"]
        }
    },
    {
        "name": "publish_instagram_post",
        "description": "Publishes a photo or a reel/video with a caption to the user's linked Instagram Business/Creator account. The media URL must be a publicly accessible direct link (image for a photo, video for a reel).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "media_url": {
                    "type": "STRING",
                    "description": "The public URL of the photo or video to publish."
                },
                "caption": {
                    "type": "STRING",
                    "description": "The caption/text description for the Instagram post."
                },
                "media_type": {
                    "type": "STRING",
                    "description": "The kind of media being published: 'IMAGE' for a photo (default) or 'REELS' for a video/reel.",
                    "enum": ["IMAGE", "REELS"]
                }
            },
            "required": ["media_url", "caption"]
        }
    },
    {
        "name": "get_instagram_feed",
        "description": "Fetches the latest published media posts from the linked Instagram account feed.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "limit": {
                    "type": "INTEGER",
                    "description": "Maximum number of media items to return (default 5)."
                }
            }
        }
    },
    {
        "name": "get_instagram_post_comments",
        "description": "Retrieves comments on a specific Instagram media post.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "media_id": {
                    "type": "STRING",
                    "description": "The unique ID of the media post."
                }
            },
            "required": ["media_id"]
        }
    },
    {
        "name": "reply_to_instagram_comment",
        "description": "Posts a reply comment to an existing comment on an Instagram post.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "comment_id": {
                    "type": "STRING",
                    "description": "The unique ID of the comment to reply to."
                },
                "text": {
                    "type": "STRING",
                    "description": "The reply comment text message."
                }
            },
            "required": ["comment_id", "text"]
        }
    }
]

# ---------------------------------------------------------------------------
# 2. TOOL PERMISSION KEYS
#
# Each tool is gated by a settings row named here. The keys mirror the localStorage keys
# used by the frontend toggles. `backend/routes/tools.py` reads the row and only treats the
# literal string "true" as permission granted - a missing row means DENIED, and the user is
# prompted to allow the call once. A tool absent from this dict entirely has no gate at all.
# ---------------------------------------------------------------------------
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
    "system_update": "freja_tool_system_update_allowed",
    "read_project_file": "freja_tool_read_project_file_allowed",
    "run_windows_command": "freja_tool_run_windows_command_allowed",
    "publish_instagram_post": "freja_tool_publish_instagram_post_allowed",
    "get_instagram_feed": "freja_tool_get_instagram_feed_allowed",
    "get_instagram_post_comments": "freja_tool_get_instagram_post_comments_allowed",
    "reply_to_instagram_comment": "freja_tool_reply_to_instagram_comment_allowed",
}

# ---------------------------------------------------------------------------
# 3. TOOL EXECUTORS
#
# Every executor takes the raw `args` dict Gemini produced and returns a JSON-serialisable
# dict that is fed straight back to the model as the function response. Executors never
# raise: an unexpected failure is reported as {"error": "..."} so the model can explain
# the problem to the user instead of the whole turn dying.
# ---------------------------------------------------------------------------

async def exec_weather(args):
    """Resolves a place name to coordinates, then reads the current conditions there."""
    location = args.get("location", "Stockholm")
    try:
        # Step 1: geocode the free-text place name into lat/lon.
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=sv&format=json"
        async with httpx.AsyncClient() as client:
            res = await client.get(geo_url, timeout=8.0)
            res.raise_for_status()
            geo_data = res.json()

        results = geo_data.get('results')
        if not results:
            return {"error": f"Could not find the location: '{location}'."}

        first = results[0]
        lat = first['latitude']
        lon = first['longitude']
        name = first['name']
        country = first.get('country', '')

        # Step 2: read current conditions at those coordinates.
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m&wind_speed_unit=ms&timezone=auto"
        async with httpx.AsyncClient() as client:
            res = await client.get(weather_url, timeout=8.0)
            res.raise_for_status()
            weather_data = res.json()

        current = weather_data.get('current')
        if not current:
            return {"error": "No weather data was returned."}

        desc = describe_weather_code(current.get('weather_code', 0))

        return {
            "location": f"{name}, {country}",
            "temperature": f"{current.get('temperature_2m')}°C",
            "feels_like": f"{current.get('apparent_temperature')}°C",
            "description": desc,
            "humidity": f"{current.get('relative_humidity_2m')}%",
            "wind_speed": f"{current.get('wind_speed_10m')} m/s",
            "is_day": "Day" if current.get('is_day') == 1 else "Night"
        }
    except Exception as e:
        return {"error": f"Failed to fetch weather data: {str(e)}"}

async def exec_google_search(args):
    query = args.get("query", "")
    if not query:
        return {"error": "Search query is missing."}
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
    sync_status = "not performed"
    sync_message = ""
    
    # 1. Retrieve keys and sync
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    
    if email and password:
        # Check if today's data already exists in the database
        has_today_data = False
        try:
            today_str = datetime.date.today().strftime('%Y-%m-%d')
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM garmin_health WHERE date = ?", (today_str,))
                if cursor.fetchone()[0] > 0:
                    has_today_data = True
        except Exception as db_err:
            print(f"[Garmin Tool] Error checking db for today's Garmin data: {db_err}")

        if has_today_data:
            sync_status = "success"
            sync_message = "Garmin sync skipped (data already exists in database)."
            print("[Garmin Tool] Today's data already exists in database. Skipping API sync.")
        elif is_sync_recent("garmin"):
            sync_status = "success"
            sync_message = "Garmin sync skipped (recently updated)."
            print("[Garmin Tool] Recent sync found. Skipping API sync, using cached DB data.")
        else:
            try:
                from backend.services.task_queue import enqueue_task
                await enqueue_task(run_garmin_sync_flow, email, password, days)
                sync_status = "success"
                sync_message = "Garmin sync completed."
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
                "message": "No Garmin data was found in the database."
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
            # "Ingen" is the Swedish placeholder get_garmin_data() substitutes for a NULL
            # workout_type, i.e. a rest day. Anything else counts as a real workout.
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
        return {"error": f"Could not fetch Garmin data: {str(e)}"}

async def exec_withings_health(args):
    days = int(args.get("days", 7) or 7)
    sync_status = "not performed"
    sync_message = ""
    
    # 1. Retrieve keys and sync synchronously
    client_id = get_api_key('freja_withings_client_id') or ""
    client_secret = get_api_key('freja_withings_client_secret') or ""
    refresh_token = get_api_key('freja_withings_refresh_token') or ""

    if client_id and client_secret and refresh_token:
        if is_sync_recent("withings"):
            sync_status = "success"
            sync_message = "Withings sync skipped (recently updated)."
            print("[Withings Tool] Recent sync found. Skipping API sync, using cached DB data.")
        else:
            try:
                await run_withings_sync_task(client_id, client_secret, refresh_token, days)
                sync_status = "success"
                sync_message = "Withings sync completed."
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
                "message": "No Withings data was found in the database."
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
        return {"error": f"Could not fetch Withings data: {str(e)}"}

async def exec_strava_data(args):
    days = int(args.get("days", 7) or 7)
    sync_status = "not performed"
    sync_message = ""
    
    # 1. Retrieve keys and sync synchronously
    client_id = get_api_key('freja_strava_client_id') or ""
    client_secret = get_api_key('freja_strava_client_secret') or ""
    refresh_token = get_api_key('freja_strava_refresh_token') or ""

    if client_id and client_secret and refresh_token:
        if is_sync_recent("strava"):
            sync_status = "success"
            sync_message = "Strava sync skipped (recently updated)."
            print("[Strava Tool] Recent sync found. Skipping API sync, using cached DB data.")
        else:
            try:
                await run_strava_sync_task(client_id, client_secret, refresh_token, days)
                sync_status = "success"
                sync_message = "Strava sync completed."
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
                "message": "No Strava activities were found in the database."
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
        return {"error": f"Could not fetch Strava activities: {str(e)}"}

async def exec_strava_activity_analysis(args):
    activity_id = args.get("activity_id", "")
    if not activity_id:
        return {"error": "Activity ID is missing."}
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
                "message": f"Found {len(events)} calendar events.",
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
                return {"error": "Title (summary), start_time and end_time are required."}
                
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
                return {"error": "The event ID (event_id) is required in order to delete."}
            return await core_delete_calendar_event(db_id=db_id)
        else:
            return {"error": f"Unknown action: {action}"}
    except Exception as e:
        return {"error": f"Calendar operation failed: {str(e)}"}

async def exec_download_facebook_photos(args, progress_callback=None):
    profile_url = args.get("profile_url", "")
    limit = int(args.get("limit", 1000) or 1000)
    if not profile_url:
        return {"error": "The Facebook profile URL is missing."}
    return await download_facebook_photos_impl(profile_url, limit, progress_callback)

async def exec_trainer_advice(args):
    """Gathers the raw health/training/weather context the model needs to write a plan.

    This tool deliberately returns data, not advice: Gemini composes the actual coaching
    text (in Swedish) from the payload. The `/api/trainer/*` routes are the ones that call
    a second model pass with a structured JSON schema."""
    goal = args.get("goal", "health and fitness")
    limitations = args.get("limitations", "")

    # Imported lazily: backend.routes.trainer imports google_calendar, which would
    # otherwise pull a heavier import chain in at module load.
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
        return {"error": "Topic is missing."}
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
        return {"error": f"Failed to fetch learned knowledge: {str(e)}"}


async def exec_system_update(args):
    """Executes git pull from GitHub and schedules a process exit/restart.

    The restart is deliberately delayed ~1.5s so this function can return its result to
    Gemini (and the user can be told an update is starting) before the process dies.
    Coming back up is the supervisor's job - systemd `Restart=always` on Linux, or the
    Task Scheduler restart settings on Windows. Without a supervisor, Freja stays down."""
    import os
    import asyncio
    from backend.config import PROJECT_ROOT

    print("[SYSTEM UPDATE] Initiating remote codebase update via git pull...")
    res = await run_subprocess_exec(["git", "pull"], cwd=str(PROJECT_ROOT))

    output = res.get("stdout", "").strip()
    errors = res.get("stderr", "").strip()
    full_log = output + ("\n" + errors if errors else "")

    if res.get("exit_code", -1) != 0:
        return {"error": f"Git pull failed (exit code {res.get('exit_code')}): {full_log}"}

    print("[SYSTEM UPDATE] Git pull successful. Scheduling uvicorn process restart.")

    async def _delayed_restart():
        await asyncio.sleep(1.5)
        os._exit(0)

    asyncio.create_task(_delayed_restart())
    return {
        "status": "success",
        "message": "Update downloaded from GitHub. F.R.E.J.A. is restarting to apply the changes...",
        "log": full_log
    }


async def exec_read_project_file(args):
    """Safely reads the contents of a non-sensitive codebase or audit file."""
    import os
    from backend.config import PROJECT_ROOT
    from backend.services.codex_service import (
        resolve_within_project,
        redact_secrets,
        SENSITIVE_FILENAME_MARKERS
    )

    file_path = args.get("file_path", "").strip()
    if not file_path:
        return {"error": "File name/path is missing."}

    lower_path = file_path.lower()
    # First gate: reject on the requested name before touching the filesystem, so a
    # secret-bearing path is refused even if it does not exist yet.
    if (
        any(marker in lower_path for marker in SENSITIVE_FILENAME_MARKERS) or
        lower_path.endswith(('.db', '.db-wal', '.db-shm', '.key', '.env'))
    ):
        return {"error": "Security error: Access to this file is blocked for security reasons."}

    try:
        # Second gate: resolve_within_project() raises if the path escapes PROJECT_ROOT
        # (e.g. via '..' or a symlink), which is what stops directory traversal.
        abs_path = resolve_within_project(file_path)
        if not os.path.exists(abs_path):
            return {"error": f"The file '{file_path}' was not found."}
        if os.path.isdir(abs_path):
            return {"error": f"The path '{file_path}' is a directory, not a file."}

        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Third gate: even an allowed file may embed a key, so scrub before returning.
        safe_content = redact_secrets(content)
        return {
            "file_path": file_path,
            "content": safe_content
        }
    except Exception as e:
        return {"error": f"Failed to read the file: {str(e)}"}


async def exec_run_windows_command(args):
    """Executes actions on the user's host Windows machine safely."""
    import os
    import re
    import webbrowser
    import subprocess
    import asyncio

    if os.name != "nt":
        return {"error": "This tool is currently only available on Windows systems."}

    action_type = args.get("action_type", "").strip()
    target = args.get("target", "").strip()

    if not action_type or not target:
        return {"error": "The 'action_type' and 'target' parameters are required."}

    if action_type == "open_app":
        # os.startfile launches files, executables and registered protocol handlers.
        try:
            os.startfile(target)
            return {"status": "success", "message": f"Launched the application '{target}'."}
        except Exception as e:
            return {"error": f"Could not launch the application '{target}': {str(e)}"}

    elif action_type == "open_url":
        # Restrict the scheme: 'file:' or 'javascript:' would turn this into a local-file
        # read or script execution primitive via the default browser.
        target_lower = target.lower()
        if not (target_lower.startswith("http://") or target_lower.startswith("https://") or target_lower.startswith("mailto:")):
            return {"error": "Security error: Only http://, https:// and mailto: addresses are allowed."}
        try:
            webbrowser.open(target)
            return {"status": "success", "message": f"Opened the web address '{target}'."}
        except Exception as e:
            return {"error": f"Could not open the web address '{target}': {str(e)}"}

    elif action_type == "open_folder":
        # Open a directory path in Windows Explorer.
        if not os.path.exists(target):
            return {"error": f"The path '{target}' was not found."}
        if not os.path.isdir(target):
            return {"error": f"The path '{target}' is not a folder/directory."}
        try:
            os.startfile(target)
            return {"status": "success", "message": f"Opened the folder '{target}' in Explorer."}
        except Exception as e:
            return {"error": f"Could not open the folder '{target}': {str(e)}"}

    elif action_type == "run_cmd":
        # The command is passed to a shell, so this is a substring denylist, not a parser.
        # It blocks the obvious destructive verbs (wiping disks, deleting files, changing
        # accounts/ACLs, powering the machine off) before the string ever reaches cmd.exe.
        FORBIDDEN_KEYWORDS = {
            "format", "del", "rmdir", "rd", "erase", "mkfs", "dd",
            "shutdown", "restart", "logoff", "abort",
            "net user", "net localgroup", "net share",
            "reg delete", "reg add", "reg import",
            "attrib", "cacls", "takeown", "icacls", "rm -rf"
        }
        cmd_lower = target.lower()
        for forbidden in FORBIDDEN_KEYWORDS:
            if forbidden in cmd_lower:
                return {"error": f"Security error: The command contains the blocked keyword '{forbidden}'."}

        try:
            proc = await asyncio.create_subprocess_shell(
                target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            out_str = stdout.decode('utf-8', errors='ignore').strip()
            err_str = stderr.decode('utf-8', errors='ignore').strip()

            return {
                "status": "success" if proc.returncode == 0 else "error",
                "exit_code": proc.returncode,
                "stdout": out_str,
                "stderr": err_str
            }
        except Exception as e:
            return {"error": f"Could not run the command: {str(e)}"}

    else:
        return {"error": f"Unknown action type '{action_type}'."}


# --- Instagram executors (thin wrappers over backend.services.instagram_service) ---

async def exec_publish_instagram_post(args):
    from backend.services.instagram_service import publish_media
    media_url = (args.get("media_url") or args.get("image_url") or "").strip()
    caption = args.get("caption", "").strip()
    media_type = (args.get("media_type") or "IMAGE").strip().upper()
    return await publish_media(media_url, caption, media_type=media_type)

async def exec_get_instagram_feed(args):
    from backend.services.instagram_service import get_recent_media
    limit = int(args.get("limit", 5) or 5)
    return await get_recent_media(limit)

async def exec_get_instagram_post_comments(args):
    from backend.services.instagram_service import get_comments
    media_id = args.get("media_id", "").strip()
    return await get_comments(media_id)

async def exec_reply_to_instagram_comment(args):
    from backend.services.instagram_service import post_comment_reply
    comment_id = args.get("comment_id", "").strip()
    text = args.get("text", "").strip()
    return await post_comment_reply(comment_id, text)


# ---------------------------------------------------------------------------
# 4. DISPATCH EXECUTOR MAP
#
# Tool name -> executor. Aliases point at the same implementation on purpose:
# `run_code`/`execute_codex_code` and `tool_analyze_code`/`codex_audit_codebase` exist
# because Gemini reaches for both names, but they still have separate permission keys.
# ---------------------------------------------------------------------------
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
    "system_update": exec_system_update,
    "read_project_file": exec_read_project_file,
    "run_windows_command": exec_run_windows_command,
    "publish_instagram_post": exec_publish_instagram_post,
    "get_instagram_feed": exec_get_instagram_feed,
    "get_instagram_post_comments": exec_get_instagram_post_comments,
    "reply_to_instagram_comment": exec_reply_to_instagram_comment,
}


# ---------------------------------------------------------------------------
# 5. DISPATCH ENTRY POINT
# ---------------------------------------------------------------------------
async def execute_tool(name: str, args: dict, progress_callback=None) -> dict:
    """Invokes the appropriate executor function for the given tool name.

    Long-running tools (Facebook download, learn_topic) accept a `progress_callback` used
    by /api/tools/status polling. We introspect the signature rather than passing it
    unconditionally, so the short tools can keep a plain `(args)` signature."""
    executor = EXECUTOR_MAP.get(name)
    if not executor:
        return {"error": f"Tool '{name}' is not registered in the system registry."}

    import inspect
    sig = inspect.signature(executor)
    if "progress_callback" in sig.parameters:
        return await executor(args, progress_callback=progress_callback)
    return await executor(args)


def Math_round(val):
    """Rounds half away from zero, matching JavaScript's Math.round() on the frontend.

    Python's built-in round() uses banker's rounding (round-half-to-even), so round(0.5)
    is 0 and round(2.5) is 2. Health averages are rendered client-side too, and the two
    must agree."""
    if val is None:
        return None
    return int(val + 0.5) if val >= 0 else int(val - 0.5)
