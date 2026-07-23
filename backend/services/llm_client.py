"""Provider-neutral LLM facade for Freja's backend.

Tries the self-hosted Ollama server first; falls back to the Google Gemini API only
when Ollama is unreachable/fails AND a Gemini API key is configured. Every backend
call site that used to talk to Gemini directly goes through here instead, so provider
selection lives in one place rather than being repeated at every caller.
"""

import logging

from backend.services import ollama_client, gemini_client

logger = logging.getLogger("freja")


def _require_gemini_fallback(ollama_err: Exception):
    """Raises with a clear message if there is no Gemini key to fall back to;
    otherwise just logs the Ollama failure and lets the caller retry against Gemini."""
    if not gemini_client.get_gemini_api_key():
        raise Exception(
            f"No LLM provider available: Ollama request failed ({ollama_err}) and no "
            "Gemini API key is configured."
        ) from ollama_err
    logger.warning(f"Ollama request failed, falling back to Gemini: {ollama_err}")


async def generate_text(prompt: str, system_instruction: str = "",
                         temperature: float = 0.2, timeout: float = 60.0) -> str:
    """Plain single-turn text generation. Tries Ollama, falls back to Gemini."""
    try:
        return await ollama_client.generate_text(prompt, system_instruction, temperature, timeout)
    except Exception as ollama_err:
        _require_gemini_fallback(ollama_err)
        return await gemini_client.generate_text(prompt, system_instruction, temperature, timeout)


async def generate_json(prompt: str, schema: dict = None, system_instruction: str = "",
                         temperature: float = 0.3, max_tokens: int = 3000,
                         timeout: float = 60.0) -> dict:
    """JSON-constrained generation. `schema` uses Gemini's responseSchema dialect
    (uppercase types) - ollama_client translates it for Ollama's `format` field. Pass
    schema=None for freeform JSON. Tries Ollama, falls back to Gemini."""
    try:
        return await ollama_client.generate_json(
            prompt, schema, system_instruction, temperature, max_tokens, timeout
        )
    except Exception as ollama_err:
        _require_gemini_fallback(ollama_err)
        return await gemini_client.generate_json(
            prompt, schema, system_instruction, temperature, max_tokens, timeout
        )
