"""Behavior tests for the codex execution sandbox, git push gating, and task pruning."""

import asyncio
import os
import time

import pytest

from backend.database import init_db, set_api_key
from backend.services import codex_service
from backend.services.codex_service import (
    SHELL_SANDBOX_DIR,
    execute_codex_code_impl,
    codex_run_and_fix_impl,
    run_subprocess_command,
    verify_safe_python_code,
)
from backend.routes import tools as tools_route

init_db()


def test_python_alias_import_bypasses_blocked():
    # Aliasing a dangerous builtin or importing a file/network-capable module
    # must be rejected even when the bound name looks innocent.
    unsafe_codes = [
        "from io import open as reader\nreader('x')",
        "from pathlib import Path\nPath('x').read_text()",
        "import pathlib",
        "import asyncio",
        "import multiprocessing",
        "import ftplib",
        "import pickle",
    ]
    for code in unsafe_codes:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_python_code(code)
        assert "Säkerhetsfel" in str(excinfo.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only shell assumptions")
def test_shell_commands_run_inside_sandbox_dir(monkeypatch):
    # Force local mode so the test doesn't depend on docker being installed.
    monkeypatch.setenv("FREJA_CODEX_SANDBOX", "local")
    result = asyncio.run(execute_codex_code_impl({"language": "shell", "code": "pwd"}))
    assert result["exit_code"] == 0
    assert result["sandbox"] == "local"
    assert os.path.basename(SHELL_SANDBOX_DIR) in result["stdout"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only shell assumptions")
def test_subprocess_timeout_kills_hung_command():
    result = asyncio.run(run_subprocess_command("sleep 5", timeout=1))
    assert result["exit_code"] == -1
    assert "tidsgränsen" in result["stderr"]


def test_git_push_never_satisfied_by_permanent_allowlist():
    # Even with codex_git_ops permanently allowed, a push must require a fresh
    # one-time grant; other actions pass on the permanent flag alone.
    set_api_key("freja_tool_codex_git_ops_allowed", "true")
    try:
        assert tools_route.is_tool_execution_authorized("codex_git_ops", {"action": "status"})
        assert not tools_route.is_tool_execution_authorized("codex_git_ops", {"action": "push"})

        tools_route.ONE_TIME_GRANTS["codex_git_ops:push"] = time.time() + 60
        assert tools_route.is_tool_execution_authorized("codex_git_ops", {"action": "push"})
        # The grant is single-use.
        assert not tools_route.is_tool_execution_authorized("codex_git_ops", {"action": "push"})
    finally:
        set_api_key("freja_tool_codex_git_ops_allowed", "false")


def test_prune_old_tool_tasks():
    tools_route.TOOL_TASKS.clear()
    tools_route.TOOL_TASKS["old_done"] = {"status": "success", "created": time.time() - 7200}
    tools_route.TOOL_TASKS["old_running"] = {"status": "processing", "created": time.time() - 7200}
    tools_route.TOOL_TASKS["fresh"] = {"status": "success", "created": time.time()}

    tools_route.prune_old_tool_tasks()

    assert "old_done" not in tools_route.TOOL_TASKS
    assert "old_running" in tools_route.TOOL_TASKS  # never prune in-flight tasks
    assert "fresh" in tools_route.TOOL_TASKS
    tools_route.TOOL_TASKS.clear()


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only shell assumptions")
def test_run_and_fix_rejects_empty_ai_response(monkeypatch, tmp_path):
    # A failing command plus an empty Gemini "fix" must leave the target file
    # untouched and keep a backup.
    target = os.path.join(codex_service.PROJECT_ROOT, "backend", "cache", "run_and_fix_target.py")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    original_content = "print('original content that must survive')\n"
    with open(target, "w", encoding="utf-8") as f:
        f.write(original_content)

    async def fake_gemini(prompt, system_instruction=""):
        return ""

    monkeypatch.setattr(codex_service, "call_gemini_api", fake_gemini)

    try:
        result = asyncio.run(codex_run_and_fix_impl({
            "command": "ls /nonexistent_dir_freja_test",
            "file_path": "backend/cache/run_and_fix_target.py",
            "max_retries": 1,
        }))

        assert result["status"] == "failed"
        assert os.path.exists(result["backup_file"])
        with open(target, encoding="utf-8") as f:
            assert f.read() == original_content
        assert any("förkastades" in entry for entry in result["history"])
    finally:
        for path in (target, target + ".codex_backup"):
            if os.path.exists(path):
                os.remove(path)
