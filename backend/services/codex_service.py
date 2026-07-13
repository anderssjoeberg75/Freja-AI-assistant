"""Local Codex Developer Tools Service.

Provides sandbox-free code execution, Git operations, codebase self-auditing,
and autonomous debugging capabilities using the local host environment and Gemini.
"""

import os
import sys
import shutil
import subprocess
import datetime
import difflib
import asyncio
import re
import uuid
from backend.config import PROJECT_ROOT
from backend.database import get_db_connection
from backend.services import gemini_client

# Ignored patterns for codebase auditing. Directory names are matched against the
# basename of each directory (os.walk yields basenames), so entries must be plain
# names like 'cache', not paths like 'backend/cache'.
IGNORED_DIRS = {
    '.git', '__pycache__', 'venv', 'node_modules', 'cache',
    'freja_io_temp', '.garminconnect', '.pytest_cache', 'htmlcov',
    'docs', 'downloads',
}
ALLOWED_EXTENSIONS = {'.py', '.js', '.html', '.css', '.json', '.md', '.sh', '.txt'}
# facebook_state.json holds live Playwright session cookies/localStorage (see
# save_session.py / facebook_service.py) and must never be shipped to the external
# Gemini API as part of a codebase dump.
IGNORED_FILES = {
    'keys.db', 'freja.db', '.telegram_bot.lock', 'package-lock.json',
    'facebook_state.json',
}

# Files whose contents must never be shipped to the external Gemini API during an
# audit, matched case-insensitively as substrings of the filename. Defence in depth
# on top of IGNORED_FILES / the extension allowlist, since a secret can live in any
# allowed-extension file (e.g. a *.local.json or a token dump).
SENSITIVE_FILENAME_MARKERS = ('secret', 'token', 'credential', 'password', '.env', '.local.json', 'apikey', 'api_key')

# Patterns whose captured secret value is redacted before any file content leaves the
# host for the audit. Keeps key=... / "token": "..." style assignments from leaking.
SECRET_REDACTION_PATTERN = re.compile(
    r'(?i)(api[_-]?key|secret|password|passwd|refresh[_-]?token|access[_-]?token|client[_-]?secret|authorization|bearer|token)'
    r'(\s*[:=]\s*)'
    r'(["\']?)([^\s"\',;]{6,})(\3)'
)

# Only these file types may be overwritten by codex_run_and_fix. Prevents the auto-fixer
# from writing arbitrary model output to executables, binaries, or unexpected paths.
ALLOWED_FIX_EXTENSIONS = {'.py', '.js', '.html', '.css', '.json', '.md', '.txt', '.sh'}


def redact_secrets(text: str) -> str:
    """Masks obvious secret assignments (key=..., token: "...") in a text blob."""
    return SECRET_REDACTION_PATTERN.sub(r'\1\2\3***REDACTED***\5', text)


import ast

def verify_safe_python_code(code: str):
    """Parses the Python code into an AST and throws ValueError if suspicious operations are detected."""
    blocked_calls = {
        'eval', 'exec', 'open', 'compile', 'input', '__import__', 'getattr', 'setattr',
        'globals', 'locals', 'vars', 'classmethod', 'staticmethod', 'breakpoint'
    }
    blocked_modules = {
        'os', 'sys', 'subprocess', 'shutil', 'pty', 'platform', 'socket', 'urllib',
        'http', 'httpx', 'requests', 'sqlite3', 'ctypes', 'importlib', 'builtins',
        # File access routes around the blocked `open` builtin
        'pathlib', 'io', 'tempfile', 'glob', 'fileinput', 'zipfile', 'tarfile',
        'shelve', 'pickle', 'marshal', 'codecs', 'mmap',
        # Network routes around the blocked socket/http modules
        'asyncio', 'ftplib', 'smtplib', 'telnetlib', 'xmlrpc', 'aiohttp',
        'poplib', 'imaplib', 'nntplib',
        # Process/thread spawning and interpreter control
        'multiprocessing', 'threading', 'concurrent', 'signal', 'webbrowser',
        'runpy', 'code', 'codeop',
    }
    
    try:
        root = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"Syntax error in the Python code: {e}")
        
    for node in ast.walk(root):
        if isinstance(node, ast.Import):
            for name in node.names:
                mod = name.name.split('.')[0]
                if mod in blocked_modules:
                    raise ValueError(f"Security error: Importing the module '{name.name}' is blocked.")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split('.')[0]
                if mod in blocked_modules:
                    raise ValueError(f"Security error: Importing from the module '{node.module}' is blocked.")
            # Block `from <module> import open as reader`-style aliasing: the imported
            # object keeps its dangerous behavior even when bound to an innocent name.
            for name in node.names:
                if name.name in blocked_calls:
                    raise ValueError(f"Security error: Import of '{name.name}' is blocked.")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in blocked_calls:
                    raise ValueError(f"Security error: Calling the function '{node.func.id}' is blocked.")
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr in blocked_calls or node.func.attr.startswith('__'):
                    raise ValueError(f"Security error: Calling the attribute/method '{node.func.attr}' is blocked.")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith('__') or node.attr in blocked_calls:
                raise ValueError(f"Security error: Access to the dunder attribute '{node.attr}' is blocked.")
        elif isinstance(node, ast.Name):
            if node.id.startswith('__') or node.id in blocked_calls:
                raise ValueError(f"Security error: The variable/name '{node.id}' is blocked.")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                val = node.value.strip()
                if re.search(r'__\w+__', val) or '__import__' in val:
                    raise ValueError(f"Security error: The string contains a dunder pattern, which is blocked.")
                if val in blocked_modules or val in blocked_calls:
                    raise ValueError(f"Security error: The string contains the blocked word '{val}'.")

