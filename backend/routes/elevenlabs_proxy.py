"""ElevenLabs API secure proxy route."""

import httpx
from backend.services.http_client import shared_client
import hashlib
import os
from fastapi import APIRouter, HTTPException, Request, Response
from backend.database import get_api_key
from backend.config import PROJECT_ROOT

router = APIRouter()

CACHE_DIR = os.path.join(PROJECT_ROOT, "backend", "cache", "voice")
os.makedirs(CACHE_DIR, exist_ok=True)

@router.post("/api/elevenlabs/tts/{voice_id}")
async def proxy_elevenlabs_tts(voice_id: str, request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    text = payload.get("text", "")
    voice_settings = payload.get("voice_settings", {})
    model_id = payload.get("model_id", "")
    
    # Generate cache key based on voice_id and payload parameters
    hash_input = f"{voice_id}_{text}_{model_id}_{str(voice_settings)}".encode("utf-8")
    cache_key = hashlib.sha256(hash_input).hexdigest()
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.mp3")
    
    # Check if voice file is already cached
    if os.path.exists(cache_path):
        print(f"[ELEVENLABS] Serving cached voice audio for text: '{text[:20]}...'")
        try:
            with open(cache_path, "rb") as f:
                cached_audio = f.read()
            return Response(content=cached_audio, media_type="audio/mpeg")
        except Exception as e:
            print(f"[ELEVENLABS] Failed to read cache file: {e}")

    # Retrieve API key from SQLite keys.db
    api_key = get_api_key('freja_eleven_apikey') or ""
    if not api_key or api_key == "e4984cf824dd4f39f489d3dd4ed6f22518700d4ad0f9a8077a7915a85b23b81d":
        raise HTTPException(status_code=400, detail="ElevenLabs API key is not configured on the server.")


    # Call official ElevenLabs API
    eleven_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }

    try:
        async with shared_client() as client:
            response = await client.post(eleven_url, json=payload, headers=headers, timeout=30.0)
            if response.status_code != 200:
                return Response(
                    content=response.content,
                    status_code=response.status_code,
                    media_type="application/json"
                )
            
            # Save audio stream to cache
            try:
                with open(cache_path, "wb") as f:
                    f.write(response.content)
            except Exception as e:
                print(f"[ELEVENLABS] Failed to write cache file: {e}")
                
            # Return audio stream
            return Response(
                content=response.content,
                status_code=200,
                media_type="audio/mpeg"
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with ElevenLabs API: {str(e)}")

