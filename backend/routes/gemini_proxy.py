"""Gemini API secure proxy route."""

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from backend.database import get_api_key

router = APIRouter()

@router.post("/api/gemini/generate")
async def proxy_gemini_generate(
    request: Request,
    model: str = Query("gemini-2.5-flash", description="Gemini model identifier")
):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Retrieve API key from SQLite keys.db
    api_key = get_api_key('freja_gemini_apikey') or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API key is not configured on the server.")

    # Call official Gemini endpoint
    google_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(google_url, json=payload, timeout=30.0)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Google Gemini API error: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Google Gemini API: {str(e)}")

