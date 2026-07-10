import os
import pytest
from fastapi.testclient import TestClient
from server import app
from backend.config import PROJECT_ROOT
from backend.services.tool_registry import execute_tool
from backend.database import get_api_key

@pytest.fixture
def db_token():
    return get_api_key('freja_access_token') or "freja1234"

@pytest.mark.anyio
async def test_read_project_file_valid():
    # Read README.md which should exist and be allowlisted
    result = await execute_tool("read_project_file", {"file_path": "README.md"})
    assert "error" not in result
    assert result["file_path"] == "README.md"
    assert "# 🌌 F.R.E.J.A." in result["content"]

@pytest.mark.anyio
async def test_read_project_file_blocked_sensitive():
    # Attempting to read keys.db or .env should be blocked by security checks
    for blocked in ["keys.db", ".env", ".freja_secret.key"]:
        result = await execute_tool("read_project_file", {"file_path": blocked})
        assert "error" in result
        assert "Security error" in result["error"]

@pytest.mark.anyio
async def test_read_project_file_blocked_traversal():
    # Attempting directory traversal should be blocked
    result = await execute_tool("read_project_file", {"file_path": "../keys.db"})
    assert "error" in result
    assert "Security error" in result["error"]

def test_serve_doc_report_auth_required():
    client = TestClient(app)
    # Accessing /api/docs/ without a token should trigger 401
    response = client.get("/api/docs/code_audit_test.md")
    assert response.status_code == 401

def test_serve_doc_report_valid(db_token):
    client = TestClient(app)
    docs_dir = os.path.join(PROJECT_ROOT, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    temp_report = os.path.join(docs_dir, "code_audit_pytest_temp.md")
    
    try:
        with open(temp_report, "w", encoding="utf-8") as f:
            f.write("# Pytest Report\nThis is a temporary audit file.")
            
        # Access the endpoint with a valid token
        headers = {"X-Freja-Token": db_token}
        response = client.get("/api/docs/code_audit_pytest_temp.md", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "# Pytest Report" in response.text
    finally:
        if os.path.exists(temp_report):
            os.remove(temp_report)

def test_serve_doc_report_not_found(db_token):
    client = TestClient(app)
    headers = {"X-Freja-Token": db_token}
    # Accessing non-existent report should return 404
    response = client.get("/api/docs/code_audit_non_existent.md", headers=headers)
    assert response.status_code == 404

def test_serve_doc_report_blocked_traversal(db_token):
    client = TestClient(app)
    headers = {"X-Freja-Token": db_token}
    # Traversal should trigger 400 Bad Request or 404 Not Found due to router resolution
    response = client.get("/api/docs/../keys.db", headers=headers)
    assert response.status_code in (400, 404)


def test_persistent_logging(db_token):
    from backend.routes.settings import add_system_log, LOG_FILE, SYSTEM_LOGS
    import json
    
    # Backup original log buffer size/items if needed
    original_logs = list(SYSTEM_LOGS)
    
    try:
        # Clear log file and queue
        SYSTEM_LOGS.clear()
        if os.path.exists(LOG_FILE):
            os.remove(LOG_FILE)
            
        # Log a test message
        add_system_log("TEST_INFO", "Pytest persistent log message")
        
        # Verify it was added to queue
        assert len(SYSTEM_LOGS) == 1
        assert SYSTEM_LOGS[0]["message"] == "Pytest persistent log message"
        
        # Verify it was written to file
        assert os.path.exists(LOG_FILE)
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["level"] == "TEST_INFO"
            assert data["message"] == "Pytest persistent log message"
            
        # Verify API logs GET endpoint returns it
        client = TestClient(app)
        headers = {"X-Freja-Token": db_token}
        response = client.get("/api/system/logs", headers=headers)
        assert response.status_code == 200
        logs = response.json()["logs"]
        assert len(logs) >= 1
        assert logs[-1]["message"] == "Pytest persistent log message"
        
        # Verify API logs DELETE endpoint clears both queue and file (recreating it only for the clearing confirmation log)
        response = client.delete("/api/system/logs", headers=headers)
        assert response.status_code == 200
        assert os.path.exists(LOG_FILE)
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["message"] == "Logghistorik rensad."
        assert len(SYSTEM_LOGS) == 1  # Contains "Logghistorik rensad."
        assert SYSTEM_LOGS[0]["message"] == "Logghistorik rensad."

    finally:
        # Restore original logs state
        SYSTEM_LOGS.clear()
        SYSTEM_LOGS.extend(original_logs)


@pytest.mark.anyio
async def test_run_windows_command_open_url_valid():
    # Valid HTTP URL
    result = await execute_tool("run_windows_command", {
        "action_type": "open_url",
        "target": "https://www.google.com"
    })
    if os.name == "nt":
        assert "error" not in result
        assert result["status"] == "success"
    else:
        assert "error" in result

@pytest.mark.anyio
async def test_run_windows_command_open_url_invalid():
    # Unsafe file:// URL
    result = await execute_tool("run_windows_command", {
        "action_type": "open_url",
        "target": "file:///C:/Windows/System32/cmd.exe"
    })
    assert "error" in result
    assert "Security error" in result["error"]

@pytest.mark.anyio
async def test_run_windows_command_open_folder_invalid():
    # Folder does not exist
    result = await execute_tool("run_windows_command", {
        "action_type": "open_folder",
        "target": "C:\\MappSomInteFinnsPytest123"
    })
    if os.name == "nt":
        assert "error" in result
        assert "was not found" in result["error"]

@pytest.mark.anyio
async def test_run_windows_command_run_cmd_benign():
    # Run echo command
    result = await execute_tool("run_windows_command", {
        "action_type": "run_cmd",
        "target": "echo Hello_Pytest"
    })
    if os.name == "nt":
        assert "error" not in result
        assert result["status"] == "success"
        assert "Hello_Pytest" in result["stdout"]

@pytest.mark.anyio
async def test_run_windows_command_run_cmd_blocked():
    # Blocked del/format keywords
    for blocked_cmd in ["del files.txt", "format C:", "rmdir /S /Q C:\\test"]:
        result = await execute_tool("run_windows_command", {
            "action_type": "run_cmd",
            "target": blocked_cmd
        })
        if os.name == "nt":
            assert "error" in result
            assert "Security error" in result["error"]


def test_client_heartbeat_flow(db_token):
    from backend.routes.settings import get_client_status
    
    client = TestClient(app)
    
    # 1. Initially it should be inactive (no heartbeat received in tests yet)
    status = get_client_status()
    assert status["active"] is False
    assert status["hostname"] is not None
    
    # 2. Trigger the heartbeat endpoint with authenticated token
    headers = {"X-Freja-Token": db_token, "User-Agent": "Pytest-Agent"}
    response = client.post("/api/client/heartbeat", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    
    # 3. Check client status again, it must be active now
    status = get_client_status()
    assert status["active"] is True
    assert status["client_info"] == "Pytest-Agent"
    assert status["hostname"] is not None




