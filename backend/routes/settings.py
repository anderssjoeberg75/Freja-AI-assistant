"""Settings API route router using FastAPI."""

import asyncio
import collections
import datetime
import logging
import os
import sys
import subprocess
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from backend.config import PROJECT_ROOT
from backend.database import get_all_api_keys, set_api_key

router = APIRouter()

# Persistent log file path
LOG_FILE = os.path.join(PROJECT_ROOT, "backend", "cache", "freja_security.log")

# Create cache directory if it doesn't exist
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# In-memory circular log buffer (up to 150 entries)
SYSTEM_LOGS = collections.deque(maxlen=150)

def add_system_log(level: str, message: str):
    """Helper to record a log entry to the in-memory system log queue and append to persistent file."""
    entry = {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level.upper(),
        "message": message
    }
    SYSTEM_LOGS.append(entry)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

# Load existing persistent logs on boot
if os.path.exists(LOG_FILE):
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        SYSTEM_LOGS.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass

# Add initial boot log if empty
if not SYSTEM_LOGS:
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
        add_system_log("INFO", "Settings saved to the database.")
        return {'status': 'success'}
    except Exception as e:
        add_system_log("ERROR", f"Error while saving settings: {e}")
        raise HTTPException(status_code=400, detail=str(e))

async def _delayed_restart():
    await asyncio.sleep(1.5)
    # Exit process; systemd / uvicorn / process manager will automatically restart it.
    os._exit(0)

@router.post("/api/system/update")
async def update_from_github():
    """Executes `git pull` from GitHub and restarts the server."""
    try:
        add_system_log("INFO", "Starting update from GitHub (git pull)...")
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
            add_system_log("ERROR", f"Git pull failed: {full_log}")
            return {
                "status": "error",
                "message": f"Git pull failed (exit code {result.returncode})",
                "log": full_log
            }

        add_system_log("INFO", f"Git pull completed: {output}")
        add_system_log("INFO", "The server is restarting...")

        # Trigger delayed process exit so systemd/uvicorn restarts the server
        asyncio.create_task(_delayed_restart())

        return {
            "status": "success",
            "message": "Update downloaded from GitHub. The server is restarting...",
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
    """Clears system log history and deletes the log file."""
    SYSTEM_LOGS.clear()
    if os.path.exists(LOG_FILE):
        try:
            os.remove(LOG_FILE)
        except Exception:
            pass
    add_system_log("INFO", "Log history cleared.")
    return {"status": "success"}


@router.get("/api/docs/{filename}")
async def serve_doc_report(filename: str):
    """Securely serves generated codebase audit reports from the docs directory."""
    # Prevent directory traversal attacks
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        raise HTTPException(status_code=400, detail="Ogiltigt filnamn.")

    report_path = os.path.join(PROJECT_ROOT, "docs", filename)
    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="Dokumentet hittades inte.")

    return FileResponse(report_path, media_type="text/markdown")


LAST_HEARTBEAT_TIME = 0.0
LAST_HEARTBEAT_INFO = ""


@router.post("/api/client/heartbeat")
async def client_heartbeat(request: Request):
    """Periodic endpoint called by the Web HUD client to report activity."""
    global LAST_HEARTBEAT_TIME, LAST_HEARTBEAT_INFO
    import time
    LAST_HEARTBEAT_TIME = time.time()
    LAST_HEARTBEAT_INFO = request.headers.get("user-agent", "Unknown browser")
    return {"status": "ok"}


def get_client_status():
    """Helper to retrieve active status of the client and the host computer name."""
    import time
    import socket
    import platform
    active = (time.time() - LAST_HEARTBEAT_TIME) < 30.0
    
    # Parse client OS from stored User-Agent heartbeat info
    ua = LAST_HEARTBEAT_INFO or ""
    client_os = "Unknown"
    if "Windows" in ua:
        client_os = "Windows (likely Windows 11)"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        client_os = "macOS"
    elif "Linux" in ua:
        client_os = "Linux"
    elif "Android" in ua:
        client_os = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        client_os = "iOS"

    return {
        "active": active,
        "hostname": socket.gethostname(),
        "system": platform.system(),
        "release": platform.release(),
        "client_os": client_os,
        "seconds_since_last": time.time() - LAST_HEARTBEAT_TIME if LAST_HEARTBEAT_TIME > 0 else None,
        "client_info": LAST_HEARTBEAT_INFO
    }


