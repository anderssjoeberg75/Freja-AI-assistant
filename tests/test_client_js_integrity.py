"""Structural checks on the client JavaScript.

There is no JS engine in this environment, so these are not a parser. They catch the
specific, silent failure mode that has actually bitten: an edit replaces a
`FrejaUIController.prototype.X = function` but loses the previous one's closing
"`;\n};", leaving an unterminated template literal. The browser then fails to execute the
*entire* file, so every dashboard method silently disappears from the prototype - which
looks like an API/auth problem rather than a syntax error, and sends you chasing the
wrong bug.
"""

import re
from pathlib import Path

import pytest

from backend.config import PROJECT_ROOT

CLIENT_JS_DIR = Path(PROJECT_ROOT) / "client"
JS_FILES = sorted(
    [p for p in CLIENT_JS_DIR.glob("*.js")]
    + [p for p in (CLIENT_JS_DIR / "js").glob("*.js")]
)


# A backtick-balance check was tried here and removed: telling a template literal from a
# backtick inside a regex literal needs a real JS parser (markdown.js has several), and it
# reported files that were perfectly fine. A check that cries wolf is worse than no check.
# The two below are exact rather than heuristic, and either one catches the failure above.


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_no_duplicate_prototype_definitions(path):
    """The same prototype method must not be defined twice in one file.

    A duplicate is the fingerprint of a half-applied replacement: the new definition
    landed while the old one was only partly removed.
    """
    names = re.findall(
        r"^\s*FrejaUIController\.prototype\.(\w+)\s*=", path.read_text(encoding="utf-8"), re.M
    )
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, f"{path.name} defines these prototype methods more than once: {duplicates}"


def test_dashboard_methods_the_hud_calls_are_defined():
    """Every loadXDashboardUI the HUD wires up must exist in ui-dashboards.js.

    Pins the blast radius of the failure above: if the file stops executing, or a method
    is renamed out from under its caller, this fails instead of the HUD silently
    rendering empty panels.
    """
    source = (CLIENT_JS_DIR / "js" / "ui-dashboards.js").read_text(encoding="utf-8")
    required = [
        "loadGarminDashboardUI",
        "loadStravaDashboardUI",
        "loadWithingsDashboardUI",
        "loadGoogleCalendarDashboardUI",
        "loadTrainerDashboardUI",
        "loadTrainerTrendsUI",
        "loadInjuryLogUI",
        "buildTrendCard",
        "buildTrendSparkline",
    ]
    missing = [
        name for name in required
        if not re.search(rf"FrejaUIController\.prototype\.{name}\s*=", source)
    ]
    assert not missing, f"ui-dashboards.js is missing: {missing}"


def test_permission_gateway_does_not_gate_on_a_local_tool_whitelist():
    """ui-tools.js must take the callable tool set from the backend registry.

    It used to carry a hand-written `toolsMetadata` map and reject anything absent from
    it ("Tool '<name>' not recognized"), so every tool added to the registry without a
    matching client entry was silently unreachable from the web UI - that is exactly how
    get_trainer_workouts failed when the user asked about today's training session. The
    labels in TOOL_DISPLAY_NAMES are cosmetic; the gate is /api/tools/metadata plus the
    server-side check in backend/routes/tools.py.
    """
    source = (CLIENT_JS_DIR / "js" / "ui-tools.js").read_text(encoding="utf-8")
    assert "/api/tools/metadata" in source, "ui-tools.js no longer fetches the registry tool list"
    assert "not recognized" not in source, "ui-tools.js rejects tools client-side again"
