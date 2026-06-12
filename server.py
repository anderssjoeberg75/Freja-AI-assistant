"""Freja FastAPI backend entry point."""

import os
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

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
from backend.routes.telegram import router as telegram_router
from backend.routes.chat import router as chat_router
from backend.routes.sync import router as sync_router

# Initialize the SQLite database schemas
init_db()

# Create FastAPI app
app = FastAPI(
    title="F.R.E.J.A. Neural Backend",
    description="FastAPI migration serving cybernetic health dashboard diagnostics and secure AI proxying.",
    version="2.0.0"
)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(telegram_worker_loop())

# Include API routers
app.include_router(settings_router)
app.include_router(search_router)
app.include_router(garmin_router)
app.include_router(strava_router)
app.include_router(withings_router)
app.include_router(gemini_router)
app.include_router(elevenlabs_router)
app.include_router(mem0_router)
app.include_router(telegram_router)
app.include_router(chat_router)
app.include_router(sync_router)

# Serve index.html specifically at "/"
@app.get("/")
async def read_index():
    return FileResponse(os.path.join(PROJECT_ROOT, "index.html"))

# Serve all other static files (app.js, style.css, models, etc.) from project root
app.mount("/", StaticFiles(directory=str(PROJECT_ROOT)), name="static")

def run_server():
    """Start the Uvicorn ASGI server."""
    print("===========================================================")
    print(f"  F.R.E.J.A. Neural Server running on http://localhost:{PORT}")
    print("  API keys database active (FastAPI Mode)")
    print("===========================================================")
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, log_level="info")

if __name__ == "__main__":
    run_server()
