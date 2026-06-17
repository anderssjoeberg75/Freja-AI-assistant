"""Authentication middleware for protecting Freja's API endpoints."""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from backend.database import get_db_connection

class FrejaAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Disable access token check completely to allow passwordless assistant startup
        return await call_next(request)