BLOCKED_SHELL_COMMANDS = {
    'rm', 'mv', 'cp', 'wget', 'curl', 'sudo', 'chmod', 'chown', 'dd', 'mkfs',
    'nc', 'netcat', 'ncat', 'socat', 'bash', 'sh', 'zsh', 'ssh', 'scp', 'sftp',
    'pip', 'pip3', 'perl', 'ruby', 'php', 'node', 'npx', 'lua',
    'powershell', 'pwsh', 'cmd', 'cmd.exe', 'cscript', 'wscript',
    'docker', 'kubectl', 'systemctl', 'service', 'kill', 'killall', 'taskkill',
    'reg', 'reg.exe', 'certutil', 'bitsadmin', 'rundll32', 'mshta', 'regsvr32',
    'env', 'printenv', 'set', 'export', 'source', 'eval', 'exec', 'alias',
    'find', 'awk', 'sed', 'xargs', 'tar', 'zip', 'unzip', '7z',
    # 'git' is deliberately blocked here: `-c core.pager=<cmd> -p` (and similar
    # core.editor / diff.external config overrides) is a documented GTFOBins
    # technique that executes an arbitrary program via git itself, bypassing every
    # other token in this blocklist. All git access must go through the dedicated,
    # argv-only codex_git_ops tool instead of raw shell.
    'git',
    # File-reading / exfiltration commands: none of these mutate anything, but
    # they can dump the contents of keys.db, .env, ssh keys, etc. straight to
    # stdout, which the blocklist above did not previously cover.
    'cat', 'tac', 'head', 'tail', 'more', 'less', 'nl', 'od', 'hexdump', 'xxd',
    'strings', 'base64', 'base32', 'grep', 'egrep', 'fgrep', 'sort', 'uniq',
    'cut', 'tr', 'wc', 'diff', 'cmp', 'file', 'stat', 'readlink', 'ln', 'tee',
    'type', 'more.com', 'findstr', 'rev', 'fold', 'comm', 'join', 'column',
    'expand', 'unexpand',
    # Windows cmd.exe builtins / utilities. The shell is cmd.exe on the Windows
    # deployment target, so the POSIX-centric names above (rm/mv/cp) are not enough:
    # these mutate, exfiltrate, or reconfigure the host and must be blocked too.
    'del', 'erase', 'rd', 'rmdir', 'md', 'mkdir', 'move', 'ren', 'rename',
    'copy', 'xcopy', 'robocopy', 'format', 'fc', 'comp', 'attrib', 'cacls',
    'icacls', 'takeown', 'net', 'net1', 'sc', 'shutdown', 'at', 'schtasks',
    'wmic', 'vssadmin', 'bcdedit', 'diskpart', 'cipher', 'mklink', 'subst',
    'assoc', 'ftype', 'setx', 'start', 'call', 'reg', 'runas', 'msg', 'clip',
    'fsutil', 'label', 'recover', 'replace',
}

# Matches python / python3 / python3.11 / py etc. so version-suffixed interpreter
# names can't slip past an exact-match blocklist.
BLOCKED_SHELL_PATTERN = re.compile(r'^(python[\d.]*|py|pip[\d.]*)$', re.IGNORECASE)

