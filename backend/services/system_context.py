"""Builds the "what am I actually running on" block injected into Freja's system prompt.

Every surface (the main chat proxy, the Telegram bot, ...) composes this block from the
same builder, so Freja's account of her own backend cannot drift between them - previously
the chat proxy carried its own copy, which reported the wrong Ollama URL, the wrong default
model, and both providers as permanently offline.

Two pieces, because they are known at different times:

  * ``build_backend_context_block`` - the configuration, built before the request is
    dispatched: what the operator selected, what each provider's model is, whether each one
    is reachable, which machines are involved, which integrations are configured.
  * ``build_runtime_provider_line`` - the single line naming the engine that is *actually*
    serving this reply. It can only be added once ``llm_client`` has picked a provider, so
    it is appended inside each provider's own branch. Injecting a guess before dispatch is
    what made Freja confidently name the wrong engine whenever the fallback fired.

Nothing secret belongs in here. The block is part of the prompt, so in Gemini mode it is
sent verbatim to Google: credentials are reported as configured / not configured, never by
value.
"""

import logging

from backend.database import get_api_key
from backend.services import ollama_client

logger = logging.getLogger("freja")

# (label, settings key) - only the presence of each value is ever reported.
_INTEGRATION_KEYS = (
    ("Garmin", "freja_garmin_email"),
    ("Strava", "freja_strava_client_id"),
    ("Withings", "freja_withings_client_id"),
    ("Google Calendar", "freja_google_calendar_client_id"),
    ("Telegram", "freja_telegram_bot_token"),
    ("Instagram", "freja_instagram_access_token"),
    ("ElevenLabs voice", "freja_eleven_apikey"),
    ("Mem0 memory", "freja_mem0_apikey"),
)

_PREFERENCE_DESCRIPTIONS = {
    "auto": "auto (Ollama first, Gemini as fallback)",
    "auto_gemini": "auto_gemini (Gemini first, Ollama as fallback)",
    "ollama": "ollama (pinned to the self-hosted server, no fallback)",
    "gemini": "gemini (pinned to the Google API, no fallback)",
}

PROVIDER_LABELS = {"ollama": "Ollama (self-hosted)", "gemini": "Google Gemini"}

# Cap on the "why is it offline" reason, per provider. Anything longer is noise the model
# pays for on every turn (prompt evaluation is the dominant cost when Ollama runs on CPU).
_MAX_DETAIL_CHARS = 120


def describe_preference(preference: str) -> str:
    """Human-readable form of the operator's provider setting."""
    return _PREFERENCE_DESCRIPTIONS.get(preference, preference or "unknown")


def _enabled_tool_names() -> list:
    """Names of the tools the operator has permanently allowed, via the same authority the
    executor consults - so the prompt cannot claim a capability the permission gate denies."""
    try:
        from backend.routes.tools import TOOL_PERMISSION_KEYS, is_tool_permanently_allowed
        return sorted(name for name in TOOL_PERMISSION_KEYS if is_tool_permanently_allowed(name))
    except Exception as e:
        logger.warning(f"Could not read tool permissions for the system context: {e}")
        return []


def build_backend_context_block(provider_status: dict, client_status: dict = None,
                                 client_os: str = "Unknown") -> str:
    """Renders the backend configuration block.

    `provider_status` is a `llm_client.check_providers()` result; `client_status` is a
    `settings.get_client_status()` result (both optional-tolerant, since a missing piece
    must degrade to "unknown" rather than break the chat)."""
    provider_status = provider_status or {}
    client_status = client_status or {}
    providers = provider_status.get("providers") or {}
    ollama = providers.get("ollama") or {}
    gemini = providers.get("gemini") or {}

    preference = provider_status.get("preference") or "auto"
    active = provider_status.get("active")

    def provider_state(status):
        if status.get("ok"):
            return "ONLINE"
        # httpx failure messages run to several lines with a URL and a documentation link.
        # Every token here is prompt the model has to read on each turn, so the reason is
        # cut to its first line and a sentence's worth of characters.
        detail = (status.get("detail") or "no detail").splitlines()[0].strip()
        if len(detail) > _MAX_DETAIL_CHARS:
            detail = detail[:_MAX_DETAIL_CHARS].rstrip() + "..."
        return f"OFFLINE ({detail})"

    integrations = ", ".join(
        f"{label} {'configured' if get_api_key(key) else 'not configured'}"
        for label, key in _INTEGRATION_KEYS
    )
    enabled_tools = _enabled_tool_names()
    backend_os = " ".join(
        part for part in (client_status.get("system"), client_status.get("release")) if part
    ) or "unknown"

    lines = [
        "",
        "",
        "[BACKEND CONFIGURATION - LIVE AND AUTHORITATIVE]",
        "- Identity: You are F.R.E.J.A., Anders' personal AI assistant, running on his own backend.",
        f"- AI provider setting (chosen by Anders in the backend control center): {describe_preference(preference)}.",
        f"- Ollama (self-hosted): {provider_state(ollama)} - model '{ollama.get('model') or ollama_client.get_ollama_model()}' "
        f"at {ollama.get('base_url') or ollama_client.get_ollama_base_url()}, context window {ollama_client.get_ollama_num_ctx()} tokens.",
        f"- Google Gemini API: {provider_state(gemini)} - model '{gemini.get('model') or 'unknown'}'.",
        f"- Provider that would serve a request right now: {PROVIDER_LABELS.get(active, 'none - no provider is reachable')}.",
        f"- Backend server host: '{client_status.get('hostname') or 'unknown'}' (OS: {backend_os}).",
        f"- Web client host: '{client_status.get('client_hostname') or 'unknown'}' (OS: {client_os}).",
        f"- Integrations: {integrations}.",
        f"- Permanently allowed tools: {', '.join(enabled_tools) if enabled_tools else 'none'}.",
        "- DIRECTIVE: These facts are authoritative - never guess or invent them. If Anders asks which AI "
        "model, provider or machine you are running on, or how the backend is configured, answer from this "
        "block, in Swedish, and name the engine that is actually serving the reply.",
    ]
    return "\n".join(lines)


def build_runtime_provider_line(provider: str, model: str = "") -> str:
    """The one line that states which engine is serving *this* reply. Appended inside the
    provider branch, where the answer is known rather than guessed."""
    label = PROVIDER_LABELS.get(provider, provider or "unknown")
    model_part = f", model '{model}'" if model else ""
    return (
        f"\n- ENGINE SERVING THIS REPLY: {label}{model_part}. This is the engine executing right now; "
        "if the fallback was used, this line - not the status above - is the truth."
    )
