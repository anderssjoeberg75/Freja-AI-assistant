"""ElevenLabs API secure proxy route."""

import httpx
from backend.services.http_client import shared_client
import hashlib
import os
from fastapi import APIRouter, HTTPException, Request, Response
from backend.database import get_api_key
from backend.config import PROJECT_ROOT, ELEVENLABS_PLACEHOLDER_KEY_HASH

router = APIRouter()

CACHE_DIR = os.path.join(PROJECT_ROOT, "backend", "cache", "voice")
os.makedirs(CACHE_DIR, exist_ok=True)

# ElevenLabs bills per character - cap how much text one request can convert so a buggy or
# malicious caller can't run up unbounded cost with a single call.
MAX_TTS_CHARS = 5000

# Cached clips accumulate forever otherwise (TTS text varies per response, so cache keys rarely
# repeat) - cap the directory to the most-recently-used files instead of growing without bound.
MAX_CACHE_FILES = 500


def _evict_cache_if_needed():
    try:
        entries = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith(".mp3")]
        if len(entries) <= MAX_CACHE_FILES:
            return
        entries.sort(key=lambda p: os.path.getmtime(p))
        for stale_path in entries[:len(entries) - MAX_CACHE_FILES]:
            try:
                os.remove(stale_path)
            except OSError:
                pass
    except OSError as e:
        print(f"[ELEVENLABS] Cache eviction failed: {e}")


@router.post("/api/elevenlabs/tts/{voice_id}")
async def proxy_elevenlabs_tts(voice_id: str, request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    text = payload.get("text", "")
    voice_settings = payload.get("voice_settings", {})
    model_id = payload.get("model_id", "")

    if len(text) > MAX_TTS_CHARS:
        raise HTTPException(status_code=400, detail=f"Text is too long for text-to-speech (max {MAX_TTS_CHARS} characters).")

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
    if not api_key or api_key == ELEVENLABS_PLACEHOLDER_KEY_HASH:
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
                _evict_cache_if_needed()
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

