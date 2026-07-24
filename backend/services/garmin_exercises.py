"""Bidirectional Swedish <-> Garmin exercise-name mapping.

Garmin uses SCREAMING_SNAKE_CASE identifiers (`BARBELL_BACK_SQUAT`); F.R.E.J.A.'s logs and
plans use Swedish free text ("Knäböj"). This table is shared by both directions that need
it: importing Garmin-logged strength sets into `trainer_strength_logs` reads Garmin -> Swedish
(Issue #183); pushing a planned strength workout to the watch reads Swedish -> Garmin, the
inverse (Issue #176 step 3). Keeping both in one table means they cannot silently drift apart.
"""

# Garmin identifier -> Swedish display name. Several Garmin keys may map to one Swedish name
# (e.g. SQUAT and BARBELL_BACK_SQUAT both read as "Knäböj" to the user).
GARMIN_TO_SWEDISH = {
    'SQUAT': 'Knäböj',
    'BARBELL_BACK_SQUAT': 'Knäböj',
    'BACK_SQUAT': 'Knäböj',
    'FRONT_SQUAT': 'Frontböj',
    'GOBLET_SQUAT': 'Goblet squat',
    'DEADLIFT': 'Marklyft',
    'BARBELL_DEADLIFT': 'Marklyft',
    'SUMO_DEADLIFT': 'Sumomarklyft',
    'ROMANIAN_DEADLIFT': 'Rumänsk marklyft',
    'BENCH_PRESS': 'Bänkpress',
    'BARBELL_BENCH_PRESS': 'Bänkpress',
    'INCLINE_BENCH_PRESS': 'Lutande bänkpress',
    'DUMBBELL_BENCH_PRESS': 'Hantelbänkpress',
    'OVERHEAD_PRESS': 'Axelpress',
    'SHOULDER_PRESS': 'Axelpress',
    'MILITARY_PRESS': 'Axelpress',
    'PULL_UP': 'Chins',
    'CHIN_UP': 'Chins',
    'LAT_PULLDOWN': 'Latsdrag',
    'BARBELL_ROW': 'Skivstångsrodd',
    'BENT_OVER_ROW': 'Böjd rodd',
    'SEATED_ROW': 'Sittande rodd',
    'LATERAL_RAISE': 'Sidolyft',
    'BICEP_CURL': 'Bicepscurl',
    'HAMMER_CURL': 'Hammercurl',
    'TRICEP_EXTENSION': 'Tricepsextension',
    'LUNGE': 'Utfall',
    'WALKING_LUNGE': 'Gående utfall',
    'LEG_PRESS': 'Benpress',
    'LEG_CURL': 'Bencurl',
    'LEG_EXTENSION': 'Benspark',
    'PLANK': 'Plankan',
    'HIP_THRUST': 'Höftlyft',
    'CALF_RAISE': 'Vadpress',
}

# Swedish -> Garmin, the inverse direction (#176 step 3), built from the same table so it
# can never drift out of sync with the forward direction. Where several Garmin keys map to
# one Swedish name, the first one encountered wins - a reasonable canonical choice for
# pushing a plan TO the watch.
SWEDISH_TO_GARMIN = {}
for _garmin_key, _swedish_name in GARMIN_TO_SWEDISH.items():
    SWEDISH_TO_GARMIN.setdefault(_swedish_name, _garmin_key)


def garmin_to_swedish(garmin_name: str) -> str:
    """Maps a Garmin exercise identifier to its Swedish display name.

    Anything unmapped is prettified rather than dropped (`CABLE_FLY` -> "Cable fly") - that
    is strictly better than losing the set, and logging the miss here is how this table is
    meant to grow from what actually shows up in practice."""
    if not garmin_name:
        return "Okänd övning"
    key = garmin_name.strip().upper()
    if key in GARMIN_TO_SWEDISH:
        return GARMIN_TO_SWEDISH[key]
    prettified = key.replace('_', ' ').capitalize()
    print(
        f"[garmin_exercises] Unmapped Garmin exercise name '{garmin_name}' -> "
        f"'{prettified}'. Consider adding it to GARMIN_TO_SWEDISH."
    )
    return prettified


def swedish_to_garmin(swedish_name: str):
    """Maps a Swedish exercise name back to its Garmin identifier, or None if unmapped."""
    if not swedish_name:
        return None
    return SWEDISH_TO_GARMIN.get(swedish_name.strip())
