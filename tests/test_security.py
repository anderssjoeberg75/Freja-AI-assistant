import pytest
from backend.services.codex_service import verify_safe_python_code, verify_safe_shell_command

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
        "git --version",
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
