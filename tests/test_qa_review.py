"""QA review test suite (senior-dev / QA pass).

Covers pure and near-pure logic across the backend with happy-path, edge-case and
error-handling cases. A few tests are marked xfail: they document bugs found during the
review that are tracked as GitHub issues, so the suite stays green while pinning the
*desired* behaviour for when each bug is fixed.
"""

import asyncio
import datetime

import pytest

from backend.database import get_db_connection


# ---------------------------------------------------------------------------
# weather_codes.describe_weather_code
# ---------------------------------------------------------------------------
class TestDescribeWeatherCode:
    def test_known_code(self):
        from backend.services.weather_codes import describe_weather_code
        assert describe_weather_code(0) == "Clear sky"
        assert describe_weather_code(95) == "Thunderstorm"

    def test_unknown_code_falls_back(self):
        from backend.services.weather_codes import describe_weather_code, DEFAULT_WEATHER_DESCRIPTION
        assert describe_weather_code(123456) == DEFAULT_WEATHER_DESCRIPTION
        assert describe_weather_code(None) == DEFAULT_WEATHER_DESCRIPTION

    def test_float_code_matches_int_key(self):
        # Open-Meteo sometimes yields the code as a float; 0.0 must resolve like 0.
        from backend.services.weather_codes import describe_weather_code
        assert describe_weather_code(0.0) == "Clear sky"


# ---------------------------------------------------------------------------
# tool_registry.Math_round (JS-compatible half-away-from-zero rounding)
# ---------------------------------------------------------------------------
class TestMathRound:
    def test_half_rounds_away_from_zero(self):
        from backend.services.tool_registry import Math_round
        assert Math_round(0.5) == 1
        assert Math_round(2.5) == 3       # Python's round() would give 2 (banker's rounding)
        assert Math_round(-0.5) == -1
        assert Math_round(-2.5) == -3

    def test_integers_and_none(self):
        from backend.services.tool_registry import Math_round
        assert Math_round(10) == 10
        assert Math_round(0) == 0
        assert Math_round(None) is None


# ---------------------------------------------------------------------------
# tool_registry.clean_schema — Gemini schema shaping
# ---------------------------------------------------------------------------
class TestCleanSchema:
    def test_upper_cases_types_and_recurses(self):
        from backend.services.tool_registry import clean_schema
        raw = {
            "type": "object",
            "title": "Foo",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}},
            },
        }
        out = clean_schema(raw)
        assert out["type"] == "OBJECT"
        assert "title" not in out
        assert out["properties"]["items"]["type"] == "ARRAY"
        assert out["properties"]["items"]["items"]["type"] == "STRING"

    def test_preserves_enum_and_description(self):
        from backend.services.tool_registry import clean_schema
        raw = {"type": "string", "enum": ["a", "b"], "description": "pick one"}
        out = clean_schema(raw)
        assert out["enum"] == ["a", "b"]
        assert out["description"] == "pick one"

    def test_non_dict_passthrough(self):
        from backend.services.tool_registry import clean_schema
        assert clean_schema("scalar") == "scalar"
        assert clean_schema(5) == 5


# ---------------------------------------------------------------------------
# trainer._format_exercises_for_calendar — strength block rendering (Issue #34)
# ---------------------------------------------------------------------------
class TestFormatExercisesForCalendar:
    def test_empty_and_none(self):
        from backend.routes.trainer import _format_exercises_for_calendar
        assert _format_exercises_for_calendar(None) == ""
        assert _format_exercises_for_calendar([]) == ""
        assert _format_exercises_for_calendar("not a list") == ""

    def test_weight_and_rpe_rendering(self):
        from backend.routes.trainer import _format_exercises_for_calendar
        out = _format_exercises_for_calendar([
            {"name": "Knäböj", "sets": 4, "reps": 6, "target_weight": 90, "rpe": 8},
            {"name": "Armhävningar", "sets": 3, "reps": 15, "rpe": 7},  # bodyweight -> RPE
        ])
        assert "Knäböj: 4x6 @ 90 kg" in out
        assert "Armhävningar: 3x15 @ RPE 7" in out

    def test_skips_malformed_entries(self):
        from backend.routes.trainer import _format_exercises_for_calendar
        out = _format_exercises_for_calendar([
            {"name": "", "sets": 3},          # no name -> skipped
            "garbage",                          # not a dict -> skipped
            {"name": "Marklyft", "sets": "x", "reps": None, "target_weight": "bad"},
        ])
        # Only the well-named row survives; bad numeric fields degrade to 0 without raising.
        assert "Marklyft" in out
        assert "Knäböj" not in out


