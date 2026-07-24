"""Builds Garmin Connect workout JSON from a F.R.E.J.A. plan session (Issue #176).

Step 1 only (the smallest useful version, per the issue's own phasing): a single
time-based MAIN step covering the whole session, correct sport type, F.R.E.J.A.'s
description as the workout note, scheduled on the right date. Richer step structure
(warm-up/interval/recovery/cool-down with HR/pace targets, calibrated from #186's
threshold benchmarks) and strength-exercise-level detail (via
backend/services/garmin_exercises.py, the reverse direction of #183's import) are later
steps, deliberately not attempted here.

Schema verified against the installed client's own typed models
(garminconnect.workout.BaseWorkout/WorkoutSegment/ExecutableStep), built as plain dicts
here rather than importing those models, since they depend on the optional `pydantic`
extra this project does not otherwise need.
"""

# Garmin's SportType IDs (from garminconnect.workout.SportType), matched to F.R.E.J.A.'s
# Swedish activity_type labels used throughout the plan/booking/HUD code.
SPORT_TYPE_BY_SWEDISH = {
    'löpning': {'sportTypeId': 1, 'sportTypeKey': 'running', 'displayOrder': 1},
    'cykling': {'sportTypeId': 2, 'sportTypeKey': 'cycling', 'displayOrder': 2},
    'simning': {'sportTypeId': 4, 'sportTypeKey': 'swimming', 'displayOrder': 3},
    'styrketräning': {'sportTypeId': 5, 'sportTypeKey': 'strength_training', 'displayOrder': 5},
    'yoga': {'sportTypeId': 7, 'sportTypeKey': 'yoga', 'displayOrder': 7},
    'promenad': {'sportTypeId': 17, 'sportTypeKey': 'walking', 'displayOrder': 17},
}
DEFAULT_SPORT_TYPE = {'sportTypeId': 3, 'sportTypeKey': 'other', 'displayOrder': 3}  # SportType.OTHER

MIN_WORKOUT_SECONDS = 60  # Garmin rejects a near-zero-duration step
MAX_WORKOUT_NAME_LEN = 100
MAX_WORKOUT_DESCRIPTION_LEN = 512


def build_garmin_workout(workout: dict, duration_minutes: int) -> dict:
    """Builds a Garmin Connect workout-upload payload for one plan session.

    `workout` is one entry from a plan's `workouts` list (`activity_type`, `title`,
    `description`); `duration_minutes` is the already-capped duration from
    `plan_occurrences()`."""
    activity_type = str(workout.get('activity_type') or '').strip().lower()
    sport_type = SPORT_TYPE_BY_SWEDISH.get(activity_type, DEFAULT_SPORT_TYPE)
    duration_seconds = max(MIN_WORKOUT_SECONDS, int(duration_minutes or 0) * 60)
    title = str(workout.get('title') or workout.get('activity_type') or 'Träningspass').strip()
    description = str(workout.get('description') or '').strip()[:MAX_WORKOUT_DESCRIPTION_LEN] or None

    main_step = {
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 8, "stepTypeKey": "main", "displayOrder": 8},
        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True},
        "endConditionValue": float(duration_seconds),
        "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1},
    }
    if description:
        main_step["description"] = description

    return {
        "workoutName": f"F.R.E.J.A. - {title}"[:MAX_WORKOUT_NAME_LEN],
        "sportType": sport_type,
        "estimatedDurationInSecs": duration_seconds,
        "workoutSegments": [
            {"segmentOrder": 1, "sportType": sport_type, "workoutSteps": [main_step]}
        ],
        "author": {},
        "description": description,
    }
