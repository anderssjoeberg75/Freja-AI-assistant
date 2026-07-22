"""AI Personal Trainer routes using FastAPI.

The trainer turns raw health data into coaching text through Gemini. There are three
model-backed entry points, each with its own prompt and response schema:

  POST /api/trainer/advice    - generates a full weekly training plan from a goal.
  GET  /api/trainer/checkin   - the short morning briefing (yesterday, today, weather).
  POST /api/trainer/optimize  - reviews already-booked workouts against recovery data.

Language convention: prompts, comments and error messages are English, but every prompt
explicitly instructs the model to answer in Swedish, since the output is shown verbatim to
the user. Weekday names in generated plans stay Swedish for the same reason - they are data
the user reads, and `day_offsets` in `book_plan_to_calendar` parses them back.

This used to be a single 3000+ line trainer.py; it's now a package with one file per route
group (profile, plans, generation, checkin, optimize, booking) plus shared.py for the
helpers/constants used across more than one of them. This file combines their routers into
one and re-exports the names other modules (garmin.py, tool_registry.py) and tests import
directly from `backend.routes.trainer`, so none of those call sites had to change.
"""

from fastapi import APIRouter

from . import profile, plans, generation, checkin, optimize, booking

router = APIRouter()
router.include_router(profile.router)
router.include_router(plans.router)
router.include_router(generation.router)
router.include_router(checkin.router)
router.include_router(optimize.router)
router.include_router(booking.router)

# Re-exported for external callers (backend/routes/garmin.py, backend/services/tool_registry.py)
# and for tests that do `import backend.routes.trainer as trainer_module`.
from .shared import (
    today_local, get_trainer_profile, fetch_7day_weather_forecast, calculate_trends,
    format_trends_summary, recompute_health_baselines, format_active_injuries,
    build_training_load_summary, _format_progression_rules, _format_exercises_for_calendar,
    MAX_TREND_DAYS, GEMINI_TIMEOUT_SECONDS,
)
from .plans import _current_week_monday
from .generation import _coerce_onboarding_profile
from .checkin import CHECKIN_SYNC_DAYS, refresh_health_sources_for_checkin
from .optimize import core_optimize_upcoming_workouts
