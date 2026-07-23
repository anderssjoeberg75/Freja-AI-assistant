"""Provider-neutral LLM facade for Freja's backend.

Which engine answers is an operator setting (`freja_llm_provider`, chosen in the admin
portal): "auto" tries the self-hosted Ollama server first and falls back to the Google
Gemini API when Ollama is unreachable/fails, while "ollama" and "gemini" pin every call
to that one provider. Every backend call site that used to talk to Gemini directly goes
through here instead, so provider selection lives in one place rather than being repeated
at every caller.

Which provider actually served a call is recorded on a context variable so endpoints can
surface it to the UI (see get_active_provider). It is stored per async context, so
concurrent requests never clobber each other's value. For a request-independent view of
what is reachable right now - what the admin portal's indicator draws - see
check_providers().
"""

import asyncio
import contextvars
import logging

from backend.database import get_api_key
from backend.services import ollama_client, gemini_client

logger = logging.getLogger("freja")

PROVIDER_AUTO = "auto"
PROVIDER_AUTO_GEMINI = "auto_gemini"
PROVIDER_OLLAMA = "ollama"
PROVIDER_GEMINI = "gemini"
VALID_PREFERENCES = (PROVIDER_AUTO, PROVIDER_AUTO_GEMINI, PROVIDER_OLLAMA, PROVIDER_GEMINI)

# Records which provider served the most recent call in the current async context.
# A ContextVar (not a module global) so concurrent requests stay isolated.
_active_provider: contextvars.ContextVar = contextvars.ContextVar(
    "freja_llm_active_provider", default="unknown"
)


def get_active_provider() -> str:
    """Returns which provider served the most recent llm_client call in this async
    context: "ollama", "gemini", or "unknown" if none has run yet. Must be read in the
    same coroutine/task that made the call."""
    return _active_provider.get()


def get_provider_preference() -> str:
    """Returns the provider chosen in the admin portal (settings key
    `freja_llm_provider`): "auto", "auto_gemini", "ollama", or "gemini". An unset or
    unrecognised value means "auto", so a stray value in the database can never leave the
    backend with no usable provider."""
    preference = (get_api_key("freja_llm_provider") or "").strip().lower()
    return preference if preference in VALID_PREFERENCES else PROVIDER_AUTO


def _require_gemini_fallback(ollama_err: Exception):
    """Raises with a clear message if there is no Gemini key to fall back to;
    otherwise just logs the Ollama failure and lets the caller retry against Gemini."""
    if not gemini_client.get_gemini_api_key():
        raise Exception(
            f"No LLM provider available: Ollama request failed ({ollama_err}) and no "
            "Gemini API key is configured."
        ) from ollama_err
    logger.warning(f"Ollama request failed, falling back to Gemini: {ollama_err}")


async def _dispatch(operation: str, call_ollama, call_gemini):
    """Runs the request against the configured provider and records which one served it.

    In "auto" (Ollama first) an Ollama failure is logged and retried against Gemini.
    In "auto_gemini" (Gemini first) a Gemini failure is logged and retried against Ollama.
    In the pinned modes the failure surfaces to the caller instead: quietly answering from
    the other engine would contradict an explicit choice, and the admin portal's red
    indicator is what explains the failure."""
    preference = get_provider_preference()

    if preference == PROVIDER_GEMINI:
        result = await call_gemini()
        _active_provider.set(PROVIDER_GEMINI)
        return result

    if preference == PROVIDER_AUTO_GEMINI:
        try:
            result = await call_gemini()
            _active_provider.set(PROVIDER_GEMINI)
            return result
        except Exception as gemini_err:
            logger.warning(f"Gemini request failed, falling back to Ollama: {gemini_err}")
            try:
                result = await call_ollama()
                _active_provider.set(PROVIDER_OLLAMA)
                return result
            except Exception as ollama_err:
                raise Exception(
                    f"No LLM provider available: Gemini request failed ({gemini_err}) and "
                    f"Ollama fallback request failed ({ollama_err})."
                ) from ollama_err

    # Default: PROVIDER_AUTO ("auto" - Ollama first) or PROVIDER_OLLAMA
    try:
        result = await call_ollama()
        _active_provider.set(PROVIDER_OLLAMA)
        return result
    except Exception as ollama_err:
        if preference == PROVIDER_OLLAMA:
            raise Exception(
                f"Ollama {operation} failed and the provider is pinned to Ollama in the "
                f"admin portal, so Gemini was not tried: {ollama_err}"
            ) from ollama_err
        _require_gemini_fallback(ollama_err)
        result = await call_gemini()
        _active_provider.set(PROVIDER_GEMINI)
        return result


async def generate_text(prompt: str, system_instruction: str = "",
                         temperature: float = 0.2, timeout: float = 60.0) -> str:
    """Plain single-turn text generation, routed by the configured provider preference.
    Records the serving provider (see get_active_provider)."""
    return await _dispatch(
        "text generation",
        lambda: ollama_client.generate_text(prompt, system_instruction, temperature, timeout),
        lambda: gemini_client.generate_text(prompt, system_instruction, temperature, timeout),
    )


async def generate_json(prompt: str, schema: dict = None, system_instruction: str = "",
                         temperature: float = 0.3, max_tokens: int = 3000,
                         timeout: float = 60.0) -> dict:
    """JSON-constrained generation, routed by the configured provider preference.
    `schema` uses Gemini's responseSchema dialect (uppercase types) - ollama_client
    translates it for Ollama's `format` field. Pass schema=None for freeform JSON.
    Records the serving provider (see get_active_provider)."""
    return await _dispatch(
        "JSON generation",
        lambda: ollama_client.generate_json(
            prompt, schema, system_instruction, temperature, max_tokens, timeout
        ),
        lambda: gemini_client.generate_json(
            prompt, schema, system_instruction, temperature, max_tokens, timeout
        ),
    )


async def check_providers(timeout: float = 6.0) -> dict:
    """Probes both providers (concurrently) and reports what the admin portal's indicator
    needs: the operator's preference, each provider's reachability, and which provider
    would actually serve a request right now - `active` is None when the preference
    cannot be honoured because the chosen engine (or, in "auto", neither engine) is
    reachable."""
    ollama_status, gemini_status = await asyncio.gather(
        ollama_client.check_health(timeout=timeout),
        gemini_client.check_health(timeout=timeout),
    )

    preference = get_provider_preference()
    if preference == PROVIDER_OLLAMA:
        active = PROVIDER_OLLAMA if ollama_status["ok"] else None
    elif preference == PROVIDER_GEMINI:
        active = PROVIDER_GEMINI if gemini_status["ok"] else None
    elif preference == PROVIDER_AUTO_GEMINI:
        if gemini_status["ok"]:
            active = PROVIDER_GEMINI
        elif ollama_status["ok"]:
            active = PROVIDER_OLLAMA
        else:
            active = None
    elif ollama_status["ok"]:
        active = PROVIDER_OLLAMA
    elif gemini_status["ok"]:
        active = PROVIDER_GEMINI
    else:
        active = None

    return {
        "preference": preference,
        "active": active,
        "providers": {"ollama": ollama_status, "gemini": gemini_status},
    }
