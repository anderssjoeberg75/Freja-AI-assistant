"""Freja FastAPI backend entry point.

Wiring order matters here and is easy to get wrong when debugging:

  1. `init_db()` runs at import time, before any router is registered, so every route can
     assume its tables exist.
  2. Middleware is applied bottom-up in Starlette: `FrejaAuthMiddleware` is added last and
     therefore runs FIRST, rejecting unauthenticated requests before CORS headers are added.
  3. Routers are then mounted, followed by the admin page at "/" and the client HUD at
     "/client". Static mounts come last so they never shadow an API route.
  4. `lifespan` starts the Telegram polling worker on boot and cancels it on shutdown.

Language convention for the whole project: all source text - comments, log lines, error
messages, tool descriptions and UI copy - is written in English. Freja nevertheless answers
the user in Swedish, which is enforced by the system prompts (see `client/gemini.js` and
`backend/services/telegram_service.py`), not by the language of the code.
"""

import os
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.config import PORT, PROJECT_ROOT
from backend.database import init_db
import asyncio
from backend.services.telegram_service import telegram_worker_loop

# Import routers
from backend.routes.settings import router as settings_router
from backend.routes.search import router as search_router
from backend.routes.garmin import router as garmin_router
from backend.routes.strava import router as strava_router
from backend.routes.withings import router as withings_router
from backend.routes.gemini_proxy import router as gemini_router
from backend.routes.elevenlabs_proxy import router as elevenlabs_router
from backend.routes.mem0_proxy import router as mem0_router
from backend.routes.instagram import router as instagram_router
from backend.routes.telegram import router as telegram_router
from backend.routes.chat import router as chat_router
from backend.routes.sync import router as sync_router
from backend.routes.google_calendar import router as google_calendar_router
from backend.routes.tools import router as tools_router
from backend.routes.trainer import router as trainer_router
from backend.routes.learning import router as learning_router

# Initialize the SQLite database schemas
init_db()

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Owns the Telegram polling worker's lifetime.

    The worker takes a file lock (`.telegram_bot.lock`) so that when uvicorn runs with
    reload or multiple workers, only one process actually polls Telegram."""
    # Startup: Initialize the Telegram background worker
    from backend.services.task_queue import start_task_queue, stop_task_queue
    start_task_queue()
    task = asyncio.create_task(telegram_worker_loop())
    yield
    # Shutdown: Clean up background tasks
    task.cancel()
    await stop_task_queue()
    # Dispose the shared outbound HTTP connection pool (Issue #24).
    from backend.services.http_client import close_shared_client
    await close_shared_client()
    try:
        await task
    except asyncio.CancelledError:
        pass

# Create FastAPI app
app = FastAPI(
    title="F.R.E.J.A. Neural Backend",
    description="FastAPI migration serving cybernetic health dashboard diagnostics and secure AI proxying.",
    version="2.0.0",
    lifespan=lifespan
)

# Register CORS Middleware.
#
# This used to be allow_origins=["*"], argued safe because allow_credentials is False and
# every protected route requires the X-Freja-Token header. The argument holds only while
# authentication is airtight: with "*" any page on the internet may READ an API response,
# so the moment a token check lapses, a contained auth bug becomes full credential
# disclosure (exactly what happened - see #55, and #19/#41).
#
# Loopback and private/LAN origins are matched by regex because they are the ones a
# self-hosted HUD actually uses (:5000 talking to :8000, or over the LAN) and no public
# web page can present them. Anything beyond that must be listed in the
# `freja_allowed_origins` setting; that list is read here at startup, so a change to it
# needs a restart to affect CORS.
from backend.origins import PRIVATE_ORIGIN_REGEX, configured_origins

try:
    _explicit_origins = configured_origins()
except Exception:
    _explicit_origins = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=_explicit_origins,
    allow_origin_regex=PRIVATE_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Authentication Middleware. Added last, so it is the OUTERMOST middleware and runs
# before CORS on every request - see the note in the module docstring.
from backend.middleware.auth import FrejaAuthMiddleware
app.add_middleware(FrejaAuthMiddleware)


# Include API routers
app.include_router(settings_router)
app.include_router(search_router)
app.include_router(garmin_router)
app.include_router(strava_router)
app.include_router(withings_router)
app.include_router(gemini_router)
app.include_router(elevenlabs_router)
app.include_router(mem0_router)
app.include_router(instagram_router)
app.include_router(telegram_router)
app.include_router(chat_router)
app.include_router(sync_router)
app.include_router(google_calendar_router)
app.include_router(tools_router)
app.include_router(trainer_router)
app.include_router(learning_router)

# Paths
ADMIN_HTML = os.path.join(PROJECT_ROOT, "backend", "admin", "admin.html")
CLIENT_DIR = os.path.join(PROJECT_ROOT, "client")

# Serve Backend Admin GUI at "/" and "/admin"
@app.get("/")
@app.get("/admin")
async def read_admin():
    if os.path.exists(ADMIN_HTML):
        return FileResponse(ADMIN_HTML)
    return {"detail": "Backend admin.html not found."}

# Mount Client static files at "/client"
if os.path.exists(CLIENT_DIR):
    app.mount("/client", StaticFiles(directory=CLIENT_DIR, html=True), name="client_static")

def run_server():
    """Start the Uvicorn ASGI server."""
    from backend.database import get_api_key
    active_token = get_api_key('freja_access_token')
    if active_token:
        masked_token = active_token[:4] + "..." + active_token[-4:] if len(active_token) > 8 else "..."
    else:
        masked_token = "Not Set"
    print("===========================================================")
    print(f"  F.R.E.J.A. Neural Backend Control Panel: http://localhost:{PORT}")
    print(f"  Client HUD (Bundled Mode): http://localhost:{PORT}/client/")
    print(f"  Active Access Token: {masked_token}")
    print("  API keys database & security active (FastAPI Mode)")
    print("===========================================================")
    # reload=True forces SelectorEventLoop on Windows, which doesn't support subprocesses (needed for Playwright).
    # Thus, reload is disabled on Windows.
    reload_enabled = os.name != 'nt'
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, log_level="info", reload=reload_enabled)

if __name__ == "__main__":
    run_server()