# ---------------------------------------------------------------------------
# trainer.format_trends_summary — RHR/HRV prompt block
# ---------------------------------------------------------------------------
class TestFormatTrendsSummary:
    def test_no_data(self):
        from backend.routes.trainer import format_trends_summary
        empty = {
            "rhr_recent_avg": None, "rhr_baseline_avg": None, "rhr_change_pct": None,
            "hrv_recent_avg": None, "hrv_baseline_avg": None, "hrv_change_pct": None,
        }
        assert "No sufficient trend data" in format_trends_summary(empty)

    def test_partial_data_only_rhr(self):
        from backend.routes.trainer import format_trends_summary
        trends = {
            "rhr_recent_avg": 55.0, "rhr_baseline_avg": 52.0, "rhr_change_pct": 5.77,
            "hrv_recent_avg": None, "hrv_baseline_avg": None, "hrv_change_pct": None,
        }
        out = format_trends_summary(trends)
        assert "Resting heart rate" in out
        assert "HRV" not in out


# ---------------------------------------------------------------------------
# auth middleware rate limiter
# ---------------------------------------------------------------------------
class TestAuthRateLimiter:
    @pytest.fixture(autouse=True)
    def _clean_state(self):
        from backend.middleware import auth
        auth._failed_attempts.clear()
        auth._locked_until.clear()
        yield
        auth._failed_attempts.clear()
        auth._locked_until.clear()

    def test_lockout_after_threshold(self):
        from backend.middleware import auth
        ip = "203.0.113.9"
        for _ in range(auth.FAILED_ATTEMPT_THRESHOLD):
            auth._record_failure(ip, "/api/x")
        assert auth._is_locked_out(ip) is True

    def test_below_threshold_not_locked(self):
        from backend.middleware import auth
        ip = "203.0.113.10"
        for _ in range(auth.FAILED_ATTEMPT_THRESHOLD - 1):
            auth._record_failure(ip, "/api/x")
        assert auth._is_locked_out(ip) is False

    def test_success_resets_failures(self):
        from backend.middleware import auth
        ip = "203.0.113.11"
        auth._record_failure(ip, "/api/x")
        auth._record_success(ip)
        assert ip not in auth._failed_attempts
        assert auth._is_locked_out(ip) is False

    def test_lockout_expires(self, monkeypatch):
        from backend.middleware import auth
        ip = "203.0.113.12"
        base = 1_000_000.0
        monkeypatch.setattr(auth.time, "time", lambda: base)
        for _ in range(auth.FAILED_ATTEMPT_THRESHOLD):
            auth._record_failure(ip, "/api/x")
        assert auth._is_locked_out(ip) is True
        # Jump past the lockout window -> unlocked and state cleared.
        monkeypatch.setattr(auth.time, "time", lambda: base + auth.LOCKOUT_SECONDS + 1)
        assert auth._is_locked_out(ip) is False


