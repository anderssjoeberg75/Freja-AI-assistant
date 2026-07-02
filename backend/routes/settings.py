"""Settings API route router using FastAPI."""

from fastapi import APIRouter, HTTPException, Request
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


