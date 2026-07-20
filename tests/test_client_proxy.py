"""Regression tests for the HUD proxy in run_client.py.

The proxy sits between the browser and the backend, so its timeout silently defines the
longest request the HUD can make. A flat 30s meant every model-backed endpoint - plan
generation, the daily check-in, and onboarding, which analyses 90 days of health data and
then waits on Gemini - was cut off mid-flight and reported as "Bad Gateway". Onboarding
could not complete a single run, and the failure pointed at the wrong component entirely.
"""

import run_client
import backend.routes.trainer as trainer_module


def test_model_backed_paths_get_a_long_timeout():
    for path in (
        "/api/trainer/onboarding/start",
        "/api/trainer/onboarding/complete",
        "/api/trainer/generate",
        "/api/trainer/checkin",
        "/api/trainer/optimize",
        "/api/gemini/generate?model=x",
        "/api/tools/execute",
    ):
        assert run_client.proxy_timeout_for(path) == run_client.LLM_PROXY_TIMEOUT, path


def test_plain_data_paths_keep_the_short_timeout():
    """A dead backend must still be noticed quickly on ordinary reads."""
    for path in ("/api/trainer/profile", "/api/trainer/workouts?days=14", "/api/garmin/data", "/api/keys"):
        assert run_client.proxy_timeout_for(path) == run_client.DEFAULT_PROXY_TIMEOUT, path


def test_proxy_timeout_exceeds_the_backend_timeout():
    """The proxy must outlive the backend's own Gemini call.

    If it gives up first the user gets a gateway error while the backend is still working -
    which is exactly how a working onboarding run looked like a broken one.
    """
    assert run_client.LLM_PROXY_TIMEOUT > trainer_module.GEMINI_TIMEOUT_SECONDS
