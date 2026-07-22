"""Codex tool aliases: executors that live in backend.services.codex_service, registered
here via registry.add(...) since there is no local `def` to decorate."""

from backend.services.codex_service import (
    execute_codex_code_impl,
    codex_git_ops_impl,
    codex_audit_codebase_impl,
    codex_run_and_fix_impl,
)
from ._registry import registry

# ---------------------------------------------------------------------------
# 3. IMPORTED / ALIASED EXECUTORS
#
# These executors live in other modules (codex_service) or are deliberate aliases that
# Gemini also reaches for. They are registered explicitly since there is no local `def`
# to decorate. Aliases (`run_code`, `tool_analyze_code`) share an implementation but keep
# their own description and permission key.
# ---------------------------------------------------------------------------
_CODEX_CODE_PARAMS = {
    "type": "OBJECT",
    "properties": {
        "language": {
            "type": "STRING",
            "description": "The language to run: 'python' or 'shell'.",
            "enum": ["python", "shell"]
        },
        "code": {
            "type": "STRING",
            "description": "The code or command to execute."
        }
    },
    "required": ["language", "code"]
}

registry.add(
    name="execute_codex_code",
    description="Runs Python code or shell commands locally on the host machine. Used to run scripts, tests or system administration tasks.",
    executor=execute_codex_code_impl,
    permission_key="freja_tool_execute_codex_code_allowed",
    parameters=_CODEX_CODE_PARAMS,
)
registry.add(
    name="run_code",
    description="Alias for execute_codex_code. Runs Python code or shell commands locally.",
    executor=execute_codex_code_impl,
    permission_key="freja_tool_run_code_allowed",
    parameters=_CODEX_CODE_PARAMS,
)
registry.add(
    name="codex_git_ops",
    description="Performs git operations in the local source directory (e.g. status, log, diff, branch, pull, commit, push, checkout).",
    executor=codex_git_ops_impl,
    permission_key="freja_tool_codex_git_ops_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "The git action: 'status', 'log', 'diff', 'branch', 'pull', 'push', 'checkout' (existing local branches only), 'clone' (https only, clones to separate workspace) or 'commit'.",
                "enum": ["status", "log", "diff", "branch", "pull", "push", "checkout", "clone", "commit"]
            },
            "argument": {
                "type": "STRING",
                "description": "Argument for the action (e.g. branch name, commit message or https repository URL)."
            }
        },
        "required": ["action"]
    },
)
registry.add(
    name="codex_audit_codebase",
    description="Performs a self-analysis (audit) of the source code to identify bugs, performance problems and code improvements, and saves a detailed report.",
    executor=codex_audit_codebase_impl,
    permission_key="freja_tool_codex_audit_codebase_allowed",
    parameters={"type": "OBJECT", "properties": {}},
)
registry.add(
    name="tool_analyze_code",
    description="Alias for codex_audit_codebase. Performs a self-analysis (audit) of the source code.",
    executor=codex_audit_codebase_impl,
    permission_key="freja_tool_tool_analyze_code_allowed",
    parameters={"type": "OBJECT", "properties": {}},
)
registry.add(
    name="codex_run_and_fix",
    description="Runs a command and automatically tries to repair the source code in the given file if the command/test fails.",
    executor=codex_run_and_fix_impl,
    permission_key="freja_tool_codex_run_and_fix_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "command": {
                "type": "STRING",
                "description": "The test command to run, e.g. 'pytest tests/test_file.py'. For security reasons, direct python/shell interpreter invocations (python, python3, py) are blocked in this channel - use a test runner such as pytest."
            },
            "file_path": {
                "type": "STRING",
                "description": "Relative path to the file to auto-repair on failure, e.g. 'backend/routes/sync.py'."
            },
            "max_retries": {
                "type": "INTEGER",
                "description": "Maximum number of auto-repair attempts (default 3)."
            }
        },
        "required": ["command", "file_path"]
    },
)

