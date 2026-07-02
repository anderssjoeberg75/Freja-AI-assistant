import pytest
from backend.services.codex_service import (
    verify_safe_python_code,
    verify_safe_shell_command,
    verify_safe_git_clone_url,
    resolve_within_project,
    PROJECT_ROOT,
)

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
