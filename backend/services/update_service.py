"""Shared "pull latest code from GitHub and restart" logic.

Two call sites - `POST /api/system/update` (admin portal) and the `system_update` tool
(Freja's own chat-driven tool-calling) - used to each implement git-pull-then-restart
independently, with different restart strategies (one called `systemctl restart` then
SIGKILLed its parent; the other just `os._exit(0)`'d and trusted a supervisor). A fix to one
(handling a failed `systemctl restart`, guarding against concurrent updates, etc.) never
applied to the other. This is the single implementation both now use.
"""

import asyncio
import os

from backend.config import PROJECT_ROOT
from backend.services.codex_service import run_subprocess_exec


async def git_pull_latest() -> dict:
    """Runs `git pull` and returns {"ok": bool, "log": str, "exit_code": int}."""
    res = await run_subprocess_exec(["git", "pull"], cwd=str(PROJECT_ROOT))
    output = res.get("stdout", "").strip()
    errors = res.get("stderr", "").strip()
    full_log = output + ("\n" + errors if errors else "")
    exit_code = res.get("exit_code", -1)
    return {"ok": exit_code == 0, "log": full_log, "exit_code": exit_code}


async def schedule_restart(delay_seconds: float = 1.5):
    """Delays so the caller can return its response first, then restarts the process.

    Tries `systemctl restart freja-backend` (the production supervisor) first, then falls
    back to killing the parent uvicorn process, then a plain process exit - covers both the
    systemd-supervised deployment and a bare `python server.py` run with no supervisor.
    """
    await asyncio.sleep(delay_seconds)
    try:
        import subprocess
        subprocess.run(["systemctl", "restart", "freja-backend"], check=False, timeout=10)
    except Exception:
        pass
    try:
        os.kill(os.getppid(), 9)
    except Exception:
        pass
    os._exit(0)
