"""Plan generation and the health-data-driven onboarding interview."""

import datetime
import httpx
import json
from fastapi import APIRouter, HTTPException, Query, Request
from backend.database import get_db_connection, get_api_key
from backend.services.http_client import shared_client
from backend.services.gemini_client import get_gemini_model, build_generate_url
from backend.services.time_utils import today_local
from .shared import (
    get_trainer_profile, fetch_7day_weather_forecast, calculate_trends, format_trends_summary,
    recompute_health_baselines, format_recent_strength_logs, format_active_injuries,
    build_training_load_summary, format_training_load_summary, _format_progression_rules,
    MAX_INPUT_LEN, GEMINI_TIMEOUT_SECONDS, BASELINE_WINDOW_DAYS, DEFAULT_LOCATION,
    RHR_ALERT_PCT, HRV_ALERT_PCT, TRAINING_LOAD_DAYS,
)
from .profile import _save_profile_values
from .plans import _current_week_monday
from .booking import core_book_plan_internal

router = APIRouter()


@router.post("/api/trainer/generate")
async def generate_trainer_plan(request: Request):
    try:
        body = await request.json()
        goal = body.get("goal", "").strip()[:MAX_INPUT_LEN]
        limitations = body.get("limitations", "").strip()[:MAX_INPUT_LEN]
        if not goal:
            raise HTTPException(status_code=400, detail="The goal is missing.")

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
                SELECT date, steps, sleep_hours, resting_hr, active_calories, workout_type, workout_duration, body_battery, hrv, recovery_time, training_status, sleep_deep_hours, sleep_light_hours, sleep_rem_hours, sleep_awake_hours, sleep_score
                FROM garmin_health
                ORDER BY date DESC
                LIMIT 7
            ''')
            for r in cursor.fetchall():
                garmin_summary.append(
                    f"Date: {r[0]}, Steps: {r[1]}, Sleep: {r[2]}h (Deep: {r[11]}h, REM: {r[13]}h, Light: {r[12]}h, Awake: {r[14]}h, Score: {r[15]}), Resting HR: {r[3]}, Calories: {r[4]}kcal, Workout: {r[5]} ({r[6]} min), Body Battery: {r[7]}, HRV: {r[8]}ms, Recovery time: {r[9]}h, Status: {r[10]}"
                )

            cursor.execute('''
                SELECT name, type, date, distance, moving_time, total_elevation_gain, average_heartrate, max_heartrate, calories
                FROM strava_activities
                ORDER BY date DESC
                LIMIT 20
            ''')
            for r in cursor.fetchall():
                dist_km = round(r[3] / 1000.0, 2) if r[3] else 0
                dur_min = round(r[4] / 60.0, 1) if r[4] else 0
                strava_summary.append(
                    f"Activity: {r[0]}, Type: {r[1]}, Date: {r[2]}, Distance: {dist_km} km, Time: {dur_min} min, Elevation gain: {r[5]}m, Avg HR: {r[6]}, Max HR: {r[7]}, Calories: {r[8]}kcal"
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
                    f"Date: {r[0]}, Weight: {r[1]} kg, Body fat: {r[2]}%, Bone mass: {r[3]} kg, Pulse: {r[4]} BPM, Sleep: {sleep_h}h (Score: {r[8]}), Steps: {r[6]}, Calories: {r[7]}kcal"
                )

        # 4. Fetch Gemini API key
        api_key = get_api_key('freja_gemini_apikey') or ""
        if not api_key:
            raise HTTPException(status_code=400, detail="The Gemini API key is not configured on the server.")

        # 5. Calculate trends
        trends = calculate_trends()
        trends_data_str = format_trends_summary(trends)

        # 5.4 Recent strength loads so the coach can apply progressive overload (Issue #34)
        strength_logs_str = format_recent_strength_logs()

        # 5.45 Open injuries so affected sessions get eased or swapped (Issue #38)
        injuries_str = format_active_injuries()

        # 5.47 A month of actually-completed training, so the plan progresses from what the
        # user has really been doing instead of jumping from a 20-minute jog to an hour.
        training_load = build_training_load_summary(TRAINING_LOAD_DAYS)
        training_load_str = format_training_load_summary(training_load)
        progression_rules = _format_progression_rules(training_load)

        # 5.5 Fetch 7-day weather forecast (for the profile's location)
        weather_forecast = await fetch_7day_weather_forecast(location)

        # 6. Compile Prompt
        garmin_data_str = "\n".join(garmin_summary) if garmin_summary else "No Garmin data available."
        strava_data_str = "\n".join(strava_summary) if strava_summary else "No Strava data available."
        # The 7-day blocks below stay short on purpose: they describe current recovery state.
        # The month of training volume the progression is built on comes from the
        # TRAINING LOAD block, which is aggregated rather than row-by-row.
        withings_data_str = "\n".join(withings_summary) if withings_summary else "No Withings data available."

        limitations_prompt = (
            f'\nINJURIES / ILLNESSES / LIMITATIONS:\n"{limitations}"\n'
            'Take particular account of these limitations, injuries or illnesses (e.g. exercise-induced '
            'asthma, knee injuries) and adapt the exercise selection and intensity accordingly.'
        ) if limitations else ""

        prompt_content = f"""
You are a professional personal trainer and health coach (COACH AI) integrated into the F.R.E.J.A. system.
Analyse the following health data, training data, trends and weather forecast for the user, and create a
tailored training plan or concrete training tips based on their stated goal.

GOAL: "{goal}"{limitations_prompt}

[CALCULATED HEALTH TRENDS (RHR & HRV)]:
{trends_data_str}

[WEATHER FORECAST FOR THE NEXT 7 DAYS]:
{weather_forecast}

[GARMIN HEALTH DATA (last 7 days)]:
{garmin_data_str}

[STRAVA WORKOUTS (most recent sessions)]:
{strava_data_str}

[WITHINGS MEASUREMENTS (last 7 measurements)]:
{withings_data_str}

[RECENTLY LOGGED STRENGTH LOADS (most recent per exercise)]:
{strength_logs_str}

[ACTIVE INJURY / PAIN LOG (dated entries the user is still bothered by)]:
{injuries_str}

[TRAINING LOAD - WHAT THE USER HAS ACTUALLY COMPLETED IN THE LAST {TRAINING_LOAD_DAYS} DAYS]:
{training_load_str}

[PROGRESSION LIMITS - THESE ARE NOT SUGGESTIONS]:
{progression_rules}

Instructions for the answer:
- Answer in Swedish.
- Write in an encouraging, professional and coaching tone (F.R.E.J.A. style: polite but extremely knowledgeable).
- Give concrete, practical advice on training intensity, recovery (look at sleep and HRV/recovery where available),
  and training modality based on the data.
- For strength sessions (Styrketräning), fill in the structured "exercises" list for that workout: name each
  exercise with target sets, reps and a target weight in kg (or an RPE if bodyweight/unloaded). Apply PROGRESSIVE
  OVERLOAD relative to the recently logged loads above - nudge weight or reps up slightly when recovery is good,
  and hold or reduce load when recovery is poor. Leave "exercises" empty for pure cardio/rest days.
- Take the coming week's weather forecast into account when planning the sessions:
  - If bad weather is expected (e.g. heavy rain, snowfall, thunderstorms or storms) on a planned training day,
    recommend indoor training or rest for that day.
  - If the user has asthma-related conditions (such as "astma" or "ansträngningsastma" in their limitations),
    pay extra attention to very cold days (e.g. apparent temperature below 0°C) combined with low humidity /
    dry air, and recommend indoor training or lower intensity to reduce the risk of asthma problems.
- Analyse the health trends. If the resting heart rate has risen sharply (>{RHR_ALERT_PCT}%) or HRV has dropped
  sharply (<{HRV_ALERT_PCT}%), add a clear recommendation for active rest or reduced intensity.
- Adapt the plan to the ACTIVE INJURY / PAIN LOG above: avoid or substitute exercises that load an
  affected area, lower the intensity of sessions that would aggravate it, and scale that caution to the
  logged severity (7-10 means avoid loading the area entirely). Mention the adaptation briefly in the
  session description so the user can see why it differs.
- Build the progression on the TRAINING LOAD block above and stay inside the PROGRESSION LIMITS.
  Every session duration you write must be justifiable against what the user has actually been
  doing over the past {TRAINING_LOAD_DAYS} days - a plan that doubles a session length is wrong
  even if the goal is ambitious. Briefly state in the summary which recent sessions you are
  progressing from.
- Create a simple weekly plan the user can follow right away.
"""

        # 7. Call Gemini
        google_url = build_generate_url(get_gemini_model(), api_key)
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt_content}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 4000,
                "responseMimeType": "application/json",
                # Every free-text field below is rendered straight into the HUD, so the schema
                # tells the model to fill them in Swedish. `day` must stay a Swedish weekday
                # name: book_plan_to_calendar() maps it back to a date via `day_offsets`.
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "summary": {
                            "type": "STRING",
                            "description": "A summarising analysis of the user's health status and training history, written in Swedish."
                        },
                        "resting_hr_trend": {
                            "type": "STRING",
                            "description": "Analysis of the resting heart rate trend (e.g. whether it has risen and indicates fatigue, or is stable). In Swedish."
                        },
                        "hrv_trend": {
                            "type": "STRING",
                            "description": "Analysis of the HRV trend (e.g. whether it has dropped and indicates under-recovery, or is good). In Swedish."
                        },
                        "weekly_focus": {
                            "type": "STRING",
                            "description": "The overall focus of this training week based on the goal and any limitations. In Swedish."
                        },
                        "workouts": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "day": {
                                        "type": "STRING",
                                        "description": "The day of the session. Must be one of the Swedish weekday names: Måndag, Tisdag, Onsdag, Torsdag, Fredag, Lördag, Söndag."
                                    },
                                    "activity_type": {
                                        "type": "STRING",
                                        "description": "Activity type in Swedish (e.g. Löpning, Styrketräning, Cykling, Yoga, Vila)."
                                    },
                                    "title": {
                                        "type": "STRING",
                                        "description": "Short descriptive title of the session, in Swedish."
                                    },
                                    "description": {
                                        "type": "STRING",
                                        "description": "Detailed instructions for the training session, in Swedish."
                                    },
                                    "duration_minutes": {
                                        "type": "INTEGER",
                                        "description": "Estimated time in minutes (0 for rest)."
                                    },
                                    "exercises": {
                                        "type": "ARRAY",
                                        "description": "Structured strength exercises for this session (empty for pure cardio/rest). Apply progressive overload against the logged loads.",
                                        "items": {
                                            "type": "OBJECT",
                                            "properties": {
                                                "name": {"type": "STRING", "description": "Exercise name in Swedish (e.g. Knäböj, Marklyft, Bänkpress)."},
                                                "sets": {"type": "INTEGER", "description": "Number of sets."},
                                                "reps": {"type": "INTEGER", "description": "Target reps per set."},
                                                "target_weight": {"type": "NUMBER", "description": "Target load in kg (0 for bodyweight/unloaded)."},
                                                "rpe": {"type": "NUMBER", "description": "Target rate of perceived exertion 1-10 (optional, 0 if not used)."}
                                            },
                                            "required": ["name", "sets", "reps"]
                                        }
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

        async with shared_client() as client:
            response = await client.post(google_url, json=payload, timeout=GEMINI_TIMEOUT_SECONDS)
            response.raise_for_status()
            res_json = response.json()

        advice_text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not advice_text:
            raise HTTPException(status_code=500, detail="Could not generate a response from Gemini.")

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

        # 9. Automatically book the generated plan's workouts into the weekly schedule.
        # Anchored on this week's Monday because the plan's weekday names are offsets from
        # the start date; passing today would shift every session by today's weekday. Days
        # that already passed are skipped rather than booked into the past.
        booking = None
        try:
            booking = await core_book_plan_internal(plan_id, _current_week_monday())
        except Exception as book_err:
            print(f"[TRAINER GENERATE] Auto-booking warning: {book_err}")

        return {
            "status": "success",
            "plan_id": plan_id,
            "date": today_str,
            "goal": goal,
            "limitations": limitations,
            "advice_text": advice_text,
            "booking": booking
        }
        
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Guided onboarding -------------------------------------------------------
# The profile fields drive every plan, but a user cannot reasonably guess their own HRV
# baseline or describe their real weekly volume. Onboarding derives everything the connected
# devices already know (Garmin / Strava / Withings) and only asks about what data cannot
# reveal: intent, schedule, preferences, equipment and how the body actually feels.
ONBOARDING_LOOKBACK_DAYS = 90    # History window analysed before the interview
MAX_ONBOARDING_QUESTIONS = 8
MAX_ONBOARDING_ANSWER_LEN = 500

# The profile columns onboarding is allowed to write, and how to coerce each one.
ONBOARDING_TEXT_FIELDS = ("goals", "limitations", "fitness_level", "availability", "location", "event", "event_date")
ONBOARDING_NUMERIC_FIELDS = ("baseline_resting_hr", "baseline_sleep_hours", "baseline_hrv")


def _collect_onboarding_signals(days: int = ONBOARDING_LOOKBACK_DAYS) -> dict:
    """Everything the connected devices can tell us about the user, as prompt-ready text."""
    days = max(14, min(int(days or ONBOARDING_LOOKBACK_DAYS), 365))
    cutoff = (today_local() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')

    garmin_lines, withings_lines = [], []
    activity_types = {}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                '''SELECT COUNT(*), AVG(steps), AVG(sleep_hours), AVG(resting_hr), AVG(hrv),
                          AVG(sleep_score), MAX(vo2max)
                   FROM garmin_health WHERE date >= ?''',
                (cutoff,)
            )
            row = cursor.fetchone() or []
            if row and row[0]:
                def _fmt(v, unit, decimals=1):
                    return f"{round(v, decimals)}{unit}" if v else "no data"
                garmin_lines.append(
                    f"{row[0]} days of Garmin data: avg {_fmt(row[1], ' steps', 0)}, "
                    f"sleep {_fmt(row[2], 'h')}, resting HR {_fmt(row[3], ' bpm')}, "
                    f"HRV {_fmt(row[4], ' ms')}, sleep score {_fmt(row[5], '')}, "
                    f"VO2max {_fmt(row[6], '')}"
                )
            cursor.execute(
                '''SELECT training_status, COUNT(*) FROM garmin_health
                   WHERE date >= ? AND training_status IS NOT NULL AND training_status != ''
                   GROUP BY training_status ORDER BY COUNT(*) DESC LIMIT 5''',
                (cutoff,)
            )
            statuses = [f"{r[0]} ({r[1]} days)" for r in cursor.fetchall()]
            if statuses:
                garmin_lines.append("Garmin training status distribution: " + ", ".join(statuses))
        except Exception as e:
            print(f"[TRAINER ONBOARDING] Garmin read error: {e}")

        try:
            cursor.execute(
                '''SELECT type, COUNT(*), AVG(moving_time), MAX(distance), AVG(average_heartrate), MAX(max_heartrate)
                   FROM strava_activities WHERE SUBSTR(date, 1, 10) >= ?
                   GROUP BY type ORDER BY COUNT(*) DESC''',
                (cutoff,)
            )
            for a_type, count, avg_time, max_dist, avg_hr, max_hr in cursor.fetchall():
                activity_types[a_type or "Träning"] = {
                    "sessions": count,
                    "avg_minutes": round((avg_time or 0) / 60.0, 1),
                    "longest_km": round((max_dist or 0) / 1000.0, 2),
                    "avg_hr": round(avg_hr, 0) if avg_hr else None,
                    "max_hr": max_hr,
                }
        except Exception as e:
            print(f"[TRAINER ONBOARDING] Strava read error: {e}")

        try:
            cursor.execute(
                '''SELECT COUNT(*), AVG(weight), MIN(weight), MAX(weight), AVG(fat_ratio)
                   FROM withings_measurements WHERE date >= ?''',
                (cutoff,)
            )
            row = cursor.fetchone() or []
            if row and row[0]:
                withings_lines.append(
                    f"{row[0]} Withings measurements: weight avg {round(row[1], 1) if row[1] else 'n/a'} kg "
                    f"(range {round(row[2], 1) if row[2] else 'n/a'}-{round(row[3], 1) if row[3] else 'n/a'} kg), "
                    f"body fat avg {round(row[4], 1) if row[4] else 'n/a'}%"
                )
        except Exception as e:
            print(f"[TRAINER ONBOARDING] Withings read error: {e}")

    activity_lines = [
        f"- {t}: {v['sessions']} sessions, typically {v['avg_minutes']} min, longest {v['longest_km']} km, "
        f"avg HR {v['avg_hr'] or 'n/a'}, max HR {v['max_hr'] or 'n/a'}"
        for t, v in activity_types.items()
    ]

    load = build_training_load_summary(TRAINING_LOAD_DAYS)
    return {
        "window_days": days,
        "garmin": "\n".join(garmin_lines) or "No Garmin data available.",
        "withings": "\n".join(withings_lines) or "No Withings data available.",
        "strava": "\n".join(activity_lines) or "No Strava activities available.",
        "trends": format_trends_summary(calculate_trends()),
        "training_load": format_training_load_summary(load),
        "injuries": format_active_injuries(),
        "strength": format_recent_strength_logs(),
        "load": load,
        "activity_types": activity_types,
    }


async def _call_gemini_json(prompt: str, schema: dict, max_tokens: int = 3000) -> dict:
    """Posts a JSON-schema-constrained prompt to Gemini and returns the parsed object."""
    api_key = get_api_key('freja_gemini_apikey') or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="The Gemini API key is not configured on the server.")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    async with shared_client() as client:
        response = await client.post(build_generate_url(get_gemini_model(), api_key), json=payload, timeout=GEMINI_TIMEOUT_SECONDS)
        response.raise_for_status()
        res_json = response.json()

    text = res_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    if not text:
        raise HTTPException(status_code=500, detail="Gemini returned an empty response.")
    try:
        return json.loads(text.replace("```json", "").replace("```", "").strip())
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Gemini returned malformed JSON: {e}")


_ONBOARDING_PROFILE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "goals": {"type": "STRING", "description": "Primary training goal, in Swedish."},
        "fitness_level": {"type": "STRING", "description": "One of exactly: beginner, intermediate, advanced."},
        "availability": {"type": "STRING", "description": "Weekly availability, e.g. '3 dagar/vecka, 45 min'. In Swedish."},
        "location": {"type": "STRING", "description": "City used for the weather forecast."},
        "event": {"type": "STRING", "description": "Target event, or empty string if none."},
        "event_date": {"type": "STRING", "description": "Event date as YYYY-MM-DD, or empty string."},
        "limitations": {"type": "STRING", "description": "Injuries, illnesses and limitations, in Swedish."},
        "baseline_resting_hr": {"type": "NUMBER", "description": "Resting HR baseline in bpm (0 if unknown)."},
        "baseline_sleep_hours": {"type": "NUMBER", "description": "Sleep baseline in hours (0 if unknown)."},
        "baseline_hrv": {"type": "NUMBER", "description": "HRV baseline in ms (0 if unknown)."},
    },
    "required": ["goals", "fitness_level", "availability", "location", "limitations"],
}


@router.post("/api/trainer/onboarding/start")
async def start_trainer_onboarding(request: Request):
    """Step 1 of onboarding: analyse the connected data and return the interview questions.

    Everything the devices can answer is filled in up front; the questions cover only what
    the data cannot know (intent, schedule, equipment, how the body actually feels)."""
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        days = int(body.get("days") or ONBOARDING_LOOKBACK_DAYS)

        signals = _collect_onboarding_signals(days)
        profile = get_trainer_profile()
        existing = ", ".join(
            f"{k}={v}" for k, v in profile.items()
            if k in ONBOARDING_TEXT_FIELDS + ONBOARDING_NUMERIC_FIELDS and v
        ) or "The profile is empty."

        prompt = f"""
You are COACH AI, F.R.E.J.A.'s personal trainer, running the onboarding interview for a new user.
Analyse the connected health and training data below, then prepare the interview.

[EXISTING TRAINING PROFILE]: {existing}

[GARMIN - last {signals['window_days']} days]:
{signals['garmin']}

[STRAVA ACTIVITY BREAKDOWN - last {signals['window_days']} days]:
{signals['strava']}

[WITHINGS BODY MEASUREMENTS - last {signals['window_days']} days]:
{signals['withings']}

[HEALTH TRENDS]:
{signals['trends']}

[COMPLETED TRAINING LOAD]:
{signals['training_load']}

[LOGGED INJURIES]:
{signals['injuries']}

[LOGGED STRENGTH LOADS]:
{signals['strength']}

Produce:
1. `data_summary`: a short Swedish analysis of what the data reveals about this user's current
   fitness, training habits, recovery and body composition. Be concrete and quote real figures.
2. `proposed_profile`: your best estimate of every profile field FROM THE DATA. Derive
   fitness_level from real training volume and heart-rate data, availability from how often
   they actually train, and the baselines from the measured averages. Use 0 for a baseline the
   data cannot support. Keep any existing value that the data does not contradict.
3. `questions`: {MAX_ONBOARDING_QUESTIONS} or fewer questions, in Swedish, covering ONLY what the data
   cannot answer - the user's actual goal and motivation, their real weekly schedule, access to
   gym/equipment, preferred activities, pain or health issues not yet logged, and any target
   event. Never ask about something the data already shows (do not ask "how often do you train"
   when the activity log answers it - instead confirm your reading of it). For each question
   give `field` (the profile field it informs: goals, availability, limitations, event,
   event_date, location, fitness_level, or "other") and `suggested_answer` (your data-based
   guess the user can accept as-is).
Answer every free-text field in Swedish.
"""

        schema = {
            "type": "OBJECT",
            "properties": {
                "data_summary": {"type": "STRING", "description": "Swedish analysis of the connected data."},
                "proposed_profile": _ONBOARDING_PROFILE_SCHEMA,
                "questions": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "id": {"type": "STRING", "description": "Short stable id, e.g. 'goal'."},
                            "question": {"type": "STRING", "description": "The question, in Swedish."},
                            "why": {"type": "STRING", "description": "One sentence on why it matters, in Swedish."},
                            "field": {"type": "STRING", "description": "Profile field this informs."},
                            "suggested_answer": {"type": "STRING", "description": "Data-based suggested answer, in Swedish."},
                        },
                        "required": ["id", "question", "field"],
                    },
                },
            },
            "required": ["data_summary", "proposed_profile", "questions"],
        }

        result = await _call_gemini_json(prompt, schema)
        result["questions"] = (result.get("questions") or [])[:MAX_ONBOARDING_QUESTIONS]
        result["status"] = "success"
        result["signals"] = {
            "window_days": signals["window_days"],
            "sessions_last_30_days": signals["load"].get("session_count", 0),
            "avg_weekly_minutes": signals["load"].get("avg_weekly_minutes", 0),
            "longest_session_minutes": signals["load"].get("longest_session_minutes", 0),
            "activity_types": signals["activity_types"],
        }
        return result

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _coerce_onboarding_profile(proposed: dict) -> dict:
    """Filters a model-proposed profile down to writable fields with sane values."""
    values = {}
    for f in ONBOARDING_TEXT_FIELDS:
        val = str((proposed or {}).get(f) or "").strip()[:MAX_INPUT_LEN]
        if val:
            values[f] = val
    if values.get("fitness_level", "").lower() not in ("beginner", "intermediate", "advanced"):
        values.pop("fitness_level", None)
    if "event_date" in values:
        try:
            datetime.datetime.strptime(values["event_date"][:10], "%Y-%m-%d")
            values["event_date"] = values["event_date"][:10]
        except ValueError:
            values.pop("event_date")  # An unparseable date would break the date input.
    for f in ONBOARDING_NUMERIC_FIELDS:
        raw = (proposed or {}).get(f)
        try:
            num = float(raw)
        except (TypeError, ValueError):
            continue
        if num > 0:  # 0 is the schema's "unknown", not a real baseline.
            values[f] = round(num, 1)
    return values


@router.post("/api/trainer/onboarding/complete")
async def complete_trainer_onboarding(request: Request):
    """Step 2 of onboarding: merge the user's answers with the data and save the profile."""
    try:
        body = await request.json()
        answers = body.get("answers") or []
        proposed = body.get("proposed_profile") or {}

        clean_answers = []
        for a in answers[:MAX_ONBOARDING_QUESTIONS]:
            question = str((a or {}).get("question") or "").strip()[:MAX_INPUT_LEN]
            answer = str((a or {}).get("answer") or "").strip()[:MAX_ONBOARDING_ANSWER_LEN]
            if question and answer:
                clean_answers.append({"question": question, "answer": answer, "field": str((a or {}).get("field") or "")})

        if not clean_answers:
            # Nothing to merge - persist the data-derived proposal as-is rather than failing.
            values = _coerce_onboarding_profile(proposed)
            if not values:
                raise HTTPException(status_code=400, detail="No answers and no usable proposed profile were provided.")
            saved = _save_profile_values(values)
            return {"status": "success", "profile": saved, "summary":
                    "Profilen sparades utifrån din data. Inga frågor besvarades.", "answers_used": 0}

        signals = _collect_onboarding_signals()
        answers_block = "\n".join(f"- {a['question']}\n  Svar: {a['answer']}" for a in clean_answers)

        prompt = f"""
You are COACH AI completing F.R.E.J.A.'s onboarding for a user.

[YOUR DATA-BASED PROPOSAL]:
{json.dumps(proposed, ensure_ascii=False)}

[THE USER'S ANSWERS]:
{answers_block}

[COMPLETED TRAINING LOAD]:
{signals['training_load']}

[HEALTH TRENDS]:
{signals['trends']}

Merge the answers with your data-based proposal into the final training profile.
The user's own answers WIN over your estimate for intent, schedule, limitations and events.
Your measured values win for the physiological baselines - do not let a guess overwrite a
measured resting HR, sleep or HRV baseline. Use 0 for a baseline the data cannot support.
Write `summary` as a short Swedish confirmation of what you understood and how the plan will
be shaped, mentioning the training volume the progression will start from.
Answer every free-text field in Swedish.
"""

        schema = {
            "type": "OBJECT",
            "properties": {
                "profile": _ONBOARDING_PROFILE_SCHEMA,
                "summary": {"type": "STRING", "description": "Swedish confirmation of the finished profile."},
            },
            "required": ["profile", "summary"],
        }

        result = await _call_gemini_json(prompt, schema)
        values = _coerce_onboarding_profile(result.get("profile") or {})
        if not values:
            raise HTTPException(status_code=500, detail="The onboarding produced no usable profile fields.")

        saved = _save_profile_values(values)
        return {
            "status": "success",
            "profile": saved,
            "summary": result.get("summary", ""),
            "answers_used": len(clean_answers),
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Gemini API error: {e.response.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