# Minimal environment passed to any sandboxed subprocess. Excludes application secrets
# that operators may have set as process env vars (e.g. TELEGRAM_BOT_TOKEN), so a shell
# command can't exfiltrate them via `env`/`printenv`.
SAFE_ENV_ALLOWLIST = (
    'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC',
    'TEMP', 'TMP', 'HOME', 'HOMEDRIVE', 'HOMEPATH',
    'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMDATA',
    'LANG', 'LC_ALL', 'PYTHONIOENCODING',
)

def build_sandbox_env(extra_env: dict = None) -> dict:
    """Builds a minimal subprocess environment, excluding secrets set as process env vars."""
    env = {k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV_ALLOWLIST}
    if extra_env:
        env.update(extra_env)
    return env

# Default hard timeout (seconds) for any sandboxed subprocess, to prevent a hung
# or intentionally-looping command from blocking the server indefinitely.
DEFAULT_SUBPROCESS_TIMEOUT = 120

# Working directory used for ad-hoc AI-authored shell commands (execute_codex_code_impl).
# Kept separate from PROJECT_ROOT so relative-path commands can't stumble onto keys.db,
# .env, or other project files just by being run from the repo root. Commands that
# legitimately need the real project tree (git ops, run_and_fix on a real test file)
# still run with cwd=PROJECT_ROOT and are validated separately.
SHELL_SANDBOX_DIR = os.path.join(PROJECT_ROOT, "backend", "cache", "shell_sandbox")


def _limit_subprocess_resources():
    """preexec_fn for POSIX subprocesses: caps memory, process count, and CPU time so a
    runaway or forkbomb-style command can't exhaust the host, independent of the
    asyncio-level wall-clock timeout."""
    import resource
    for limit, value in (
        (resource.RLIMIT_AS, 1536 * 1024 * 1024),  # ~1.5 GB address space
        (resource.RLIMIT_NPROC, 64),
        (resource.RLIMIT_CPU, 180),
    ):
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass


def _sandbox_subprocess_kwargs() -> dict:
    """Extra Popen kwargs to constrain a sandboxed subprocess. POSIX-only; a no-op on
    Windows since the `resource` module and preexec_fn forking model don't exist there."""
    if os.name == "nt":
        return {}
    return {"preexec_fn": _limit_subprocess_resources}


# Path fragments that must never appear in a sandboxed shell command, regardless of
# which (non-blocked) binary they're passed to: blocks both directory traversal and
# direct references to secret/credential files sitting next to the project root.
SENSITIVE_PATH_MARKERS = (
    'keys.db', 'freja.db', 'database.db', '.freja_secret.key', '.env',
    '.ssh', 'id_rsa', 'facebook_state.json',
)

# --- Container sandbox -------------------------------------------------------
# FREJA_CODEX_SANDBOX selects how AI-authored code is executed:
#   'docker' - always run inside a throwaway container (fails if docker is missing)
#   'local'  - run directly on the host (the pre-existing behavior; blocklist +
#              rlimits are then the only isolation, which is weaker - notably on
#              Windows where the `resource` module doesn't exist at all)
#   'auto'   - use docker when available, otherwise fall back to local (default)
CODEX_SANDBOX_MODE_ENV = "FREJA_CODEX_SANDBOX"
DOCKER_SANDBOX_IMAGE = os.environ.get("FREJA_CODEX_DOCKER_IMAGE", "python:3.12-alpine")

_docker_available_cache = None


def _docker_available() -> bool:
    """Checks (and caches) whether a usable docker CLI + daemon exists on the host."""
    global _docker_available_cache
    if _docker_available_cache is None:
        if shutil.which("docker") is None:
            _docker_available_cache = False
        else:
            try:
                probe = subprocess.run(
                    ["docker", "info"], capture_output=True, timeout=10
                )
                _docker_available_cache = probe.returncode == 0
            except Exception:
                _docker_available_cache = False
    return _docker_available_cache


def resolve_sandbox_mode() -> str:
    """Returns the effective sandbox mode: 'docker' or 'local'."""
    mode = os.environ.get(CODEX_SANDBOX_MODE_ENV, "auto").strip().lower()
    if mode == "docker":
        return "docker"
    if mode == "local":
        return "local"
    return "docker" if _docker_available() else "local"


