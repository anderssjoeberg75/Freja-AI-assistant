import time
import uuid
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from backend.services.tool_registry import TOOL_DECLARATIONS, TOOL_PERMISSION_KEYS, execute_tool
from backend.services.facebook_service import cancel_facebook_download
from backend.database import get_api_key

router = APIRouter()

# Global dict to store status and results of background tasks
TOOL_TASKS = {}

# Finished task entries older than this are pruned so TOOL_TASKS can't grow
# without bound over a long-running server session.
TOOL_TASK_MAX_AGE_SECONDS = 3600


def prune_old_tool_tasks():
    """Drops finished task entries older than TOOL_TASK_MAX_AGE_SECONDS."""
    cutoff = time.time() - TOOL_TASK_MAX_AGE_SECONDS
    for task_id in list(TOOL_TASKS.keys()):
        task = TOOL_TASKS.get(task_id) or {}
        if task.get("status") != "processing" and task.get("created", 0) < cutoff:
            del TOOL_TASKS[task_id]

# Short-lived, single-use "allow this call only" grants issued by the frontend's
# permission-gateway prompt (the "allow once" button). Keeps the authoritative
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
        value = get_api_key(permission_key)
        return bool(value and value.strip().lower() == "true")
    except Exception:
        return False


def consume_one_time_grant(grant_key: str) -> bool:
    """Checks for and consumes a valid, unexpired one-time execution grant for this key."""
    grant_expiry = ONE_TIME_GRANTS.get(grant_key)
    if grant_expiry is None:
        return False
    del ONE_TIME_GRANTS[grant_key]
    return time.time() <= grant_expiry


def _is_git_push(name: str, args: dict) -> bool:
    """`git push` publishes to a remote and is hard to reverse, unlike the other
    codex_git_ops actions (status/log/clone/checkout/commit stay local). It must always
    be re-confirmed per call, even when the user has permanently allowed codex_git_ops."""
    return name == "codex_git_ops" and (args or {}).get("action", "").strip().lower() == "push"


def _grant_key(name: str, args: dict) -> str:
    """Derives the ONE_TIME_GRANTS key for a tool call. Git push gets its own namespaced
    key so a one-time grant issued for a harmless action (e.g. `git log`) can't be reused
    to authorize a push, and vice versa."""
    if _is_git_push(name, args):
        return f"{name}:push"
    return name


def is_tool_execution_authorized(name: str, args: dict = None) -> bool:
    """Server-side authority for whether a tool call may run right now."""
    args = args or {}
    grant_key = _grant_key(name, args)
    if _is_git_push(name, args):
        # Never satisfied by the permanent allow-list; always needs a fresh one-time grant.
        return consume_one_time_grant(grant_key)
    if is_tool_permanently_allowed(name):
        return True
    return consume_one_time_grant(grant_key)

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
            "result": result,
            "created": (TOOL_TASKS.get(task_id) or {}).get("created", time.time())
        }
        if status == "cancelled":
            print(f"[Tool Background] Task {task_id} ('{name}') was cancelled.")
        else:
            print(f"[Tool Background] Task {task_id} ('{name}') completed successfully.")
    except Exception as e:
        TOOL_TASKS[task_id] = {
            "status": "failed",
            "progress": 100,
            "error": str(e),
            "created": (TOOL_TASKS.get(task_id) or {}).get("created", time.time())
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
            raise HTTPException(status_code=400, detail="The tool name (name) is missing.")

        if not is_tool_execution_authorized(name, args):
            raise HTTPException(
                status_code=403,
                detail=f"Permission is missing for the tool '{name}'. Approve it in Settings or via the permission prompt."
            )

        prune_old_tool_tasks()

        task_id = str(uuid.uuid4())
        TOOL_TASKS[task_id] = {
            "status": "processing",
            "progress": 0,
            "result": None,
            "created": time.time()
        }
        
        # Enqueue the background task
        background_tasks.add_task(run_tool_background, task_id, name, args)

        return {"task_id": task_id, "status": "processing"}
    except HTTPException:
        # Preserve intentional 400/403 responses; without this the generic handler
        # below would re-wrap them as 500 and the frontend's permission prompt
        # (which keys off a 403) would never trigger.
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/tools/status/{task_id}")
async def get_tool_status(task_id: str):
    """Returns the status and result/error of a background task."""
    task = TOOL_TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task

@router.post("/api/tools/grant_once")
async def post_grant_once(request: Request):
    """Issues a short-lived, single-use execution grant for one tool call.

    Called by the frontend's permission-gateway modal when the user clicks
    "allow once", so the following /api/tools/execute
    call is authorized server-side without persisting a permanent allow-list entry.
    """
    body = await request.json()
    name = body.get("name")
    args = body.get("args", {})
    if not name:
        raise HTTPException(status_code=400, detail="The tool name (name) is missing.")
    grant_key = _grant_key(name, args)
    ONE_TIME_GRANTS[grant_key] = time.time() + ONE_TIME_GRANT_TTL_SECONDS
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
