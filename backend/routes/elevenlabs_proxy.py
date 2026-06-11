"""ElevenLabs API secure proxy route."""

import sqlite3
import requests
from fastapi import APIRouter, HTTPException, Request, Response
from backend.config import DB_FILE

router = APIRouter()

@router.post("/api/elevenlabs/tts/{voice_id}")
async def proxy_elevenlabs_tts(voice_id: str, request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Retrieve API key from SQLite keys.db
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_eleven_apikey'")
    row = cursor.fetchone()
    conn.close()

    api_key = row[0].strip() if row else ""
    if not api_key:
        raise HTTPException(status_code=400, detail="ElevenLabs API key is not configured on the server.")

    # Call official ElevenLabs API
    eleven_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(eleven_url, json=payload, headers=headers, timeout=30)
        if response.status_code != 200:
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type="application/json"
            )
        # Return audio stream
        return Response(
            content=response.content,
            status_code=200,
            media_type="audio/mpeg"
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with ElevenLabs API: {str(e)}")