def _docker_run_args(container_name: str, inner_cmd: list) -> list:
    """Builds the argv for a locked-down throwaway container: no network, read-only
    root filesystem, capped memory/pids/cpu, with only the sandbox dir writable."""
    return [
        "docker", "run", "--rm",
        "--name", container_name,
        "--network=none",
        "--read-only",
        "--memory=512m",
        "--pids-limit=64",
        "--cpus=1",
        "--tmpfs", "/tmp:size=64m",
        "-v", f"{SHELL_SANDBOX_DIR}:/work",
        "-w", "/work",
        DOCKER_SANDBOX_IMAGE,
    ] + inner_cmd


async def _kill_container(container_name: str):
    """Best-effort cleanup of a container that outlived its client (e.g. on timeout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "kill", container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=15)
    except Exception:
        pass


def log_codex_execution(tool: str, command: str, exit_code, detail: str = ""):
    """Appends one row to the persistent codex audit log. Never raises: logging
    failure must not break the tool call itself."""
    try:
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO codex_audit_log (timestamp, tool, command, exit_code, detail)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.datetime.now().isoformat(timespec="seconds"),
                    tool,
                    command[:2000],
                    exit_code if exit_code is not None else -999,
                    detail[:500],
                ),
            )
            conn.commit()
    except Exception as e:
        print(f"[CODEX AUDIT LOG] Could not write to the log: {e}")


def resolve_within_project(rel_path: str) -> str:
    """Resolves rel_path against PROJECT_ROOT and raises ValueError if it escapes the project directory
    (via '..' traversal or an absolute path that overrides the join)."""
    # Windows-style separators and drive letters are never part of a valid
    # forward-slash relative path; reject them on every OS so a path like
    # '..\\..\\x' can't slip through os.path.join as a single literal component
    # on Linux while traversing on a Windows deployment.
    if '\\' in rel_path or re.match(r'^[A-Za-z]:', rel_path):
        raise ValueError(f"Security error: The path '{rel_path}' contains invalid Windows characters.")
    project_root_real = os.path.realpath(PROJECT_ROOT)
    abs_path = os.path.realpath(os.path.join(project_root_real, rel_path))
    if os.path.commonpath([abs_path, project_root_real]) != project_root_real:
        raise ValueError(f"Security error: The path '{rel_path}' lies outside the project directory.")
    return abs_path


# Git supports "remote helper" transports (ext::, fd::) that run an arbitrary program
# as part of a clone. Only allow well-known network protocols.
ALLOWED_GIT_CLONE_SCHEMES = ('https://', 'http://', 'git://', 'ssh://')


def verify_safe_git_clone_url(url: str):
    """Blocks git clone transports (ext::, fd::, file://, etc.) that can execute arbitrary commands."""
    if not url.lower().startswith(ALLOWED_GIT_CLONE_SCHEMES):
        raise ValueError(
            "Security error: Only https://, http://, git:// or ssh:// are allowed for clone URLs."
        )


def verify_safe_shell_command(cmd_str: str):
    """Checks the shell command string for suspicious or dangerous operations."""
    if '$(' in cmd_str or '`' in cmd_str:
        raise ValueError("Security error: Command substitution ($() or `) is blocked.")

    if '>' in cmd_str or '<' in cmd_str:
        raise ValueError("Security error: Redirection (> or <) is blocked.")

    if '..' in cmd_str:
        raise ValueError("Security error: Directory traversal ('..') is blocked.")

    # cmd.exe expands %VAR%; this can smuggle absolute paths / secrets past the
    # sandbox working directory (e.g. del %USERPROFILE%\...). Block it outright.
    if '%' in cmd_str:
        raise ValueError("Security error: Environment variable expansion ('%') is blocked.")

    # Block absolute paths (Windows drive letters, UNC shares, POSIX root, ~ home) so
    # a command can't escape the sandbox cwd even without '..'.
    if re.search(r'(^|[\s"\'=;|&])([A-Za-z]:[\\/]|\\\\|/|~)', cmd_str):
        raise ValueError("Security error: Absolute paths are blocked.")

    lowered_cmd = cmd_str.lower()
    for marker in SENSITIVE_PATH_MARKERS:
        if marker in lowered_cmd:
            raise ValueError(f"Security error: Reference to a protected file ('{marker}') is blocked.")

    clean_cmd = cmd_str.replace("'", "").replace('"', "").replace('\\', "")
    tokens = re.split(r'[\s/;|&:]+', clean_cmd)

    for token in tokens:
        token_clean = token.strip().lower()
        if not token_clean:
            continue
        if token_clean in BLOCKED_SHELL_COMMANDS or BLOCKED_SHELL_PATTERN.match(token_clean):
            raise ValueError(f"Security error: The command or operator '{token}' is blocked.")


async def _wait_with_timeout(proc, timeout: int):
    """Waits for a subprocess to finish, killing it and raising TimeoutError if it hangs."""
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.communicate()
        except Exception:
            pass
        raise TimeoutError(f"The command exceeded the timeout of {timeout} seconds and was terminated.")


async def run_subprocess_command(cmd_str: str, cwd: str = PROJECT_ROOT, timeout: int = DEFAULT_SUBPROCESS_TIMEOUT) -> dict:
    """Helper to run a shell command asynchronously and return results."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=build_sandbox_env(),
            **_sandbox_subprocess_kwargs()
        )
        stdout, stderr = await _wait_with_timeout(proc, timeout)
        exit_code = proc.returncode
        stdout_str = stdout.decode("utf-8", errors="ignore")
        stderr_str = stderr.decode("utf-8", errors="ignore")
        output = stdout_str + stderr_str

        return {
            "exit_code": exit_code,
            "stdout": stdout_str,
            "stderr": stderr_str,
            "output": output
        }
    except Exception as e:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "output": f"Subprocess exception: {str(e)}"
        }


async def run_subprocess_exec(args_list, cwd: str = PROJECT_ROOT, timeout: int = DEFAULT_SUBPROCESS_TIMEOUT, extra_env: dict = None) -> dict:
    """Runs a command via exec (no shell), so arguments can't be parsed by a shell. Prevents command injection."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args_list,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=build_sandbox_env(extra_env),
            **_sandbox_subprocess_kwargs()
        )
        stdout, stderr = await _wait_with_timeout(proc, timeout)
        exit_code = proc.returncode
        stdout_str = stdout.decode("utf-8", errors="ignore")
        stderr_str = stderr.decode("utf-8", errors="ignore")
        output = stdout_str + stderr_str

        return {
            "exit_code": exit_code,
            "stdout": stdout_str,
            "stderr": stderr_str,
            "output": output
        }
    except Exception as e:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "output": f"Subprocess exception: {str(e)}"
        }


