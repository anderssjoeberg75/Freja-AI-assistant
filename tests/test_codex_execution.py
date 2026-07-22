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
        assert "Security error" in str(excinfo.value)


def test_python_import_from_blocked_submodule_name():
    # `from <non-blocked-pkg> import os`-style: the ImportFrom check only screened the
    # imported name against blocked_calls, never against blocked_modules, so a blocked
    # module name imported *as a submodule of something else* slipped past unchecked.
    # verify_safe_python_code only parses the AST - it never actually imports anything -
    # so a syntactically valid but semantically bogus source module is enough to isolate
    # the check being tested here from the earlier `node.module in blocked_modules` one.
    unsafe_codes = [
        "from some_package import os",
        "from another_package import subprocess",
    ]
    for code in unsafe_codes:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_python_code(code)
        assert "Security error" in str(excinfo.value)


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

    result = {}
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
        for path in (target, result.get("backup_file", "")):
            if path and os.path.exists(path):
                os.remove(path)


def test_run_and_fix_rejects_js_and_html_targets():
    # .js/.html are content a browser actually loads/executes, and unlike .py/.sh there is
    # no re-validation step for them before the AI's rewrite is written to disk - so they
    # were dropped from ALLOWED_FIX_EXTENSIONS rather than left as an unguarded write path.
    for rel_path, content in (
        ("backend/cache/run_and_fix_target.js", "console.log('x');\n"),
        ("backend/cache/run_and_fix_target.html", "<p>x</p>\n"),
    ):
        abs_path = os.path.join(codex_service.PROJECT_ROOT, *rel_path.split("/"))
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            result = asyncio.run(codex_run_and_fix_impl({
                "command": "pytest --this-flag-does-not-exist-xyz",
                "file_path": rel_path,
                "max_retries": 1,
            }))
            assert "error" in result
            assert "not supported" in result["error"]
        finally:
            if os.path.exists(abs_path):
                os.remove(abs_path)


def test_run_and_fix_backup_path_is_unique_per_call(monkeypatch):
    # Two runs against the same file must not share a backup path - a fixed
    # `<file>.codex_backup` name meant a second concurrent/sequential run could clobber
    # the first run's backup before it was ever restored from.
    target = os.path.join(codex_service.PROJECT_ROOT, "backend", "cache", "run_and_fix_target.py")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write("print('original')\n")

    async def fake_gemini(prompt, system_instruction=""):
        return ""

    monkeypatch.setattr(codex_service, "call_gemini_api", fake_gemini)

    backups = []
    try:
        for _ in range(2):
            result = asyncio.run(codex_run_and_fix_impl({
                "command": "pytest --this-flag-does-not-exist-xyz",
                "file_path": "backend/cache/run_and_fix_target.py",
                "max_retries": 1,
            }))
            backups.append(result["backup_file"])
        assert backups[0] != backups[1]
        assert all(os.path.exists(b) for b in backups)
    finally:
        for path in [target] + backups:
            if path and os.path.exists(path):
                os.remove(path)


def test_codex_audit_codebase_impl(monkeypatch, tmp_path):
    async def fake_gemini(prompt, system_instruction=""):
        return f"Summary of findings\n{codex_service.AUDIT_REPORT_SEPARATOR}\nDetailed report here."

    monkeypatch.setattr(codex_service, "call_gemini_api", fake_gemini)
    res = asyncio.run(codex_service.codex_audit_codebase_impl({}))

    assert "summary" in res
    assert res["summary"] == "Summary of findings"
    assert "report_file" in res
    assert os.path.exists(res["report_file"])

