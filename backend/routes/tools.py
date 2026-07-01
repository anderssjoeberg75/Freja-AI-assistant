import time
import uuid
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from backend.services.tool_registry import TOOL_DECLARATIONS, TOOL_PERMISSION_KEYS, execute_tool
from backend.services.facebook_service import cancel_facebook_download
from backend.database import get_db_connection

router = APIRouter()

# Global dict to store status and results of background tasks
TOOL_TASKS = {}

# Short-lived, single-use "allow this call only" grants issued by the frontend's
# permission-gateway prompt (the "Tillåt denna gång" button). Keeps the authoritative
# permission check server-side instead of trusting a client-supplied flag, while still
# allowing one-off approvals that aren't persisted to the permanent allow-list.
ONE_TIME_GRANTS = {}
ONE_TIME_GRANT_TTL_SECONDS = 120


def is_tool_permanently_allowed(name: str) -> bool:
    """Checks the persisted (server-side) permission flag for a tool, set via Settings."""
    permission_key = TOOL_PERMISSION_KEYS.get(name)
    if not permission_key:
        # Tool has no permission gate defined; nothing to enforce.
        return True
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key_value FROM api_keys WHERE key_name = ?", (permission_key,))
            row = cursor.fetchone()
        return bool(row and row[0] and row[0].strip().lower() == "true")
    except Exception:
        return False


def consume_one_time_grant(name: str) -> bool:
    """Checks for and consumes a valid, unexpired one-time execution grant for this tool."""
    grant_expiry = ONE_TIME_GRANTS.get(name)
    if grant_expiry is None:
        return False
    del ONE_TIME_GRANTS[name]
    return time.time() <= grant_expiry


def is_tool_execution_authorized(name: str) -> bool:
    """Server-side authority for whether a tool call may run right now."""
    if is_tool_permanently_allowed(name):
        return True
    return consume_one_time_grant(name)

async def run_tool_background(task_id: str, name: str, args: dict):
    try:
        print(f"[Tool Background] Starting task {task_id} for tool '{name}'...")
        
        def progress_callback(current: int, total: int, stage: str):
            percent = int((current / total) * 100) if total > 0 else 0
            if task_id in TOOL_TASKS:
                TOOL_TASKS[task_id].update({
                    "progress": percent,
                    "current": current,
                    "total": total,
                    "stage": stage
                })

        result = await execute_tool(name, args, progress_callback=progress_callback)
        
        status = "success"
        if isinstance(result, dict) and result.get("status") == "cancelled":
            status = "cancelled"
            
        TOOL_TASKS[task_id] = {
            "status": status,
            "progress": 100,
            "result": result
        }
        if status == "cancelled":
            print(f"[Tool Background] Task {task_id} ('{name}') was cancelled.")
        else:
            print(f"[Tool Background] Task {task_id} ('{name}') completed successfully.")
    except Exception as e:
        TOOL_TASKS[task_id] = {
            "status": "failed",
            "progress": 100,
            "error": str(e)
        }
        print(f"[Tool Background] Task {task_id} ('{name}') failed: {e}")

@router.get("/api/tools/declarations")
async def get_tools_declarations():
    """Returns the list of Gemini-compatible tool function declarations."""
    return TOOL_DECLARATIONS

@router.post("/api/tools/execute")
async def post_execute_tool(request: Request, background_tasks: BackgroundTasks):
    """Starts a tool in the background and returns a task_id immediately."""
    try:
        body = await request.json()
        name = body.get("name")
        args = body.get("args", {})
        
        if not name:
            raise HTTPException(status_code=400, detail="Verktygsnamn saknas (name).")

        if not is_tool_execution_authorized(name):
            raise HTTPException(
                status_code=403,
                detail=f"Behörighet saknas för verktyget '{name}'. Godkänn det i Inställningar eller via behörighetsförfrågan."
            )

        task_id = str(uuid.uuid4())
        TOOL_TASKS[task_id] = {
            "status": "processing",
            "progress": 0,
            "result": None
        }
        
        # Enqueue the background task
        background_tasks.add_task(run_tool_background, task_id, name, args)
        
        return {"task_id": task_id, "status": "processing"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/tools/status/{task_id}")
async def get_tool_status(task_id: str):
    """Returns the status and result/error of a background task."""
    task = TOOL_TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Uppgiften hittades inte (Task not found).")
    return task

@router.post("/api/tools/grant_once")
async def post_grant_once(request: Request):
    """Issues a short-lived, single-use execution grant for one tool call.

    Called by the frontend's permission-gateway modal when the user clicks
    "Tillåt denna gång" (allow once), so the following /api/tools/execute
    call is authorized server-side without persisting a permanent allow-list entry.
    """
    body = await request.json()
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Verktygsnamn saknas (name).")
    ONE_TIME_GRANTS[name] = time.time() + ONE_TIME_GRANT_TTL_SECONDS
    return {"status": "granted", "name": name, "expires_in": ONE_TIME_GRANT_TTL_SECONDS}

@router.post("/api/tools/cancel_download")
async def post_cancel_download():
    """Aborts/cancels any active Facebook photo downloading task or learning task."""
    cancel_facebook_download()
    try:
        from backend.services.learning_service import cancel_learning
        cancel_learning()
    except Exception as e:
        print(f"[Tools Route] Failed to call cancel_learning: {e}")
    return {"status": "cancelled"}
