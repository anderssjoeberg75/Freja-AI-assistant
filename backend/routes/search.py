"""Search API router using FastAPI."""

from fastapi import APIRouter, Query
from backend.services.search_service import perform_search

router = APIRouter()

@router.get("/api/search")
async def get_search(q: str = Query("", alias="q")):
    query = q.strip()
    if not query:
        return []
    return await perform_search(query)
