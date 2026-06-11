"""Gemini API secure proxy route."""

import sqlite3
import requests
from fastapi import APIRouter, HTTPException, Query, Request
from backend.config import DB_FILE

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
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_gemini_apikey'")
    row = cursor.fetchone()
    conn.close()

    api_key = row[0].strip() if row else ""
    if not api_key:
        raise HTTPException(status_code=400, detail="Gemini API key is not configured on the server.")

    # Call official Gemini endpoint
    google_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    try:
        response = requests.post(google_url, json=payload, timeout=30)
        # Forward headers and response
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with Google Gemini API: {str(e)}")
