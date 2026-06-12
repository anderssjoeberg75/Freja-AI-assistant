"""Device sync status query route."""

from fastapi import APIRouter
from backend.services.sync_status import get_sync_states

router = APIRouter()

@router.get("/api/sync/status")
async def get_sync_status():
    """Fetches the active states of Garmin, Strava, and Withings sync background tasks."""
    return get_sync_states()