async def _run_in_docker(language: str, code: str, temp_file_name: str = None) -> dict:
    """Runs the code/command inside a throwaway, network-less container. The sandbox
    dir is the only writable mount, so host files can't be read or modified even if
    the blocklist were bypassed."""
    container_name = f"freja_codex_{uuid.uuid4().hex[:12]}"
    if language == "python":
        inner_cmd = ["python", f"/work/{temp_file_name}"]
    else:
        inner_cmd = ["sh", "-c", code]

    res = await run_subprocess_exec(_docker_run_args(container_name, inner_cmd))
    if "timeout" in res.get("stderr", ""):
        await _kill_container(container_name)
    return res


async def execute_codex_code_impl(args: dict) -> dict:
    """Executes Python code or shell commands in a sandbox (docker if available)."""
    language = args.get("language", "python").lower()
    code = args.get("code", "")

    if not code:
        return {"error": "No code or command was provided."}

    # The AST/blocklist validation always runs, even in docker mode (defense in depth).
    try:
        if language == "python":
            verify_safe_python_code(code)
        else:
            verify_safe_shell_command(code)
    except ValueError as val_err:
        log_codex_execution("execute_codex_code", code, None, f"blocked: {val_err}")
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(val_err),
            "output": f"Security error: {str(val_err)}"
        }

    os.makedirs(SHELL_SANDBOX_DIR, exist_ok=True)
    sandbox_mode = resolve_sandbox_mode()

    if language == "python":
        # Unique name so concurrent calls can't collide; placed inside the sandbox
        # dir so the docker mount can see it and relative paths stay contained.
        temp_file_name = f"temp_codex_{uuid.uuid4().hex}.py"
        temp_file = os.path.join(SHELL_SANDBOX_DIR, temp_file_name)

        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(code)

            if sandbox_mode == "docker":
                res = await _run_in_docker("python", code, temp_file_name)
            else:
                res = await run_subprocess_command(
                    f"{sys.executable} {temp_file}", cwd=SHELL_SANDBOX_DIR
                )
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
    else:
        if sandbox_mode == "docker":
            res = await _run_in_docker("shell", code)
        else:
            res = await run_subprocess_command(code, cwd=SHELL_SANDBOX_DIR)

    log_codex_execution("execute_codex_code", code, res["exit_code"], f"sandbox={sandbox_mode}")
    sandbox_note = (
        "isolated Docker container" if sandbox_mode == "docker"
        else "locally on the host machine (weaker isolation; install Docker for full sandbox)"
    )
    return {
        "message": f"Executed {'Python code' if language == 'python' else 'command'} in {sandbox_note}.",
        "sandbox": sandbox_mode,
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
        "output": res["output"]
    }


