import asyncio
import time
import pytest
from backend.services.codex_service import (
    verify_safe_python_code,
    verify_safe_shell_command,
    verify_safe_git_clone_url,
    resolve_within_project,
    redact_secrets,
    codex_git_ops_impl,
    codex_run_and_fix_impl,
    PROJECT_ROOT,
)
import backend.routes.tools as tools_mod

def test_verify_safe_python_code_valid():
    # Valid and safe Python code should pass
    safe_code = """
def add(a, b):
    return a + b
result = add(5, 10)
print(result)
"""
    verify_safe_python_code(safe_code)

def test_verify_safe_python_code_invalid_imports():
    # Blocked imports should raise ValueError
    unsafe_codes = [
        "import os\nos.system('ls')",
        "from sys import exit\nexit(1)",
        "import subprocess",
        "import urllib.request"
    ]
    for code in unsafe_codes:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_python_code(code)
        assert "Säkerhetsfel: Import" in str(excinfo.value)

def test_verify_safe_python_code_invalid_calls():
    # Blocked function calls should raise ValueError
    unsafe_codes = [
        "eval('2 + 2')",
        "exec('print(1)')",
        "open('secrets.txt', 'r')",
        "compile('x = 5', '', 'exec')"
    ]
    for code in unsafe_codes:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_python_code(code)
        assert "Säkerhetsfel: Anrop" in str(excinfo.value)

def test_verify_safe_shell_command_valid():
    # Safe commands should pass
    safe_cmds = [
        "ls -la",
        "echo 'Hello World'",
        "pytest -v tests"
    ]
    for cmd in safe_cmds:
        verify_safe_shell_command(cmd)

def test_verify_safe_shell_command_invalid():
    # Dangerous commands should raise ValueError
    unsafe_cmds = [
        "rm -rf /",
        "mv file.txt /tmp",
        "curl http://malicious.com",
        "wget http://malicious.com",
        "sudo apt-get install git",
        "chmod +x script.sh",
        "cat secrets.txt > output.txt",
        "ls && rm -f file",
        "/usr/bin/curl http://malicious.com",
        "c''url http://malicious.com",
        "python3 -c 'import os'",
        "echo $(whoami)",
        "cat < input.txt"
    ]
    for cmd in unsafe_cmds:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_shell_command(cmd)
        assert "Säkerhetsfel" in str(excinfo.value)

def test_verify_safe_shell_command_invalid_extended_blocklist():
    # Previously-missed bypasses: versioned interpreters and living-off-the-land binaries
    unsafe_cmds = [
        "python3.11 -c 'import os'",
        "py -c 'print(1)'",
        "cp /etc/passwd ./leaked.txt",
        "find . -exec cat {} \\;",
        "docker run --rm -v /:/mnt alpine",
        "powershell -c Get-Process",
        "env",
        "printenv TELEGRAM_BOT_TOKEN",
    ]
    for cmd in unsafe_cmds:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_shell_command(cmd)
        assert "Säkerhetsfel" in str(excinfo.value)

def test_verify_safe_python_code_invalid_dunder_and_bypasses():
    # Attempted AST sandbox bypasses using reflection or dunder attributes
    unsafe_codes = [
        "().__class__.__subclasses__()",
        "__import__('os').system('ls')",
        "getattr(sys, 'exit')(0)",
        "import importlib",
        "import builtins",
        "x = '__class__'",
        "y = 'eval'"
    ]
    for code in unsafe_codes:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_python_code(code)
        assert "Säkerhetsfel" in str(excinfo.value)

def test_verify_safe_shell_command_blocks_git_entirely():
    # `git` is fully blocked in the free-form shell channel: `-c core.pager=<cmd> -p` is a
    # documented GTFOBins technique that executes an arbitrary program via git itself,
    # bypassing every other token in the blocklist. Git access must go through codex_git_ops.
    unsafe_cmds = [
        "git --version",
        "git -c core.pager=id -p log",
        "git -c core.editor=id commit",
    ]
    for cmd in unsafe_cmds:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_shell_command(cmd)
        assert "Säkerhetsfel" in str(excinfo.value)

def test_verify_safe_git_clone_url_valid():
    # Standard network transports should pass
    safe_urls = [
        "https://github.com/example/repo.git",
        "http://example.com/repo.git",
        "git://example.com/repo.git",
        "ssh://git@example.com/repo.git",
    ]
    for url in safe_urls:
        verify_safe_git_clone_url(url)

def test_verify_safe_git_clone_url_blocks_remote_helpers():
    # `ext::`/`fd::` remote helpers execute an arbitrary program as part of `git clone`
    unsafe_urls = [
        "ext::sh -c 'touch pwned'",
        "fd::0",
        "file:///etc/passwd",
        "EXT::sh -c 'id'",
    ]
    for url in unsafe_urls:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_git_clone_url(url)
        assert "Säkerhetsfel" in str(excinfo.value)

def test_resolve_within_project_valid():
    resolved = resolve_within_project("tests/test_security.py")
    assert resolved.startswith(str(PROJECT_ROOT))

def test_resolve_within_project_blocks_traversal():
    unsafe_paths = [
        "../../etc/passwd",
        "..\\..\\Windows\\System32\\drivers\\etc\\hosts",
    ]
    for path in unsafe_paths:
        with pytest.raises(ValueError) as excinfo:
            resolve_within_project(path)
        assert "Säkerhetsfel" in str(excinfo.value)

