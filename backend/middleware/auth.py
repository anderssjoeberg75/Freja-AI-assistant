"""Authentication middleware for protecting Freja's API endpoints."""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from backend.database import get_db_connection

class FrejaAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        # 1. Bypass auth for non-API endpoints (e.g. static HTML/JS/CSS assets)
        # and the Strava callback redirect endpoint.
        if not path.startswith("/api/") or path == "/api/strava/callback":
            return await call_next(request)
            
        # 2. Check X-Freja-Token header
        token = request.headers.get("X-Freja-Token")
        
        # 3. Retrieve expected token from SQLite database
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_access_token'")
                row = cursor.fetchone()
            expected_token = row[0].strip() if row else "freja_secret"
        except Exception:
            # Fallback if DB is temporarily locked/busy during initialization/migration
            expected_token = "freja_secret"
            
        if not token or token.strip() != expected_token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: Invalid or missing Freja Access Token."}
            )
            
        return await call_next(request)