# Cloned repos land in a dedicated workspace instead of PROJECT_ROOT, so a clone
# can never shadow project modules or dump files into the served client directory.
GIT_CLONE_WORKSPACE = os.path.join(PROJECT_ROOT, "backend", "cache", "git_workspace")


async def _git_branch_exists(branch: str) -> bool:
    """True if `branch` names an existing local branch (not a file path or remote ref)."""
    res = await run_subprocess_exec(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]
    )
    return res["exit_code"] == 0


def _git_result(action: str, command: str, res: dict) -> dict:
    log_codex_execution("codex_git_ops", command, res["exit_code"], f"action={action}")
    return {
        "action": action,
        "command": command,
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
        "output": res["output"]
    }


async def codex_git_ops_impl(args: dict) -> dict:
    """Runs git commands locally within the workspace."""
    action = args.get("action", "").lower()
    argument = (args.get("argument", "") or "").strip()

    if not action:
        return {"error": "The git action is missing."}

    # Actions that operate on a user-supplied argument must have one.
    if action in ("clone", "checkout", "commit") and not argument:
        return {"error": f"The git action '{action}' requires an argument."}

    # Reject leading-dash arguments to block git option injection (e.g. --upload-pack=...).
    if action in ("clone", "checkout") and argument.startswith("-"):
        return {"error": "Security error: The argument must not start with '-'."}

    # Block remote-helper transports (ext::, fd::, ...) that let `git clone` execute an
    # arbitrary program instead of fetching over a network protocol.
    if action == "clone":
        try:
            verify_safe_git_clone_url(argument)
        except ValueError as val_err:
            return {"error": str(val_err)}

    # Defense in depth: restrict git itself to safe network protocols, in case a future
    # code path builds a clone/fetch argument without going through verify_safe_git_clone_url.
    git_env = {"GIT_ALLOW_PROTOCOL": "http:https:git:ssh"}

    # Commit is two steps: stage everything, then commit with the message as a single argv element.
    if action == "commit":
        add_res = await run_subprocess_exec(["git", "add", "."], extra_env=git_env)
        if add_res["exit_code"] != 0:
            return _git_result(action, "git add .", add_res)
        res = await run_subprocess_exec(["git", "commit", "-m", argument], extra_env=git_env)
        return _git_result(action, f"git add . && git commit -m '{argument}'", res)

    # Checkout is restricted to existing local branches: `git checkout <file>` would
    # silently discard uncommitted changes in that file, which the AI must not be
    # able to trigger.
    if action == "checkout":
        if not await _git_branch_exists(argument):
            return {"error": f"Security error: '{argument}' is not an existing local branch. "
                             "Checkout of files or unknown references is blocked."}
        res = await run_subprocess_exec(["git", "checkout", argument], extra_env=git_env)
        return _git_result(action, f"git checkout {argument}", res)

    # Clone only via https (verify_safe_git_clone_url above already blocked remote-helper
    # transports), into the dedicated workspace directory.
    if action == "clone":
        if not argument.lower().startswith("https://"):
            return {"error": "Security error: Only https:// clone URLs are allowed."}
        os.makedirs(GIT_CLONE_WORKSPACE, exist_ok=True)
        res = await run_subprocess_exec(["git", "clone", argument], cwd=GIT_CLONE_WORKSPACE, extra_env=git_env)
        return _git_result(action, f"git clone {argument} (in {GIT_CLONE_WORKSPACE})", res)

    # Build argv lists (no shell) so the argument can never be parsed as a shell command.
    git_cmds = {
        "status": ["git", "status"],
        "log": ["git", "log", "-n", "5"],
        "diff": ["git", "diff", "--stat", "HEAD"],
        "branch": ["git", "branch", "--list"],
        "pull": ["git", "pull"],
        "push": ["git", "push"],
    }

    cmd_list = git_cmds.get(action)
    if not cmd_list:
        return {"error": f"Unknown git action: '{action}'."}

    res = await run_subprocess_exec(cmd_list, extra_env=git_env)
    return _git_result(action, " ".join(cmd_list), res)


