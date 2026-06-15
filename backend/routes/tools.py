"""FastAPI Router for Unified Tools Execution."""

from fastapi import APIRouter, HTTPException, Request
from backend.services.tool_registry import TOOL_DECLARATIONS, execute_tool

router = APIRouter()

@router.get("/api/tools/declarations")
async def get_tools_declarations():
    """Returns the list of Gemini-compatible tool function declarations."""
    return TOOL_DECLARATIONS

@router.post("/api/tools/execute")
async def post_execute_tool(request: Request):
    """Executes a tool on the backend and returns the JSON result."""
    try:
        body = await request.json()
        name = body.get("name")
        args = body.get("args", {})
        
        if not name:
            raise HTTPException(status_code=400, detail="Verktygsnamn saknas (name).")
            
        result = await execute_tool(name, args)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
