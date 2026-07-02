"""Authentication middleware for protecting Freja's API endpoints."""

import logging
import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from backend.database import get_api_key

logger = logging.getLogger("freja.auth")

# OAuth provider redirect endpoints are hit directly by the browser navigating away
# from the provider (Google/Strava), so no custom X-Freja-Token header can be attached.
# These are safe to exempt: they only accept a one-time authorization `code` and exchange
# it server-side; they don't expose or mutate secrets by themselves.
AUTH_EXEMPT_PATHS = {"/api/strava/callback", "/api/google_calendar/callback"}

# In-memory sliding-window rate limiter for failed auth attempts, keyed by client IP.
# Freja runs as a single local/self-hosted process, so a non-persistent, non-distributed
# limiter is sufficient: it resets on restart, but stops a sustained brute-force attempt
# against the access token for as long as the server keeps running.
FAILED_ATTEMPT_WINDOW_SECONDS = 300
FAILED_ATTEMPT_THRESHOLD = 10
LOCKOUT_SECONDS = 300

_failed_attempts = defaultdict(list)  # ip -> [failure timestamps]
_locked_until = {}  # ip -> unix timestamp when the lockout lifts


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_locked_out(ip: str) -> bool:
    unlock_at = _locked_until.get(ip)
    if unlock_at is None:
        return False
    if time.time() >= unlock_at:
        _locked_until.pop(ip, None)
        _failed_attempts.pop(ip, None)
        return False
    return True


def _record_failure(ip: str, path: str):
    now = time.time()
    attempts = [t for t in _failed_attempts[ip] if now - t < FAILED_ATTEMPT_WINDOW_SECONDS]
    attempts.append(now)
    _failed_attempts[ip] = attempts
    logger.warning("Freja auth: rejected request from %s to %s (invalid or missing token)", ip, path)
    if len(attempts) >= FAILED_ATTEMPT_THRESHOLD:
        _locked_until[ip] = now + LOCKOUT_SECONDS
        logger.warning(
            "Freja auth: locking out %s for %ss after %d failed attempts in the last %ss",
            ip, LOCKOUT_SECONDS, len(attempts), FAILED_ATTEMPT_WINDOW_SECONDS
        )


def _record_success(ip: str):
    _failed_attempts.pop(ip, None)
    _locked_until.pop(ip, None)


class FrejaAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. Bypass auth for non-API endpoints (static HTML/JS/CSS assets) and OAuth redirects.
        if not path.startswith("/api/") or path in AUTH_EXEMPT_PATHS:
            return await call_next(request)

        ip = _client_ip(request)

        # 2. Reject outright if this client has too many recent failed attempts.
        if _is_locked_out(ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many failed authentication attempts. Try again later."},
                headers={"Retry-After": str(LOCKOUT_SECONDS)}
            )

        # 3. Check X-Freja-Token header against the token stored in SQLite.
        token = request.headers.get("X-Freja-Token")

        try:
            expected_token = get_api_key('freja_access_token')
        except Exception:
            # Fail closed if the DB is temporarily locked/busy during initialization/migration.
            expected_token = None

        if not expected_token or not token or token.strip() != expected_token:
            _record_failure(ip, path)
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: Invalid or missing Freja Access Token."}
            )

        _record_success(ip)
        return await call_next(request)
