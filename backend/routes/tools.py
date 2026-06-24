import uuid
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from backend.services.tool_registry import TOOL_DECLARATIONS, execute_tool
from backend.services.facebook_service import cancel_facebook_download

router = APIRouter()

# Global dict to store status and results of background tasks
TOOL_TASKS = {}

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

@router.post("/api/tools/cancel_download")
async def post_cancel_download():
    """Aborts/cancels any active Facebook photo downloading task."""
    cancel_facebook_download()
    return {"status": "cancelled"}
