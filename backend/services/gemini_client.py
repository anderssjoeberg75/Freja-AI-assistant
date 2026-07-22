"""Shared Google Gemini API client.

Centralizes API-key retrieval, model selection, and text generation so that
codex_service, the gemini proxy route, and other services don't each hardcode
the endpoint URL and model name.
"""

import httpx
from backend.services.http_client import shared_client

from backend.database import get_api_key

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def get_gemini_api_key() -> str:
    """Returns the configured Gemini API key, or an empty string if unset."""
    return get_api_key("freja_gemini_apikey") or ""


def get_gemini_model() -> str:
    """Returns the configured Gemini model name (settings key 'freja_gemini_model'),
    falling back to the project default."""
    return get_api_key("freja_gemini_model") or DEFAULT_GEMINI_MODEL


def build_generate_url(model: str, api_key: str) -> str:
    return f"{GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"


async def generate_text(prompt: str, system_instruction: str = "",
                         temperature: float = 0.2, timeout: float = 60.0) -> str:
    """Sends a single-turn text prompt to Gemini and returns the generated text."""
    api_key = get_gemini_api_key()
    if not api_key:
        raise Exception("Gemini API key is missing from the database.")

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    url = build_generate_url(get_gemini_model(), api_key)
    async with shared_client() as client:
        resp = await client.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        resp_json = resp.json()

    # Gemini can return HTTP 200 with an explicitly empty "candidates" list (e.g. a
    # safety-blocked prompt) - candidates[0] would then raise IndexError instead of a clear
    # "no output" result, since dict.get(key, default) only substitutes on a missing key.
    candidates = resp_json.get("candidates") or [{}]
    parts = candidates[0].get("content", {}).get("parts") or [{}]
    return parts[0].get("text", "")
