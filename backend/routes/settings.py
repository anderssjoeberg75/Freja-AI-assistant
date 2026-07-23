"""Settings API route router using FastAPI."""

import asyncio
import collections
import datetime
import logging
import os
import re
import sys
import subprocess
import json
from fastapi import APIRouter, HTTPException, Request, Query
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

# Cap on-disk log size. The handler below is attached to uvicorn.access, so every HTTP
# request the server handles appends a line here - with no cap, this grows for the entire
# life of the deployment (the DELETE endpoint that would otherwise reclaim it is
# intentionally locked down for compliance reasons, so this is the only backstop).
LOG_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_FILE_TRIM_KEEP_BYTES = 5 * 1024 * 1024  # keep the most recent ~5 MB when trimming


def _rotate_log_file_if_needed():
    try:
        if os.path.getsize(LOG_FILE) <= LOG_FILE_MAX_BYTES:
            return
        with open(LOG_FILE, "rb") as f:
            f.seek(-LOG_FILE_TRIM_KEEP_BYTES, os.SEEK_END)
            tail = f.read()
        # Drop a possibly-truncated first line so every remaining line is valid JSON.
        tail = tail.split(b"\n", 1)[-1] if b"\n" in tail else b""
        with open(LOG_FILE, "wb") as f:
            f.write(tail)
    except (FileNotFoundError, OSError):
        pass


def add_system_log(level: str, message: str):
    """Helper to record a log entry to the in-memory system log queue and append to persistent file."""
    entry = {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level.upper(),
        "message": message
    }
    SYSTEM_LOGS.append(entry)
    try:
        _rotate_log_file_if_needed()
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
async def get_keys(unmask: bool = Query(False, description="Unmask sensitive keys")):
    try:
        return get_all_api_keys(unmask=unmask)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Keys this endpoint must never accept a write for: the app's own auth credential (writing
# it rotates the token every integration and the legitimate owner relies on, or lets whoever
# briefly held a leaked token persist their own), and internal bookkeeping the app manages
# itself (sync watermarks, the heartbeat's client identity, the Garmin backfill queue) that a
# generic "save my settings" call was never meant to touch or corrupt.
_PROTECTED_KEY_NAMES = {"freja_access_token"}
_PROTECTED_KEY_PREFIXES = ("last_sync_", "freja_client_")


def _is_protected_key(key_name: str) -> bool:
    return key_name in _PROTECTED_KEY_NAMES or key_name.startswith(_PROTECTED_KEY_PREFIXES) or key_name == "garmin_backfill_range"


@router.post("/api/keys")
async def post_keys(request: Request):
    try:
        data = await request.json()
        blocked = [k for k in data if _is_protected_key(k)]
        if blocked:
            raise HTTPException(status_code=400, detail=f"These settings cannot be changed here: {', '.join(sorted(blocked))}.")
        for key_name, key_value in data.items():
            # Skip saving if client sends back masked placeholder
            if key_value in ("[MASKED]", "configured") or (key_value and key_value.startswith("••••")):
                continue
            set_api_key(key_name, key_value)
        add_system_log("INFO", "Settings saved to the database.")
        return {'status': 'success'}
    except HTTPException:
        raise
    except Exception as e:
        add_system_log("ERROR", f"Error while saving settings: {e}")
        raise HTTPException(status_code=400, detail=str(e))

async def _delayed_restart():
    await asyncio.sleep(1.5)
    # Trigger systemd service restart or kill uvicorn parent process so systemd cleanly restarts service
    try:
        subprocess.run(["systemctl", "restart", "freja-backend"], check=False)
    except Exception:
        pass
    try:
        os.kill(os.getppid(), 9)
    except Exception:
        pass
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
    """Disabled for compliance/security reasons."""
    raise HTTPException(
        status_code=403,
        detail="Log history deletion is disabled for compliance and security reasons."
    )


@router.get("/api/docs/{filename}")
async def serve_doc_report(filename: str):
    """Securely serves generated codebase audit reports from the docs directory."""
    # The old ".."/leading-slash string checks didn't cover a Windows drive-absolute
    # filename (e.g. "C:\\Users\\...\\keys.db") - FastAPI's {filename} path segment accepts
    # backslashes as ordinary characters, and os.path.join silently discards the base path
    # entirely once the second argument is itself absolute on Windows, so that request
    # served the real file at the absolute path, completely bypassing the docs sandbox.
    # Resolving to a real path and checking containment is the actual safe pattern.
    docs_root = os.path.realpath(os.path.join(PROJECT_ROOT, "docs"))
    report_path = os.path.realpath(os.path.join(docs_root, filename))
    if os.path.commonpath([report_path, docs_root]) != docs_root:
        raise HTTPException(status_code=400, detail="Ogiltigt filnamn.")

    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="The document was not found.")

    return FileResponse(report_path, media_type="text/markdown")


