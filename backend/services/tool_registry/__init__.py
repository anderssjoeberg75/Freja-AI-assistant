"""Unified Tool Registry for F.R.E.J.A.

Defines all tool declarations in Gemini format and implements their execution in Python.
Used by both the web frontend (via API) and the Telegram bot.

Layout of this package (split out of a single 1800+ line tool_registry.py):
  _registry.py      - ToolRegistry / clean_schema / Math_round - the decorator-based registry
                       infrastructure, shared by every domain submodule below.
  weather_search.py, health_data.py, calendar_facebook.py, trainer_tools.py, learning.py,
  system.py, instagram.py, codex_aliases.py
                    - one `exec_*` coroutine per tool, each registered once via
                      `@registry.register(...)` (or `registry.add(...)` for codex_aliases,
                      which wraps executors defined in backend.services.codex_service).
  This file         - imports every submodule above (so their registration side effects run),
                      then derives TOOL_DECLARATIONS, TOOL_PERMISSION_KEYS, EXECUTOR_MAP and
                      execute_tool from the single shared registry, and re-exports the names
                      other modules and tests import directly from `backend.services.tool_registry`.

Note that `execute_tool` itself does NOT enforce permissions. The permission gate is
`backend/routes/tools.py`'s `is_tool_execution_authorized`, called before dispatch on both the
HTTP path (`backend/routes/tools.py`) and the Telegram bot (`backend/services/telegram_service.py`)
- `execute_tool` is ungated by design and every caller is expected to check first.

Language convention: every string in this file is English, including tool descriptions and
tool results. Freja still answers the user in Swedish - that is enforced by the system prompts
(see `client/gemini.js` and `backend/services/telegram_service.py`), which instruct the model to
translate tool output into Swedish before replying.
"""

from ._registry import registry, ToolRegistry, clean_schema, _params_from_pydantic, ToolSpec, Math_round

from . import (
    weather_search, health_data, calendar_facebook, trainer_tools, learning, system,
    instagram, codex_aliases,
)

# Re-exported for external callers (backend/routes/tools.py, backend/services/telegram_service.py)
# and for tests that import specific executors/helpers directly from this package.
from .health_data import exec_garmin_health, get_garmin_data, get_api_key

# ---------------------------------------------------------------------------
# DERIVED PUBLIC STRUCTURES + DISPATCH ENTRY POINT
#
# Everything below is generated from the single registry above, so the historical public
# names keep working for their existing consumers. EXECUTOR_MAP is retained for backward
# compatibility even though execute_tool no longer reads it directly.
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
