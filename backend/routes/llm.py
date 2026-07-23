"""LLM provider status route.

Backs the admin portal's provider indicator: which engine is selected, whether the
backend can actually reach it, and what the other one looks like.
"""

import time

from fastapi import APIRouter, Query

from backend.services import llm_client

router = APIRouter()

# The portal polls this on a timer, and every probe costs one request to the Ollama box
# plus one to Google. Sharing a result for this long keeps an indicator light fresh
# enough while stopping several open admin tabs from multiplying that traffic.
_STATUS_CACHE_TTL_SECONDS = 10.0
_status_cache = {"expires_at": 0.0, "payload": None}


@router.get("/api/system/llm-status")
async def get_llm_status(refresh: bool = Query(False, description="Bypass the short status cache")):
    """Returns the selected LLM provider, per-provider reachability, and which provider
    would serve a request right now. `refresh=true` forces a fresh probe - the portal uses
    it right after settings are saved, when the cached answer is known to be stale."""
    now = time.monotonic()
    if not refresh and _status_cache["payload"] is not None and now < _status_cache["expires_at"]:
        return _status_cache["payload"]

    payload = await llm_client.check_providers()
    _status_cache["payload"] = payload
    _status_cache["expires_at"] = now + _STATUS_CACHE_TTL_SECONDS
    return payload