# ---------------------------------------------------------------------------
# tools.py permission gate + one-time grants
# ---------------------------------------------------------------------------
class TestToolAuthorization:
    def test_git_push_needs_fresh_grant_each_time(self):
        from backend.routes import tools
        tools.ONE_TIME_GRANTS.clear()
        # A push is never satisfied by the permanent allow-list.
        assert tools.is_tool_execution_authorized("codex_git_ops", {"action": "push"}) is False
        # Issue a one-time grant; it authorises exactly one push, then is consumed.
        key = tools._grant_key("codex_git_ops", {"action": "push"})
        import time as _t
        tools.ONE_TIME_GRANTS[key] = _t.time() + 60
        assert tools.is_tool_execution_authorized("codex_git_ops", {"action": "push"}) is True
        assert tools.is_tool_execution_authorized("codex_git_ops", {"action": "push"}) is False

    def test_grant_key_namespaces_push(self):
        from backend.routes import tools
        assert tools._grant_key("codex_git_ops", {"action": "push"}) == "codex_git_ops:push"
        assert tools._grant_key("codex_git_ops", {"action": "status"}) == "codex_git_ops"

    def test_unknown_tool_has_no_gate(self):
        from backend.routes import tools
        # A tool with no permission key is not gated (returns True).
        assert tools.is_tool_permanently_allowed("some_unregistered_tool") is True

    def test_expired_grant_not_consumed(self):
        from backend.routes import tools
        import time as _t
        tools.ONE_TIME_GRANTS.clear()
        tools.ONE_TIME_GRANTS["get_weather"] = _t.time() - 1  # already expired
        assert tools.consume_one_time_grant("get_weather") is False


# ---------------------------------------------------------------------------
# database key aliasing + masking
# ---------------------------------------------------------------------------
class TestDatabaseKeys:
    def test_alias_round_trip(self):
        from backend.database import set_api_key, get_api_key
        # Writing the new name also writes the legacy alias, and vice versa.
        set_api_key("freja_strava_client_id", "QA_alias_value")
        assert get_api_key("freja_strava_client_id") == "QA_alias_value"
        assert get_api_key("strava_client_id") == "QA_alias_value"  # legacy alias resolves

    def test_sensitive_values_are_masked(self):
        from backend.database import set_api_key, get_all_api_keys
        set_api_key("freja_qa_probe_secret", "supersecret123")
        allkeys = get_all_api_keys()
        assert allkeys.get("freja_qa_probe_secret") == "••••••••"

    def test_access_token_is_masked_by_get_all_api_keys(self):
        # Documents the root cause behind the corrupt-token bug: the access token the HUD
        # needs for auth is masked here, so any client that mirrors /api/keys into
        # localStorage will overwrite its real token with bullets. Tracked as a GitHub issue.
        from backend.database import get_all_api_keys, get_api_key
        if not get_api_key("freja_access_token"):
            pytest.skip("no access token configured in this environment")
        assert get_all_api_keys().get("freja_access_token") == "••••••••"


# ---------------------------------------------------------------------------
# search_service.perform_search input handling
# ---------------------------------------------------------------------------
class TestPerformSearch:
    def test_empty_query_returns_empty_without_network(self):
        from backend.services.search_service import perform_search
        assert asyncio.run(perform_search("")) == []
        assert asyncio.run(perform_search("   ")) == []
        assert asyncio.run(perform_search(None)) == []


# ---------------------------------------------------------------------------
# KNOWN BUGS (xfail) — pin the desired behaviour for tracked issues
# ---------------------------------------------------------------------------
class TestKnownBugs:
    @pytest.fixture
    def _seed_strava(self):
        """Insert 5 QA activities all dated TODAY; clean up after."""
        today = datetime.date.today()
        rows = [("QA_TEST_today", "Löpning", today.strftime("%Y-%m-%d")) for _ in range(5)]
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.executemany(
                "INSERT INTO strava_activities (name, type, date, distance, moving_time) "
                "VALUES (?, ?, ?, 1000.0, 300)", rows
            )
            conn.commit()
        yield today
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM strava_activities WHERE name LIKE 'QA_TEST_%'")
            conn.commit()

    @pytest.mark.xfail(reason="Bug: get_strava_data treats `days` as a SQL LIMIT (row count), "
                              "not a date window. 5 activities all within today cannot all be "
                              "returned when days<5, and the coach's reported period is wrong.")
    def test_strava_days_should_be_a_date_window(self, _seed_strava):
        from backend.routes.strava import get_strava_data
        # All 5 QA activities are dated today, so a correct "last 3 days" query must return
        # every one of them. The current LIMIT-based query caps the whole result at 3 rows.
        results = asyncio.run(get_strava_data(days=3))
        qa = [r for r in results if str(r.get("name", "")) == "QA_TEST_today"]
        assert len(qa) == 5, (
            f"expected all 5 same-day activities within the window, got {len(qa)} "
            "(days is being used as a row LIMIT, not a date window)"
        )
