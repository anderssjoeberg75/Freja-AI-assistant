"""Unified Tool Registry for F.R.E.J.A.

Defines all tool declarations in Gemini format and implements their execution in Python.
Used by both the web frontend (via API) and the Telegram bot.

Layout of this module:
  1. ToolRegistry / clean_schema - the decorator-based registry infrastructure.
  2. Tool executors              - one `exec_*` coroutine per tool, each registered once via
                                   `@registry.register(...)` carrying its declaration + gate.
  3. Imported / aliased executors - codex impls and aliases registered via `registry.add(...)`.
  4. Derived structures          - TOOL_DECLARATIONS, TOOL_PERMISSION_KEYS, EXECUTOR_MAP and
                                   execute_tool, all generated from the single registry so the
                                   three lists can no longer drift out of sync by hand.

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
from backend.services.http_client import shared_client
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
# 1. TOOL REGISTRY (decorator-based, single source of truth)
#
# Each tool is defined ONCE, via `@registry.register(...)` on its executor. The registry
# derives everything else from that single definition:
#   - TOOL_DECLARATIONS    (the Gemini functionDeclarations list)
#   - TOOL_PERMISSION_KEYS (name -> settings flag consumed by backend/routes/tools.py)
#   - the name -> executor dispatch used by execute_tool
# This removes the three hand-synced structures that used to drift apart (a tool missing
# from the permission map silently ran ungated; an executor in the wrong place 404'd).
#
# A tool's argument schema can be given either as an explicit Gemini `parameters` dict or
# as a Pydantic `args_schema` model, from which the declaration is auto-generated via
# `clean_schema()` (strips title/default/anyOf, upper-cases the JSON types for Gemini).
# `registry.execute()` centralises arg hygiene: it drops None/empty values so declared
# defaults apply, and (when an args_schema is present) validates and returns a short,
# retryable error the model can correct.
#
# The permission GATE itself still lives in backend/routes/tools.py
# (`is_tool_execution_authorized`); the registry only supplies the name -> key mapping.
# ---------------------------------------------------------------------------
import inspect
from pydantic import BaseModel, Field, ValidationError

# JSON-schema keys that Gemini's function-declaration format does not accept and that
# Pydantic emits; stripped by clean_schema().
_GEMINI_STRIP_KEYS = ("title", "default", "additionalProperties", "$defs", "definitions")


def clean_schema(schema: dict) -> dict:
    """Rewrites a JSON schema (e.g. from Pydantic) into the shape Gemini expects.

    Strips keys Gemini rejects (title/default/additionalProperties/$defs), upper-cases the
    JSON `type` names (``string`` -> ``STRING``), collapses an ``anyOf`` of a real type plus
    ``null`` (how Pydantic renders Optional[...]) down to that real type, and recurses into
    ``properties`` and array ``items``."""
    if not isinstance(schema, dict):
        return schema

    # Optional[X] renders as {"anyOf": [<X>, {"type": "null"}]} - collapse to <X>.
    if "anyOf" in schema:
        non_null = [s for s in schema["anyOf"] if s.get("type") != "null"]
        merged = dict(non_null[0]) if len(non_null) == 1 else {}
        for k, v in schema.items():
            if k != "anyOf" and k not in merged:
                merged[k] = v
        schema = merged

    out = {}
    for key, val in schema.items():
        if key in _GEMINI_STRIP_KEYS:
            continue
        if key == "type" and isinstance(val, str):
            out["type"] = val.upper()
        elif key == "properties" and isinstance(val, dict):
            out["properties"] = {k: clean_schema(v) for k, v in val.items()}
        elif key == "items" and isinstance(val, dict):
            out["items"] = clean_schema(val)
        else:
            out[key] = val
    return out


def _params_from_pydantic(args_schema) -> dict:
    """Builds a Gemini OBJECT `parameters` block from a Pydantic model class."""
    cleaned = clean_schema(args_schema.model_json_schema())
    params = {"type": "OBJECT", "properties": cleaned.get("properties", {})}
    if cleaned.get("required"):
        params["required"] = cleaned["required"]
    return params


class ToolSpec:
    """One tool's single definition: declaration + permission key + executor."""
    __slots__ = ("name", "description", "parameters", "permission_key", "executor", "args_schema")

    def __init__(self, name, description, executor, parameters=None, permission_key=None, args_schema=None):
        self.name = name
        self.description = description
        self.executor = executor
        self.permission_key = permission_key
        self.args_schema = args_schema
        if parameters is not None:
            self.parameters = parameters
        elif args_schema is not None:
            self.parameters = _params_from_pydantic(args_schema)
        else:
            self.parameters = {"type": "OBJECT", "properties": {}}

    @property
    def declaration(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _short_validation_error(exc: ValidationError) -> str:
    """Condenses a Pydantic ValidationError into a one-line, model-actionable hint."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(args)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts[:5])


class ToolRegistry:
    """Holds the single source of truth for every tool and derives the legacy structures."""

    def __init__(self):
        self._specs = {}  # name -> ToolSpec (insertion-ordered)

    def register(self, name, description, parameters=None, permission_key=None, args_schema=None):
        """Decorator: registers the decorated coroutine as the executor for `name`."""
        def _decorator(fn):
            self.add(name, description, fn, parameters, permission_key, args_schema)
            return fn
        return _decorator

    def add(self, name, description, executor, parameters=None, permission_key=None, args_schema=None):
        """Registers an executor defined elsewhere (imported impl or alias)."""
        if name in self._specs:
            raise ValueError(f"Tool '{name}' is already registered.")
        self._specs[name] = ToolSpec(name, description, executor, parameters, permission_key, args_schema)

    # --- Derived views (keep the historical public names/behaviour) ---
    @property
    def declarations(self) -> list:
        return [spec.declaration for spec in self._specs.values()]

    @property
    def permission_keys(self) -> dict:
        return {name: spec.permission_key for name, spec in self._specs.items() if spec.permission_key}

    @property
    def executor_map(self) -> dict:
        return {name: spec.executor for name, spec in self._specs.items()}

    def _hygiene(self, spec: ToolSpec, args: dict) -> dict:
        """Drops None/empty values so declared defaults apply. Unknown keys are only pruned
        on the Pydantic path (validation there is authoritative); dict-schema tools keep any
        extra keys their executors read as aliases."""
        cleaned = {k: v for k, v in (args or {}).items() if v is not None and v != ""}
        if spec.args_schema is not None:
            allowed = set(spec.args_schema.model_fields.keys())
            cleaned = {k: v for k, v in cleaned.items() if k in allowed}
        return cleaned

    async def execute(self, name: str, args: dict, progress_callback=None) -> dict:
        spec = self._specs.get(name)
        if not spec:
            return {"error": f"Tool '{name}' is not registered in the system registry."}

        call_args = self._hygiene(spec, args)
        if spec.args_schema is not None:
            try:
                model = spec.args_schema(**call_args)
                call_args = model.model_dump(exclude_none=True)
            except ValidationError as ve:
                return {"error": f"Invalid arguments for '{name}': {_short_validation_error(ve)}"}

        # Long-running tools accept a progress_callback (used by /api/tools/status polling);
        # introspect so short tools keep a plain (args) signature.
        if "progress_callback" in inspect.signature(spec.executor).parameters:
            return await spec.executor(call_args, progress_callback=progress_callback)
        return await spec.executor(call_args)


registry = ToolRegistry()

# ---------------------------------------------------------------------------
# 2. TOOL EXECUTORS
#
# Every executor takes the (hygiene-cleaned) `args` dict and returns a JSON-serialisable
# dict that is fed straight back to the model as the function response. Executors never
# raise: an unexpected failure is reported as {"error": "..."} so the model can explain
# the problem to the user instead of the whole turn dying. Each is registered once via the
# `@registry.register(...)` decorator that carries its Gemini declaration + permission key.
# ---------------------------------------------------------------------------

class WeatherArgs(BaseModel):
    location: str = Field(description="Name of the city or place to look up, e.g. Stockholm, Gothenburg, London.")


@registry.register(
    name="get_weather",
    description="Gets the current weather for a given city or geographic location.",
    permission_key="freja_tool_get_weather_allowed",
    args_schema=WeatherArgs,
)
async def exec_weather(args):
    """Resolves a place name to coordinates, then reads the current conditions there."""
    location = args.get("location", "Stockholm")
    try:
        # Step 1: geocode the free-text place name into lat/lon.
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=sv&format=json"
        async with shared_client() as client:
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
        async with shared_client() as client:
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

class SearchArgs(BaseModel):
    query: str = Field(description="The query to search for on Google.")


@registry.register(
    name="google_search",
    description="Searches the web for information, news or facts.",
    permission_key="freja_tool_google_search_allowed",
    args_schema=SearchArgs,
)
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

@registry.register(
    name="get_garmin_health",
    description="Gets the user's latest Garmin health and training data (steps, sleep, resting heart rate, calories, body battery, HRV, recovery time, training status and workouts). Defaults to 1 day (only the last 24 hours) unless the user explicitly asks for a longer period such as the last week.",
    permission_key="freja_tool_get_garmin_health_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "days": {
                "type": "INTEGER",
                "description": "Number of days of history to fetch (default is 1, i.e. only the most recent day)."
            }
        }
    },
)
async def exec_garmin_health(args):
    days = int(args.get("days", 1) or 1)
    sync_status = "not performed"
    sync_message = ""
    
    # 1. Retrieve keys and sync
    email = get_api_key('freja_garmin_email') or ""
    password = get_api_key('freja_garmin_password') or ""
    
    if email and password:
        # Force sync if fetching historical data (days > 1) or if no recent sync has run in the last 15 minutes.
        # We do not skip if today's data already exists, since steps and body battery update throughout the day.
        is_recent = is_sync_recent("garmin", max_age_hours=0.25)
        
        if is_recent and days <= 1:
            sync_status = "success"
            sync_message = "Garmin sync skipped (recently updated)."
            print("[Garmin Tool] Recent sync found in the last 15 minutes. Skipping API sync, using cached DB data.")
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
        total_stress = 0
        stress_count = 0
        total_sleep_score = 0
        sleep_score_count = 0
        total_intensity = 0
        intensity_count = 0

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
            if day.get('stress_avg') is not None:
                total_stress += day['stress_avg']
                stress_count += 1
            if day.get('sleep_score') is not None:
                total_sleep_score += day['sleep_score']
                sleep_score_count += 1
            if day.get('intensity_minutes') is not None:
                total_intensity += day['intensity_minutes']
                intensity_count += 1

        num_days = len(data)
        avg_steps = Math_round(total_steps / num_days) if num_days > 0 else 0
        avg_sleep = round(total_sleep / num_days, 1) if num_days > 0 else 0.0
        avg_hr = Math_round(total_hr / num_days) if num_days > 0 else 0
        avg_calories = Math_round(total_calories / num_days) if num_days > 0 else 0
        avg_bb = Math_round(total_bb / bb_count) if bb_count > 0 else None
        avg_hrv = Math_round(total_hrv / hrv_count) if hrv_count > 0 else None
        avg_recovery = Math_round(total_recovery / recovery_count) if recovery_count > 0 else None
        avg_stress = Math_round(total_stress / stress_count) if stress_count > 0 else None
        avg_sleep_score = Math_round(total_sleep_score / sleep_score_count) if sleep_score_count > 0 else None
        latest_vo2max = data[0].get('vo2max') if data else None

        return {
            "sync_status": sync_status,
            "sync_message": sync_message,
            "period_days": num_days,
            "latest_metrics": {
                "training_status": data[0].get('training_status') if data else None,
                "recovery_time_hours": data[0].get('recovery_time') if data else None,
                "vo2max": latest_vo2max
            },
            "averages": {
                "avg_daily_steps": avg_steps,
                "avg_sleep_hours": avg_sleep,
                "avg_resting_heart_rate": avg_hr,
                "avg_active_calories": avg_calories,
                "avg_body_battery": avg_bb,
                "avg_hrv": avg_hrv,
                "avg_recovery_time_hours": avg_recovery,
                "avg_stress": avg_stress,
                "avg_sleep_score": avg_sleep_score,
                "total_intensity_minutes": total_intensity if intensity_count > 0 else None,
                "total_workouts": workout_days,
                "total_workout_minutes": total_workout_min
            },
            "daily_logs": data
        }
    except Exception as e:
        return {"error": f"Could not fetch Garmin data: {str(e)}"}

@registry.register(
    name="get_withings_health",
    description="Gets the user's latest Withings measurements including weight, body composition, heart rate, sleep statistics (score, duration) and daily activity (steps, calories). The 'days' parameter sets how many days of history to fetch (default 7).",
    permission_key="freja_tool_get_withings_health_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "days": {
                "type": "INTEGER",
                "description": "Number of days of history to fetch (default 7)."
            }
        }
    },
)
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

@registry.register(
    name="get_strava_data",
    description="Gets the user's latest Strava activities (name, type, distance, moving time, elevation gain, average heart rate, max heart rate and calories). Defaults to 7 days of history unless the user explicitly asks for a longer period such as 14 or 30 days.",
    permission_key="freja_tool_get_strava_data_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "days": {
                "type": "INTEGER",
                "description": "Number of days of history to fetch (default is 7)."
            }
        }
    },
)
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

@registry.register(
    name="get_strava_activity_analysis",
    description="Gets lap times (laps/splits) plus heart rate and power zone distributions for one specific activity ID. This makes it possible to analyse tempo, pacing and aerobic/anaerobic load during the session.",
    permission_key="freja_tool_get_strava_activity_analysis_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "activity_id": {
                "type": "STRING",
                "description": "The unique activity ID (from Strava, e.g. obtained via get_strava_data)."
            }
        },
        "required": ["activity_id"]
    },
)
async def exec_strava_activity_analysis(args):
    activity_id = args.get("activity_id", "")
    if not activity_id:
        return {"error": "Activity ID is missing."}
    return await get_strava_activity_details(id=activity_id)

@registry.register(
    name="get_strava_athlete_stats",
    description="Gets the user's accumulated training volume, including year-to-date (YTD) and all-time totals plus statistics for the last 4 weeks broken down by running, cycling and swimming.",
    permission_key="freja_tool_get_strava_athlete_stats_allowed",
    parameters={"type": "OBJECT", "properties": {}},
)
async def exec_strava_athlete_stats(args):
    return await get_strava_athlete_stats()

@registry.register(
    name="manage_google_calendar",
    description="Manages the user's calendar events. You can create new events, edit existing events, delete events, or list events within a given time window (days).",
    permission_key="freja_tool_manage_google_calendar_allowed",
    parameters={
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
    },
)
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

@registry.register(
    name="download_facebook_photos",
    description="Downloads photos from a user's Facebook profile or photo gallery (e.g. .../photos_by) using Playwright. Fetches the images and saves them locally.",
    permission_key="freja_tool_download_facebook_photos_allowed",
    parameters={
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
    },
)
async def exec_download_facebook_photos(args, progress_callback=None):
    profile_url = args.get("profile_url", "")
    limit = int(args.get("limit", 1000) or 1000)
    if not profile_url:
        return {"error": "The Facebook profile URL is missing."}
    return await download_facebook_photos_impl(profile_url, limit, progress_callback)

@registry.register(
    name="get_personal_trainer_advice",
    description="Fetches the user's health and training data (from Garmin, Strava and Withings) and compiles personal training advice, tips and a training plan based on the user's stated goal.",
    permission_key="freja_tool_get_personal_trainer_advice_allowed",
    parameters={
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
    },
)
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
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, sleep_score
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

@registry.register(
    name="learn_topic",
    description="Searches the web and learns everything about a given topic (e.g. growing onions). Stores the acquired knowledge in the database for future use.",
    permission_key="freja_tool_learn_topic_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "topic": {
                "type": "STRING",
                "description": "The topic or search query Freja should learn about (e.g. 'growing onions')."
            }
        },
        "required": ["topic"]
    },
)
async def exec_learn_topic(args, progress_callback=None):
    topic = args.get("topic", "")
    if not topic:
        return {"error": "Topic is missing."}
    from backend.services.learning_service import learn_topic_impl
    return await learn_topic_impl(topic, progress_callback=progress_callback)

@registry.register(
    name="get_learned_knowledge",
    description="Retrieves previously learned knowledge from the database, filtered by keyword or topic, in order to answer the user's questions.",
    permission_key="freja_tool_get_learned_knowledge_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "Optional search query or topic keyword used to filter stored knowledge (e.g. 'onions')."
            }
        }
    },
)
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


@registry.register(
    name="system_update",
    description="Downloads the latest code from GitHub (git pull) and restarts F.R.E.J.A. to apply the updates.",
    permission_key="freja_tool_system_update_allowed",
    parameters={"type": "OBJECT", "properties": {}},
)
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


@registry.register(
    name="read_project_file",
    description="Reads the contents of a source file or audit report inside the project (e.g. 'docs/code_audit_20260709.md' or 'backend/routes/settings.py'). Blocked for files holding sensitive data such as databases or .env files.",
    permission_key="freja_tool_read_project_file_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Relative path to the file inside the project directory."
            }
        },
        "required": ["file_path"]
    },
)
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


@registry.register(
    name="run_windows_command",
    description="Performs system actions on the user's Windows computer, such as launching applications (open_app), opening web addresses (open_url), opening folders in Explorer (open_folder) or running Windows commands (run_cmd).",
    permission_key="freja_tool_run_windows_command_allowed",
    parameters={
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
    },
)
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
        import shlex
        try:
            # Parse the command safely as a structured argument list
            args_list = shlex.split(target, posix=False)
        except Exception as pe:
            return {"error": f"Invalid command format: {str(pe)}"}

        if not args_list:
            return {"error": "Empty command string."}

        # Clean/sanitize arguments (strip enclosing quotes if posix=False preserved them)
        cmd_args = [arg.strip('"\'') for arg in args_list]
        base_cmd = cmd_args[0].lower()
        
        # Strip path / file extension (e.g. C:\Windows\System32\ping.exe -> ping)
        base_cmd_name = os.path.basename(base_cmd).rstrip(".exe")

        # Strict allowlist of safe executables to prevent arbitrary command execution
        SAFE_EXECUTABLES = {"ping", "ipconfig", "systeminfo", "hostname", "whoami", "tasklist", "netstat", "git", "echo"}
        if base_cmd_name not in SAFE_EXECUTABLES:
            return {"error": f"Security error: The executable '{base_cmd_name}' is not in the list of approved commands."}

        # Prevent directory traversal or local hijacked binary execution
        if "/" in base_cmd or "\\" in base_cmd:
            return {"error": "Security error: Absolute or relative paths are not allowed in the command executable."}

        try:
            # Execute command directly with safe structured argument array (bypassing the shell)
            proc = await asyncio.create_subprocess_exec(
                cmd_args[0],
                *cmd_args[1:],
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

@registry.register(
    name="publish_instagram_post",
    description="Publishes a photo or a reel/video with a caption to the user's linked Instagram Business/Creator account. The media URL must be a publicly accessible direct link (image for a photo, video for a reel).",
    permission_key="freja_tool_publish_instagram_post_allowed",
    parameters={
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
    },
)
async def exec_publish_instagram_post(args):
    from backend.services.instagram_service import publish_media
    media_url = (args.get("media_url") or args.get("image_url") or "").strip()
    caption = args.get("caption", "").strip()
    media_type = (args.get("media_type") or "IMAGE").strip().upper()
    return await publish_media(media_url, caption, media_type=media_type)

@registry.register(
    name="get_instagram_feed",
    description="Fetches the latest published media posts from the linked Instagram account feed.",
    permission_key="freja_tool_get_instagram_feed_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "limit": {
                "type": "INTEGER",
                "description": "Maximum number of media items to return (default 5)."
            }
        }
    },
)
async def exec_get_instagram_feed(args):
    from backend.services.instagram_service import get_recent_media
    limit = int(args.get("limit", 5) or 5)
    return await get_recent_media(limit)

@registry.register(
    name="get_instagram_post_comments",
    description="Retrieves comments on a specific Instagram media post.",
    permission_key="freja_tool_get_instagram_post_comments_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "media_id": {
                "type": "STRING",
                "description": "The unique ID of the media post."
            }
        },
        "required": ["media_id"]
    },
)
async def exec_get_instagram_post_comments(args):
    from backend.services.instagram_service import get_comments
    media_id = args.get("media_id", "").strip()
    return await get_comments(media_id)

@registry.register(
    name="reply_to_instagram_comment",
    description="Posts a reply comment to an existing comment on an Instagram post.",
    permission_key="freja_tool_reply_to_instagram_comment_allowed",
    parameters={
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
    },
)
async def exec_reply_to_instagram_comment(args):
    from backend.services.instagram_service import post_comment_reply
    comment_id = args.get("comment_id", "").strip()
    text = args.get("text", "").strip()
    return await post_comment_reply(comment_id, text)


async def _build_trainer_context_summary(days: int = 14) -> dict:
    """Builds a comprehensive summary of active training plan, scheduled workouts,
    recent running history (Garmin/Strava), health recovery data, and active injuries."""
    result = {
        "status": "success",
        "active_plan": None,
        "scheduled_workouts": [],
        "recent_runs": [],
        "health_summary": [],
        "injuries": []
    }

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 1. Fetch active training plan
            cursor.execute('''
                SELECT id, date, goal, advice_text, limitations
                FROM trainer_plans
                ORDER BY id DESC LIMIT 1
            ''')
            plan_row = cursor.fetchone()
            if plan_row:
                plan_id, p_date, p_goal, p_advice, p_limitations = plan_row
                plan_json = {}
                if p_advice:
                    try:
                        cleaned = p_advice.replace("```json", "").replace("```", "").strip()
                        plan_json = json.loads(cleaned)
                    except Exception:
                        plan_json = {"summary": p_advice[:300]}

                result["active_plan"] = {
                    "plan_id": plan_id,
                    "date": p_date,
                    "goal": p_goal,
                    "limitations": p_limitations,
                    "summary": plan_json.get("summary", ""),
                    "weekly_focus": plan_json.get("weekly_focus", ""),
                    "workouts_defined": plan_json.get("workouts", [])
                }

            # 2. Fetch scheduled workouts for current week
            try:
                today = datetime.date.today()
                monday = today - datetime.timedelta(days=today.weekday())
                sunday = monday + datetime.timedelta(days=6)

                cursor.execute('''
                    SELECT b.id, b.plan_id, b.workout_date, b.week, p.advice_text
                    FROM trainer_bookings b
                    JOIN trainer_plans p ON b.plan_id = p.id
                    WHERE b.workout_date >= ? AND b.workout_date <= ?
                    ORDER BY b.workout_date ASC
                ''', (monday.isoformat(), sunday.isoformat()))
                rows = cursor.fetchall()
                for b_row in rows:
                    b_id, p_id, w_date_str, w_week, advice_text = b_row
                    w_title = "Scheduled Workout"
                    try:
                        p_obj = json.loads(advice_text.replace("```json", "").replace("```", "").strip())
                        w_list = p_obj.get("workouts", [])
                        w_date = datetime.datetime.strptime(w_date_str, "%Y-%m-%d").date()
                        w_dow = w_date.weekday()
                        day_offsets = {"måndag": 0, "tisdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lördag": 5, "söndag": 6}
                        for w in w_list:
                            d_name = str(w.get("day", "")).lower()
                            if day_offsets.get(d_name) == w_dow:
                                w_title = f"{w.get('activity_type', 'Träning')}: {w.get('title', 'Pass')} ({w.get('duration_minutes', 0)} min)"
                                break
                    except Exception:
                        pass
                    result["scheduled_workouts"].append({
                        "booking_id": b_id,
                        "plan_id": p_id,
                        "workout_date": w_date_str,
                        "workout_summary": w_title
                    })
            except Exception as b_err:
                print(f"[TRAINER CONTEXT] Booking fetch error: {b_err}")

            # 3. Fetch active injuries
            try:
                cursor.execute('''
                    SELECT area, description, severity, date_logged
                    FROM trainer_injury_logs
                    ORDER BY date_logged DESC
                    LIMIT 5
                ''')
                for inj in cursor.fetchall():
                    result["injuries"].append({
                        "area": inj[0],
                        "description": inj[1],
                        "severity": inj[2],
                        "date_logged": inj[3]
                    })
            except Exception:
                pass

            # 4. Fetch recent run history from Strava & Garmin
            try:
                cursor.execute('''
                    SELECT name, type, date, distance, moving_time, average_heartrate, max_heartrate
                    FROM strava_activities
                    ORDER BY date DESC
                    LIMIT 10
                ''')
                for s in cursor.fetchall():
                    dist_km = round(s[3] / 1000.0, 2) if s[3] else 0
                    dur_min = round(s[4] / 60.0, 1) if s[4] else 0
                    result["recent_runs"].append({
                        "source": "Strava",
                        "name": s[0],
                        "type": s[1],
                        "date": s[2],
                        "distance_km": dist_km,
                        "duration_min": dur_min,
                        "avg_hr": s[5],
                        "max_hr": s[6]
                    })
            except Exception:
                pass

            try:
                cursor.execute('''
                    SELECT date, workout_type, workout_duration, resting_hr, body_battery, hrv, sleep_score
                    FROM garmin_health
                    ORDER BY date DESC
                    LIMIT ?
                ''', (days,))
                for g in cursor.fetchall():
                    result["health_summary"].append({
                        "date": g[0],
                        "workout": g[1],
                        "duration_min": g[2],
                        "resting_hr": g[3],
                        "body_battery": g[4],
                        "hrv": g[5],
                        "sleep_score": g[6]
                    })
            except Exception:
                pass
    except Exception as e:
        print(f"[TRAINER CONTEXT ERROR]: {e}")

    return result


@registry.register(
    name="get_trainer_workouts",
    description="Retrieves scheduled PT training sessions, active training plan goal, limitations/injuries, and recent Garmin/Strava running history so Freja can discuss workout rationale and progression with the user.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "days": {
                "type": "INTEGER",
                "description": "Number of days of history/workouts to inspect (default 14)."
            }
        }
    },
)
async def exec_get_trainer_workouts(args):
    days = int((args or {}).get("days", 14) or 14)
    return await _build_trainer_context_summary(days)


@registry.register(
    name="update_trainer_workout",
    description="Updates or adjusts a specific scheduled workout in the user's PT training plan (e.g. changing duration, title, description, or activity type based on coaching decisions).",
    parameters={
        "type": "OBJECT",
        "properties": {
            "workout_date": {
                "type": "STRING",
                "description": "Date of the workout to update in YYYY-MM-DD format (e.g. '2026-07-20')."
            },
            "duration_minutes": {
                "type": "INTEGER",
                "description": "New duration in minutes (e.g. 35)."
            },
            "title": {
                "type": "STRING",
                "description": "Updated workout title (e.g. 'Lugnt pass med gå-pauser')."
            },
            "description": {
                "type": "STRING",
                "description": "Updated detailed description and structure of the workout."
            },
            "activity_type": {
                "type": "STRING",
                "description": "Activity type (e.g. 'Löpning', 'Styrketräning', 'Aktiv vila', 'Cykling')."
            }
        },
        "required": ["workout_date"]
    },
)
async def exec_update_trainer_workout(args):
    w_date = str(args.get("workout_date", "")).strip()
    new_dur = args.get("duration_minutes")
    new_title = str(args.get("title", "")).strip()
    new_desc = str(args.get("description", "")).strip()
    new_act = str(args.get("activity_type", "")).strip()

    if not w_date:
        return {"error": "workout_date is required."}

    updated_plan = False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, advice_text FROM trainer_plans
            ORDER BY id DESC LIMIT 1
        ''')
        plan_row = cursor.fetchone()
        if plan_row:
            plan_id, advice_text = plan_row
            try:
                cleaned = advice_text.replace("```json", "").replace("```", "").strip()
                plan_json = json.loads(cleaned)
                workouts = plan_json.get("workouts", [])

                day_offsets = {"måndag": 0, "tisdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lördag": 5, "söndag": 6}
                target_dt = datetime.datetime.strptime(w_date, "%Y-%m-%d").date()
                target_dow = target_dt.weekday()

                for w in workouts:
                    d_name = str(w.get("day", "")).lower()
                    if day_offsets.get(d_name) == target_dow:
                        if new_dur is not None: w["duration_minutes"] = int(new_dur)
                        if new_title: w["title"] = new_title
                        if new_desc: w["description"] = new_desc
                        if new_act: w["activity_type"] = new_act
                        updated_plan = True
                        break

                if updated_plan:
                    cursor.execute('''
                        UPDATE trainer_plans SET advice_text = ? WHERE id = ?
                    ''', (json.dumps(plan_json, ensure_ascii=False, indent=2), plan_id))
                    conn.commit()
            except Exception as p_err:
                print(f"[UPDATE WORKOUT] Plan update error: {p_err}")

    events_updated = 0
    try:
        from backend.routes.google_calendar import core_get_calendar_data, core_save_calendar_event
        events = core_get_calendar_data(14)
        for ev in events:
            if (ev.get("start_time") or "")[:10] == w_date:
                summary = new_title or ev.get("summary")
                if new_act and not summary.startswith("💪"):
                    summary = f"💪 {new_act}: {summary}"
                start_dt = ev.get("start_time")
                end_dt = ev.get("end_time")
                if new_dur and start_dt and len(start_dt) >= 16:
                    s_time = datetime.datetime.strptime(start_dt[:16], "%Y-%m-%dT%H:%M")
                    e_time = s_time + datetime.timedelta(minutes=int(new_dur))
                    end_dt = e_time.strftime("%Y-%m-%dT%H:%M")

                await core_save_calendar_event(
                    summary=summary,
                    start_time=start_dt[:16],
                    end_time=end_dt[:16] if end_dt else start_dt[:16],
                    description=new_desc or ev.get("description", ""),
                    location=ev.get("location", ""),
                    db_id=ev.get("id")
                )
                events_updated += 1
    except Exception as c_err:
        print(f"[UPDATE WORKOUT] Calendar update error: {c_err}")

    return {
        "status": "success",
        "message": f"Workout on {w_date} updated successfully.",
        "plan_updated": updated_plan,
        "events_updated": events_updated
    }


# ---------------------------------------------------------------------------
# 3. IMPORTED / ALIASED EXECUTORS
#
# These executors live in other modules (codex_service) or are deliberate aliases that
# Gemini also reaches for. They are registered explicitly since there is no local `def`
# to decorate. Aliases (`run_code`, `tool_analyze_code`) share an implementation but keep
# their own description and permission key.
# ---------------------------------------------------------------------------
_CODEX_CODE_PARAMS = {
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

registry.add(
    name="execute_codex_code",
    description="Runs Python code or shell commands locally on the host machine. Used to run scripts, tests or system administration tasks.",
    executor=execute_codex_code_impl,
    permission_key="freja_tool_execute_codex_code_allowed",
    parameters=_CODEX_CODE_PARAMS,
)
registry.add(
    name="run_code",
    description="Alias for execute_codex_code. Runs Python code or shell commands locally.",
    executor=execute_codex_code_impl,
    permission_key="freja_tool_run_code_allowed",
    parameters=_CODEX_CODE_PARAMS,
)
registry.add(
    name="codex_git_ops",
    description="Performs git operations in the local source directory (e.g. status, log, diff, branch, pull, commit, push, checkout).",
    executor=codex_git_ops_impl,
    permission_key="freja_tool_codex_git_ops_allowed",
    parameters={
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
    },
)
registry.add(
    name="codex_audit_codebase",
    description="Performs a self-analysis (audit) of the source code to identify bugs, performance problems and code improvements, and saves a detailed report.",
    executor=codex_audit_codebase_impl,
    permission_key="freja_tool_codex_audit_codebase_allowed",
    parameters={"type": "OBJECT", "properties": {}},
)
registry.add(
    name="tool_analyze_code",
    description="Alias for codex_audit_codebase. Performs a self-analysis (audit) of the source code.",
    executor=codex_audit_codebase_impl,
    permission_key="freja_tool_tool_analyze_code_allowed",
    parameters={"type": "OBJECT", "properties": {}},
)
registry.add(
    name="codex_run_and_fix",
    description="Runs a command and automatically tries to repair the source code in the given file if the command/test fails.",
    executor=codex_run_and_fix_impl,
    permission_key="freja_tool_codex_run_and_fix_allowed",
    parameters={
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
    },
)


# ---------------------------------------------------------------------------
# 4. DERIVED PUBLIC STRUCTURES + DISPATCH ENTRY POINT
#
# Everything below is generated from the single registry above, so the historical public
# names keep working for their existing consumers (backend/routes/tools.py, telegram_service).
# EXECUTOR_MAP is retained for backward compatibility even though execute_tool no longer
# reads it directly.
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = registry.declarations
TOOL_PERMISSION_KEYS = registry.permission_keys
EXECUTOR_MAP = registry.executor_map


async def execute_tool(name: str, args: dict, progress_callback=None) -> dict:
    """Dispatches a tool call through the registry (arg hygiene + optional schema validation).

    Long-running tools (Facebook download, learn_topic) accept a `progress_callback` used by
    /api/tools/status polling; the registry introspects the executor signature so short tools
    keep a plain `(args)` signature."""
    return await registry.execute(name, args, progress_callback=progress_callback)


def Math_round(val):
    """Rounds half away from zero, matching JavaScript's Math.round() on the frontend.

    Python's built-in round() uses banker's rounding (round-half-to-even), so round(0.5)
    is 0 and round(2.5) is 2. Health averages are rendered client-side too, and the two
    must agree."""
    if val is None:
        return None
    return int(val + 0.5) if val >= 0 else int(val - 0.5)
