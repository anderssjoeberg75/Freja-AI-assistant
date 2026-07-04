"""Gemini API secure proxy route."""

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from backend.services import gemini_client

router = APIRouter()

@router.post("/api/gemini/generate")
async def proxy_gemini_generate(
    request: Request,
    model: str = Query(None, description="Gemini model identifier (defaults to server-configured model)")
):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    api_key = gemini_client.get_gemini_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API key is not configured on the server.")

    # Call official Gemini endpoint
    google_url = gemini_client.build_generate_url(model or gemini_client.get_gemini_model(), api_key)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(google_url, json=payload, timeout=30.0)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Google Gemini API error: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Google Gemini API: {str(e)}")