async def call_gemini_api(prompt: str, system_instruction: str = "") -> str:
    """Queries Gemini via the shared client (kept as a thin wrapper for existing callers)."""
    return await gemini_client.generate_text(prompt, system_instruction)


AUDIT_MAX_CHARS = 180000


async def codex_audit_codebase_impl(args: dict = None) -> dict:
    """Analyzes the Freja python/JS codebase for bugs and code quality, writing the review to a file."""
    # 1. Collect candidate files, most recently modified first, so that when the
    # prompt hits the size limit it's the stale files that get cut - not whichever
    # files happened to come last in os.walk order.
    candidates = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Filter directories in place
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]

        for file in files:
            ext = os.path.splitext(file)[1]
            if ext in ALLOWED_EXTENSIONS and file not in IGNORED_FILES:
                # Skip files whose name suggests they hold secrets, even if the extension
                # is allowlisted (e.g. settings.local.json, a *.token file).
                lower_name = file.lower()
                if any(marker in lower_name for marker in SENSITIVE_FILENAME_MARKERS):
                    print(f"[CODEX AUDIT] Skipping potential secret-bearing file: {file}")
                    continue

                file_path = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(file_path)
                except OSError:
                    mtime = 0
                candidates.append((mtime, file_path))

    candidates.sort(reverse=True)

    code_content = ""
    file_count = 0
    truncated = False
    for _, file_path in candidates:
        rel_path = os.path.relpath(file_path, PROJECT_ROOT)
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                # Redact obvious inline secrets before the content leaves the host.
                safe_content = redact_secrets(f.read())
                code_content += f"\n--- FILE: {rel_path} ---\n{safe_content}\n"
                file_count += 1
        except Exception as e:
            print(f"[CODEX AUDIT] Could not read {rel_path}: {e}")
        if len(code_content) > AUDIT_MAX_CHARS:
            truncated = True
            break

    if file_count == 0:
        return {"error": "No files were found to audit."}

    # Limit character context size to prevent token exhaustion
    if truncated or len(code_content) > AUDIT_MAX_CHARS:
        code_content = code_content[:AUDIT_MAX_CHARS] + "\n\n... [TRUNCATED DUE TO SIZE LIMITS] ..."
        
    system_instruction = f"""You are an expert, senior software architect.
Analyse the attached source code with respect to:
1. Architectural patterns and any violations of them.
2. Code quality, security and performance problems.
3. Improvements and refactoring suggestions.
4. Potential bugs or edge cases.

Structure your answer EXACTLY as follows:
1. A short summary (bullet list of the most important findings).
2. A separator, exactly: {AUDIT_REPORT_SEPARATOR}
3. The full, detailed Markdown report below the separator.

Write the entire answer in SWEDISH.
Use emojis to make the report more readable (e.g. 🔴 for critical errors, ⚠️ for warnings,
✅ for strengths, 💡 for tips).
"""
    
    prompt = f"Here is the source code for the project ({file_count} files):\n{code_content}"
    
    try:
        report_text = await call_gemini_api(prompt, system_instruction)
    except Exception as e:
        return {"error": f"Failed to generate the code review: {str(e)}"}
        
    # Write report to docs/ directory
    docs_dir = os.path.join(PROJECT_ROOT, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"code_audit_{timestamp}.md"
    filepath = os.path.join(docs_dir, filename)
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report_text)
    except Exception as e:
        return {"error": f"Could not save the report to a file: {str(e)}"}
        
    # Extract summary
    summary = report_text.split(AUDIT_REPORT_SEPARATOR)[0].strip() if AUDIT_REPORT_SEPARATOR in report_text else report_text[:800]
    
    return {
        "summary": summary,
        "report_file": filepath,
        "file_count_analyzed": file_count
    }