def test_resolve_within_project_blocks_absolute_path_outside_root():
    with pytest.raises(ValueError):
        resolve_within_project("C:\\Windows\\System32\\drivers\\etc\\hosts")


# --- Windows cmd.exe hardening -------------------------------------------------

def test_verify_safe_shell_command_blocks_windows_builtins():
    # cmd.exe destructive / reconfiguration builtins missed by the POSIX blocklist.
    unsafe_cmds = [
        "del /q /s file", "erase x", "move a b", "copy a b", "format c",
        "rd /s /q dir", "rmdir dir", "ren a b", "attrib +h x", "shutdown /s",
        "net user", "schtasks /create /tn t", "reg add HKLM", "takeown /f x",
        "icacls x /grant everyone:F", "sc stop service", "bcdedit /set",
    ]
    for cmd in unsafe_cmds:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_shell_command(cmd)
        assert "Säkerhetsfel" in str(excinfo.value)


def test_verify_safe_shell_command_blocks_env_expansion_and_absolute_paths():
    unsafe_cmds = [
        "echo %PATH%",                 # %VAR% expansion
        "somecmd %USERPROFILE%\\x",    # %VAR% expansion
        "type C:\\Windows\\x",         # drive-letter absolute path (also blocked binary)
        "cat /etc/passwd",             # POSIX root (also blocked binary)
        "ls ~/",                       # home expansion
        "dir \\\\server\\share",       # UNC path
    ]
    for cmd in unsafe_cmds:
        with pytest.raises(ValueError) as excinfo:
            verify_safe_shell_command(cmd)
        assert "Säkerhetsfel" in str(excinfo.value)


def test_verify_safe_shell_command_still_allows_relative_test_commands():
    # Regression: the new %/absolute-path guards must not block legitimate relative usage.
    for cmd in ["pytest tests/test_security.py", "pytest -v tests", "ls -la", "echo hello"]:
        verify_safe_shell_command(cmd)


def test_verify_safe_python_code_allows_benign_double_underscore():
    # Refined dunder check: a benign double-underscore identifier is no longer a false positive.
    verify_safe_python_code("x = 'my__var'\nprint(x)")


# --- Secret redaction for the codebase audit -----------------------------------

def test_redact_secrets_masks_values():
    text = 'api_key = "sk-abcdef123456"\ntoken: "ghp_verysecrettoken"\npassword=hunter2secret'
    out = redact_secrets(text)
    assert "sk-abcdef123456" not in out
    assert "ghp_verysecrettoken" not in out
    assert "hunter2secret" not in out
    assert "REDACTED" in out


# --- Tool execution authorization (server-side) --------------------------------

def test_is_git_push_detection():
    assert tools_mod._is_git_push("codex_git_ops", {"action": "push"}) is True
    assert tools_mod._is_git_push("codex_git_ops", {"action": "PUSH"}) is True
    assert tools_mod._is_git_push("codex_git_ops", {"action": "status"}) is False
    assert tools_mod._is_git_push("execute_codex_code", {"action": "push"}) is False


def test_git_push_requires_namespaced_one_time_grant():
    tools_mod.ONE_TIME_GRANTS.clear()
    # No grant -> not authorized.
    assert tools_mod.is_tool_execution_authorized("codex_git_ops", {"action": "push"}) is False

    # A grant for the non-push namespace must NOT authorize a push.
    tools_mod.ONE_TIME_GRANTS["codex_git_ops"] = time.time() + 60
    assert tools_mod.is_tool_execution_authorized("codex_git_ops", {"action": "push"}) is False

    # A push-namespaced grant authorizes exactly once, then is consumed.
    push_key = tools_mod._grant_key("codex_git_ops", {"action": "push"})
    tools_mod.ONE_TIME_GRANTS[push_key] = time.time() + 60
    assert tools_mod.is_tool_execution_authorized("codex_git_ops", {"action": "push"}) is True
    assert tools_mod.is_tool_execution_authorized("codex_git_ops", {"action": "push"}) is False
    tools_mod.ONE_TIME_GRANTS.clear()


# --- codex_git_ops argument / transport injection ------------------------------

def test_codex_git_ops_blocks_option_injection():
    res = asyncio.run(codex_git_ops_impl({"action": "checkout", "argument": "--upload-pack=touch pwned"}))
    assert "error" in res and "Säkerhetsfel" in res["error"]


def test_codex_git_ops_blocks_remote_helper_clone():
    res = asyncio.run(codex_git_ops_impl({"action": "clone", "argument": "ext::sh -c id"}))
    assert "error" in res and "Säkerhetsfel" in res["error"]


# --- codex_run_and_fix guards --------------------------------------------------

def test_run_and_fix_rejects_unsafe_command():
    res = asyncio.run(codex_run_and_fix_impl({"command": "rm -rf /", "file_path": "server.py"}))
    assert "error" in res and "Säkerhetsfel" in res["error"]


def test_run_and_fix_rejects_path_outside_project():
    res = asyncio.run(codex_run_and_fix_impl({"command": "pytest", "file_path": "../../etc/passwd"}))
    assert "error" in res and "Säkerhetsfel" in res["error"]


def test_run_and_fix_rejects_disallowed_file_extension():
    # .gitignore exists at the project root and has no allowlisted extension.
    res = asyncio.run(codex_run_and_fix_impl({"command": "pytest", "file_path": ".gitignore"}))
    assert "error" in res and "Säkerhetsfel" in res["error"]