LAST_HEARTBEAT_TIME = 0.0
LAST_HEARTBEAT_INFO = ""
LAST_CLIENT_HOSTNAME = "Unknown"


@router.post("/api/client/heartbeat")
async def client_heartbeat(request: Request):
    """Periodic endpoint called by the Web HUD client to report activity."""
    global LAST_HEARTBEAT_TIME, LAST_HEARTBEAT_INFO, LAST_CLIENT_HOSTNAME
    import time
    LAST_HEARTBEAT_TIME = time.time()
    LAST_HEARTBEAT_INFO = request.headers.get("user-agent", "Unknown browser")
    
    # Try parsing hostname from request body and store in DB if valid. This value is later
    # spliced verbatim into the assistant's own system prompt (see gemini_proxy.py /
    # telegram_service.py), so an unvalidated client-supplied string here is an indirect
    # prompt-injection vector - restrict it to a short, plain hostname-shaped value instead
    # of trusting whatever the client sends.
    try:
        data = await request.json()
        hostname = str(data.get("hostname", "")).strip() if data else ""
        if hostname and hostname != "Unknown" and len(hostname) <= 64 and re.fullmatch(r"[A-Za-z0-9._-]+", hostname):
            LAST_CLIENT_HOSTNAME = hostname
            from backend.database import set_api_key
            set_api_key("freja_client_hostname", hostname)
    except Exception:
        pass

    # Parse and store client OS in DB if valid
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

    if client_os != "Unknown":
        from backend.database import set_api_key
        set_api_key("freja_client_os", client_os)

    return {"status": "ok"}


def get_client_status():
    """Helper to retrieve active status of the client and the host computer name."""
    import time
    import socket
    import platform
    from backend.database import get_api_key
    active = (time.time() - LAST_HEARTBEAT_TIME) < 30.0
    
    client_hostname = get_api_key("freja_client_hostname") or LAST_CLIENT_HOSTNAME
    client_os = get_api_key("freja_client_os") or "Unknown"

    return {
        "active": active,
        "hostname": socket.gethostname(),
        "client_hostname": client_hostname,
        "system": platform.system(),
        "release": platform.release(),
        "client_os": client_os,
        "seconds_since_last": time.time() - LAST_HEARTBEAT_TIME if LAST_HEARTBEAT_TIME > 0 else None,
        "client_info": LAST_HEARTBEAT_INFO
    }


@router.get("/api/system/gemini-models")
async def get_gemini_models():
    """Fetches list of available Gemini models from the Google API if the API key is configured.
    Otherwise returns a fallback list of popular models.
    """
    import httpx
    from backend.services.http_client import shared_client
    from backend.services.gemini_client import get_gemini_api_key

    api_key = get_gemini_api_key()
    
    fallback_models = [
        {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
        {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
        {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
        {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash"},
        {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro"}
    ]

    if not api_key:
        return fallback_models

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        async with shared_client() as client:
            resp = await client.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                result = []
                for m in models:
                    name = m.get("name", "")
                    if name.startswith("models/"):
                        model_id = name.split("models/")[1]
                    else:
                        model_id = name
                    
                    display_name = m.get("displayName", model_id)
                    methods = m.get("supportedGenerationMethods", [])
                    
                    if "generateContent" in methods:
                        result.append({"id": model_id, "name": display_name})
                
                if result:
                    # Sort so newest/important models are first
                    result.sort(key=lambda x: (
                        0 if "2.5-flash" in x["id"] else
                        1 if "2.5-pro" in x["id"] else
                        2 if "2.0-flash" in x["id"] else
                        3,
                        x["id"]
                    ))
                    return result
    except Exception as e:
        import logging
        logging.getLogger("freja").warning(f"Could not fetch models dynamically from Gemini API: {e}")
        
    return fallback_models


@router.get("/api/system/ollama-models")
async def get_ollama_models(base_url: str = Query(None)):
    """Fetches list of available models installed on the configured Ollama server (or given base_url)."""
    import logging
    from backend.services.ollama_client import get_ollama_base_url, get_ollama_model
    from backend.services.http_client import shared_client

    url = (base_url or get_ollama_base_url()).rstrip("/")
    current_model = get_ollama_model()
    fallback_models = [{"id": current_model, "name": current_model}]

    try:
        async with shared_client() as client:
            resp = await client.get(f"{url}/api/tags", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("models", [])
                result = []
                for m in models:
                    name = m.get("name", "")
                    if name:
                        result.append({"id": name, "name": name})
                if result:
                    result.sort(key=lambda x: x["name"])
                    return result
    except Exception as e:
        logging.getLogger("freja").warning(f"Could not fetch models from Ollama server at {url}: {e}")

    return fallback_models




