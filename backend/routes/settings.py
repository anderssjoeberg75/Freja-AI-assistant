"""Settings API route router using FastAPI."""

import asyncio
import collections
import datetime
import logging
import os
import sys
import subprocess
from fastapi import APIRouter, HTTPException, Request

from backend.config import PROJECT_ROOT
from backend.database import get_all_api_keys, set_api_key

router = APIRouter()

# In-memory circular log buffer (up to 150 entries)
SYSTEM_LOGS = collections.deque(maxlen=150)

def add_system_log(level: str, message: str):
    """Helper to record a log entry to the in-memory system log queue."""
    SYSTEM_LOGS.append({
        "time": datetime.datetime.now().strftime("%H:%M:%S"),
        "level": level.upper(),
        "message": message
    })

# Add initial boot log
add_system_log("INFO", "F.R.E.J.A. System Log Monitor Initialized.")

class MemoryLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            add_system_log(record.levelname, msg)
        except Exception:
            pass

log_handler = MemoryLogHandler()
log_handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
logging.getLogger("freja").addHandler(log_handler)
logging.getLogger("uvicorn.access").addHandler(log_handler)

@router.get("/api/keys")
async def get_keys():
    try:
        return get_all_api_keys()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/keys")
async def post_keys(request: Request):
    try:
        data = await request.json()
        for key_name, key_value in data.items():
            # Skip saving if client sends back masked placeholder
            if key_value in ("[MASKED]", "configured") or (key_value and key_value.startswith("••••")):
                continue
            set_api_key(key_name, key_value)
        add_system_log("INFO", "Inställningar sparades i databasen.")
        return {'status': 'success'}
    except Exception as e:
        add_system_log("ERROR", f"Fel vid sparning av inställningar: {e}")
        raise HTTPException(status_code=400, detail=str(e))

async def _delayed_restart():
    await asyncio.sleep(1.5)
    # Exit process; systemd / uvicorn / process manager will automatically restart it.
    os._exit(0)

@router.post("/api/system/update")
async def update_from_github():
    """Executes `git pull` from GitHub and restarts the server."""
    try:
        add_system_log("INFO", "Startar uppdatering från GitHub (git pull)...")
        result = subprocess.run(
            ["git", "pull"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout.strip()
        errors = result.stderr.strip()
        full_log = output + ("\n" + errors if errors else "")

        if result.returncode != 0:
            add_system_log("ERROR", f"Git pull misslyckades: {full_log}")
            return {
                "status": "error",
                "message": f"Git pull misslyckades (felkod {result.returncode})",
                "log": full_log
            }

        add_system_log("INFO", f"Git pull genomförd: {output}")
        add_system_log("INFO", "Servern startar om...")

        # Trigger delayed process exit so systemd/uvicorn restarts the server
        asyncio.create_task(_delayed_restart())

        return {
            "status": "success",
            "message": "Uppdatering hämtad från GitHub! Servern startar om...",
            "log": full_log
        }
    except Exception as e:
        add_system_log("ERROR", f"Uppdateringsfel: {e}")
        raise HTTPException(status_code=500, detail=f"Uppdateringsfel: {str(e)}")

@router.get("/api/system/logs")
async def get_system_logs():
    """Returns recent system logs for the admin terminal monitor."""
    return {"logs": list(SYSTEM_LOGS)}

@router.delete("/api/system/logs")
async def clear_system_logs():
    """Clears system log history."""
    SYSTEM_LOGS.clear()
    add_system_log("INFO", "Logghistorik rensad.")
    return {"status": "success"}
