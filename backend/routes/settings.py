"""Settings API route router using FastAPI."""

import asyncio
import os
import sys
import subprocess
from fastapi import APIRouter, HTTPException, Request

from backend.config import PROJECT_ROOT
from backend.database import get_all_api_keys, set_api_key

router = APIRouter()

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
        return {'status': 'success'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

async def _delayed_restart():
    await asyncio.sleep(1.5)
    # Exit process; systemd / uvicorn / process manager will automatically restart it.
    os._exit(0)

@router.post("/api/system/update")
async def update_from_github():
    """Executes `git pull` from GitHub and restarts the server."""
    try:
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
            return {
                "status": "error",
                "message": f"Git pull misslyckades (felkod {result.returncode})",
                "log": full_log
            }

        # Trigger delayed process exit so systemd/uvicorn restarts the server
        asyncio.create_task(_delayed_restart())

        return {
            "status": "success",
            "message": "Uppdatering hämtad från GitHub! Servern startar om...",
            "log": full_log
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Uppdateringsfel: {str(e)}")
