"""Authentication middleware for protecting Freja's API endpoints."""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from backend.database import get_db_connection

# OAuth provider redirect endpoints are hit directly by the browser navigating away
# from the provider (Google/Strava), so no custom X-Freja-Token header can be attached.
# These are safe to exempt: they only accept a one-time authorization `code` and exchange
# it server-side; they don't expose or mutate secrets by themselves.
AUTH_EXEMPT_PATHS = {"/api/strava/callback", "/api/google_calendar/callback"}

class FrejaAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. Bypass auth for non-API endpoints (static HTML/JS/CSS assets) and OAuth redirects.
        if not path.startswith("/api/") or path in AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # 2. Check X-Freja-Token header against the token stored in SQLite.
        token = request.headers.get("X-Freja-Token")

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_access_token'")
                row = cursor.fetchone()
            expected_token = row[0].strip() if row and row[0] else None
        except Exception:
            # Fail closed if the DB is temporarily locked/busy during initialization/migration.
            expected_token = None

        if not expected_token or not token or token.strip() != expected_token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: Invalid or missing Freja Access Token."}
            )

        return await call_next(request)
