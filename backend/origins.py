"""One definition of which browser origins F.R.E.J.A. trusts.

Three places need to answer the same question - the CORS policy in `server.py`, the CORS
headers on auth failures in `backend/middleware/auth.py`, and the OAuth redirect check in
`backend/routes/google_calendar.py`. They used to answer it three different ways, and the
loosest of them (`allow_origins=["*"]`) set the real security boundary: any page on the
internet could read an API response, so a lapse anywhere in authentication became full
credential disclosure rather than a contained bug (see issues #19, #41, #55).

The policy, in order:

  * Loopback is always trusted. The HUD normally runs on a different port than the backend
    (:5000 vs :8000), so it is cross-origin by definition, and a page served from the
    user's own machine is not an exfiltration channel.
  * Private (RFC 1918 / link-local) addresses are trusted, because a self-hosted Freja is
    routinely reached over the LAN and a hostile public web page can never present such an
    origin.
  * Anything else must be opted into explicitly via the comma-separated
    `freja_allowed_origins` setting.

Public-internet origins therefore get nothing back, which is the whole point.
"""

import ipaddress
from urllib.parse import urlparse

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")

# Matches the loopback and private-range hosts trusted above. Used to build the CORS
# regex, so it has to describe exactly the same set as `is_trusted_host`.
PRIVATE_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost"
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|\[::1\]"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|169\.254\.\d{1,3}\.\d{1,3}"
    r"|[A-Za-z0-9-]+\.local"
    r")(:\d+)?$"
)


def origin_of(url: str):
    """Normalized 'scheme://host[:port]' for `url`, or None if it is not a usable http(s) origin.

    Rebuilt from the parsed parts rather than sliced out of the string, so userinfo
    ("https://evil.com@trusted.host") and paths cannot smuggle a different target through.
    """
    try:
        parsed = urlparse((url or "").strip())
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return f"{parsed.scheme}://{parsed.hostname}" + (f":{port}" if port else "")


def is_trusted_host(host: str) -> bool:
    """True for loopback, private-range and .local hosts - i.e. anything on the user's own
    machine or LAN, which a public web page can never present as its origin."""
    if not host:
        return False
    host = host.strip().strip("[]").lower()
    if host in LOOPBACK_HOSTS or host.endswith(".local"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False  # A public hostname must be opted into explicitly.
    return addr.is_loopback or addr.is_private or addr.is_link_local


def configured_origins() -> list:
    """The explicitly opted-in origins from the `freja_allowed_origins` setting."""
    # Imported lazily: this module is loaded while the app is being constructed, before the
    # database layer is necessarily ready.
    from backend.database import get_api_key

    try:
        raw = get_api_key("freja_allowed_origins") or ""
    except Exception:
        return []
    return [o for o in (origin_of(entry) for entry in raw.split(",") if entry.strip()) if o]


def is_allowed_origin(origin: str, request=None) -> bool:
    """Whether `origin` may be given a CORS response or used as an OAuth redirect target."""
    if not origin:
        return False
    normalized = origin_of(origin)
    if not normalized:
        return False
    if is_trusted_host(urlparse(normalized).hostname):
        return True
    # This backend's own origin, so a HUD served by Freja itself always works.
    if request is not None and normalized == origin_of(str(request.base_url)):
        return True
    return normalized in configured_origins()