async def codex_run_and_fix_impl(args: dict) -> dict:
    """Runs a script or test suite command and attempts to automatically fix errors if it fails."""
    command = args.get("command", "")
    file_path = args.get("file_path", "")
    max_retries = int(args.get("max_retries", 3) or 3)
    
    if not command or not file_path:
        return {"error": "Both 'command' and 'file_path' are required to run run_and_fix."}
        
    try:
        verify_safe_shell_command(command)
    except ValueError as val_err:
        return {"error": f"Security error in run_and_fix: {str(val_err)}"}
        
    try:
        abs_file_path = resolve_within_project(file_path)
    except ValueError as val_err:
        return {"error": str(val_err)}
    if not os.path.exists(abs_file_path):
        return {"error": f"The target file was not found at: {abs_file_path}"}

    # Only allow the auto-fixer to overwrite known code/text file types, so a fix can't
    # be steered into writing arbitrary model output to an executable, binary, or an
    # unexpected path within the project.
    fix_ext = os.path.splitext(abs_file_path)[1].lower()
    if fix_ext not in ALLOWED_FIX_EXTENSIONS:
        return {"error": f"Security error: Auto-fix is not supported for the file type '{fix_ext or 'unknown'}'. Allowed: {', '.join(sorted(ALLOWED_FIX_EXTENSIONS))}."}

    # Safety net: keep a backup of the original file so a bad AI rewrite can always
    # be rolled back, even if the file was never committed to git.
    backup_path = abs_file_path + ".codex_backup"
    try:
        shutil.copy2(abs_file_path, backup_path)
    except Exception as backup_err:
        return {"error": f"Could not create backup copy ({backup_path}): {backup_err}"}

    history = []

    for attempt in range(max_retries + 1):
        # Execute the command
        res = await run_subprocess_command(command)
        exit_code = res["exit_code"]
        output = res["output"]
        
        if exit_code == 0:
            return {
                "status": "success",
                "message": f"The command succeeded on attempt {attempt + 1}.",
                "output": output,
                "backup_file": backup_path,
                "history": history
            }

        if attempt == max_retries:
            return {
                "status": "failed",
                "message": f"Failed after {max_retries} auto-fix attempts. "
                           f"Original file can be restored from: {backup_path}",
                "output": output,
                "backup_file": backup_path,
                "history": history
            }
            
        # Try to fix: read current code
        try:
            with open(abs_file_path, "r", encoding="utf-8") as f:
                current_code = f.read()
        except Exception as read_err:
            return {"error": f"Could not read the source file: {str(read_err)}"}
            
        # Ask Gemini to fix it
        prompt = f"""Running the command `{command}` failed for the file `{file_path}`.

Command output:
{output}

Current code in `{file_path}`:
```
{current_code}
```

Find the error and repair the source code.
IMPORTANT: Return only the complete, updated source code. Do not write any commentary,
explanations or markdown code fences (such as ```python). Give me only raw, clean code that
can be written directly to the file.
"""
        
        try:
            fixed_code = await call_gemini_api(prompt, "You are an AI coding assistant that answers with clean code only, without markdown wrappers.")

            # Clean markdown code block wraps if LLM ignored instructions
            if fixed_code.startswith("```"):
                fixed_code = fixed_code.split("\n", 1)[1]
            if fixed_code.endswith("```"):
                fixed_code = fixed_code.rsplit("\n", 1)[0]

            fixed_code = fixed_code.strip()

            # Refuse suspicious rewrites: an empty or drastically shrunken response
            # is almost always a truncated/failed generation, not a real fix.
            if len(fixed_code) < 10 or len(fixed_code) < len(current_code) * 0.2:
                history.append(f"Attempt {attempt + 1}: AI response was rejected (empty or suspiciously short) - file was left untouched.")
                continue

            # Re-run the same AST sandbox check used for execute_codex_code before writing
            # LLM-generated code to disk, so auto-fix can't silently reintroduce blocked
            # imports/calls (os, subprocess, eval, etc.) that the sandbox exists to prevent.
            if abs_file_path.endswith(".py"):
                try:
                    verify_safe_python_code(fixed_code)
                except ValueError as val_err:
                    return {
                        "error": f"Security error: The generated fix code was blocked: {str(val_err)}",
                        "backup_file": backup_path,
                        "history": history
                    }

            diff = "\n".join(difflib.unified_diff(
                current_code.splitlines(), fixed_code.splitlines(),
                fromfile=f"{file_path} (before)", tofile=f"{file_path} (after)", lineterm=""
            ))

            # Write fixed code back to file
            with open(abs_file_path, "w", encoding="utf-8") as f:
                f.write(fixed_code + "\n")

            log_codex_execution("codex_run_and_fix", command, exit_code,
                                f"rewrote {file_path} (attempt {attempt + 1})")
            history.append(
                f"Attempt {attempt + 1}: Corrected the source code based on execution error. "
                f"Backup file: {backup_path}\nDiff:\n{diff[:4000]}"
            )
        except Exception as e:
            return {
                "error": f"An error occurred while generating the auto-fix on attempt {attempt + 1}: {str(e)}",
                "backup_file": backup_path,
                "history": history
            }

    return {"error": "Unexpected end of the retry loop."}
