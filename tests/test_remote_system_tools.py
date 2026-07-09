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
        assert "Säkerhetsfel" in result["error"]

@pytest.mark.anyio
async def test_read_project_file_blocked_traversal():
    # Attempting directory traversal should be blocked
    result = await execute_tool("read_project_file", {"file_path": "../keys.db"})
    assert "error" in result
    assert "Säkerhetsfel" in result["error"]

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

