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
        raise ValueError(f"Syntaxfel i Python-koden: {e}")
        
    for node in ast.walk(root):
        if isinstance(node, ast.Import):
            for name in node.names:
                mod = name.name.split('.')[0]
                if mod in blocked_modules:
                    raise ValueError(f"Säkerhetsfel: Import av modulen '{name.name}' är blockerad.")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split('.')[0]
                if mod in blocked_modules:
                    raise ValueError(f"Säkerhetsfel: Import från modulen '{node.module}' är blockerad.")
            # Block `from <module> import open as reader`-style aliasing: the imported
            # object keeps its dangerous behavior even when bound to an innocent name.
            for name in node.names:
                if name.name in blocked_calls:
                    raise ValueError(f"Säkerhetsfel: Import av '{name.name}' är blockerad.")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in blocked_calls:
                    raise ValueError(f"Säkerhetsfel: Anrop av funktionen '{node.func.id}' är blockerad.")
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr in blocked_calls or node.func.attr.startswith('__'):
                    raise ValueError(f"Säkerhetsfel: Anrop av attributet/metoden '{node.func.attr}' är blockerad.")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith('__') or node.attr in blocked_calls:
                raise ValueError(f"Säkerhetsfel: Åtkomst till dunder-attributet '{node.attr}' är blockerad.")
        elif isinstance(node, ast.Name):
            if node.id.startswith('__') or node.id in blocked_calls:
                raise ValueError(f"Säkerhetsfel: Variabeln/namnet '{node.id}' är blockerad.")
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                val = node.value.strip()
                if '__' in val:
                    raise ValueError(f"Säkerhetsfel: Textsträng innehåller dunder-mönster ('__') vilket är blockerat.")
                if val in blocked_modules or val in blocked_calls:
                    raise ValueError(f"Säkerhetsfel: Textsträng innehåller blockerat ord '{val}' vilket är blockerat.")

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
        print(f"[CODEX AUDIT LOG] Kunde inte skriva till loggen: {e}")


def resolve_within_project(rel_path: str) -> str:
    """Resolves rel_path against PROJECT_ROOT and raises ValueError if it escapes the project directory
    (via '..' traversal or an absolute path that overrides the join)."""
    # Windows-style separators and drive letters are never part of a valid
    # forward-slash relative path; reject them on every OS so a path like
    # '..\\..\\x' can't slip through os.path.join as a single literal component
    # on Linux while traversing on a Windows deployment.
    if '\\' in rel_path or re.match(r'^[A-Za-z]:', rel_path):
        raise ValueError(f"Säkerhetsfel: Sökvägen '{rel_path}' innehåller ogiltiga Windows-tecken.")
    project_root_real = os.path.realpath(PROJECT_ROOT)
    abs_path = os.path.realpath(os.path.join(project_root_real, rel_path))
    if os.path.commonpath([abs_path, project_root_real]) != project_root_real:
        raise ValueError(f"Säkerhetsfel: Sökvägen '{rel_path}' ligger utanför projektkatalogen.")
    return abs_path


# Git supports "remote helper" transports (ext::, fd::) that run an arbitrary program
# as part of a clone. Only allow well-known network protocols.
ALLOWED_GIT_CLONE_SCHEMES = ('https://', 'http://', 'git://', 'ssh://')


def verify_safe_git_clone_url(url: str):
    """Blocks git clone transports (ext::, fd::, file://, etc.) that can execute arbitrary commands."""
    if not url.lower().startswith(ALLOWED_GIT_CLONE_SCHEMES):
        raise ValueError(
            "Säkerhetsfel: Endast https://, http://, git:// eller ssh:// är tillåtna för klon-URL:er."
        )


def verify_safe_shell_command(cmd_str: str):
    """Checks the shell command string for suspicious or dangerous operations."""
    if '$(' in cmd_str or '`' in cmd_str:
        raise ValueError("Säkerhetsfel: Kommandosubstitution ($() eller `) är blockerad.")

    if '>' in cmd_str or '<' in cmd_str:
        raise ValueError("Säkerhetsfel: Omdirigering (> eller <) är blockerad.")

    if '..' in cmd_str:
        raise ValueError("Säkerhetsfel: Katalog-traversal ('..') är blockerad.")

    lowered_cmd = cmd_str.lower()
    for marker in SENSITIVE_PATH_MARKERS:
        if marker in lowered_cmd:
            raise ValueError(f"Säkerhetsfel: Referens till en skyddad fil ('{marker}') är blockerad.")

    clean_cmd = cmd_str.replace("'", "").replace('"', "").replace('\\', "")
    tokens = re.split(r'[\s/;|&:]+', clean_cmd)

    for token in tokens:
        token_clean = token.strip().lower()
        if not token_clean:
            continue
        if token_clean in BLOCKED_SHELL_COMMANDS or BLOCKED_SHELL_PATTERN.match(token_clean):
            raise ValueError(f"Säkerhetsfel: Kommandot eller operatorn '{token}' är blockerad.")


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
        raise TimeoutError(f"Kommandot överskred tidsgränsen på {timeout} sekunder och avbröts.")


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
    if "tidsgränsen" in res.get("stderr", ""):
        await _kill_container(container_name)
    return res


async def execute_codex_code_impl(args: dict) -> dict:
    """Executes Python code or shell commands in a sandbox (docker if available)."""
    language = args.get("language", "python").lower()
    code = args.get("code", "")

    if not code:
        return {"error": "Ingen kod eller kommando angavs."}

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
            "output": f"Säkerhetsfel: {str(val_err)}"
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
        "isolerad Docker-container" if sandbox_mode == "docker"
        else "lokalt på värdmaskinen (svagare isolering; installera Docker för full sandbox)"
    )
    return {
        "message": f"Körde {'Python-kod' if language == 'python' else 'kommandot'} i {sandbox_note}.",
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
        return {"error": "Git-åtgärd saknas."}

    # Actions that operate on a user-supplied argument must have one.
    if action in ("clone", "checkout", "commit") and not argument:
        return {"error": f"Git-åtgärden '{action}' kräver ett argument."}

    # Reject leading-dash arguments to block git option injection (e.g. --upload-pack=...).
    if action in ("clone", "checkout") and argument.startswith("-"):
        return {"error": "Säkerhetsfel: Argumentet får inte börja med '-'."}

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
            return {"error": f"Säkerhetsfel: '{argument}' är inte en befintlig lokal branch. "
                             "Checkout av filer eller okända referenser är blockerad."}
        res = await run_subprocess_exec(["git", "checkout", argument], extra_env=git_env)
        return _git_result(action, f"git checkout {argument}", res)

    # Clone only via https (verify_safe_git_clone_url above already blocked remote-helper
    # transports), into the dedicated workspace directory.
    if action == "clone":
        if not argument.lower().startswith("https://"):
            return {"error": "Säkerhetsfel: Endast https://-URL:er kan klonas."}
        os.makedirs(GIT_CLONE_WORKSPACE, exist_ok=True)
        res = await run_subprocess_exec(["git", "clone", argument], cwd=GIT_CLONE_WORKSPACE, extra_env=git_env)
        return _git_result(action, f"git clone {argument} (i {GIT_CLONE_WORKSPACE})", res)

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
        return {"error": f"Okänd git-åtgärd: '{action}'."}

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
                code_content += f"\n--- FIL: {rel_path} ---\n{f.read()}\n"
                file_count += 1
        except Exception as e:
            print(f"[CODEX AUDIT] Kunde inte läsa {rel_path}: {e}")
        if len(code_content) > AUDIT_MAX_CHARS:
            truncated = True
            break

    if file_count == 0:
        return {"error": "Inga filer hittades att granska."}

    # Limit character context size to prevent token exhaustion
    if truncated or len(code_content) > AUDIT_MAX_CHARS:
        code_content = code_content[:AUDIT_MAX_CHARS] + "\n\n... [TRUNCATED DUE TO SIZE LIMITS] ..."
        
    system_instruction = """Du är en expert och senior mjukvaruarkitekt.
Analysera den bifogade källkoden med avseende på:
1. Arkitektoniska mönster och eventuella överträdelser.
2. Kodkvalitet, säkerhet och prestandaproblem.
3. Förbättringar och förslag på refaktorisering.
4. Potentiella buggar eller kantfall.

Strukturera ditt svar EXAKT enligt följande:
1. Kort sammanfattning (Punktlista de viktigaste fynden).
2. Separator exakt som: ---RAPPORT_START---
3. Fullständig och detaljerad Markdown-rapport nedanför separatorn på SVENSKA.

Använd emojis för att göra rapporten mer läsbar (t.ex. 🔴 för kritiska fel, ⚠️ för varningar, ✅ för styrkor, 💡 för tips).
"""
    
    prompt = f"Här är källkoden för projektet ({file_count} filer):\n{code_content}"
    
    try:
        report_text = await call_gemini_api(prompt, system_instruction)
    except Exception as e:
        return {"error": f"Misslyckades att generera kodgranskning: {str(e)}"}
        
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
        return {"error": f"Kunde inte spara rapporten till fil: {str(e)}"}
        
    # Extract summary
    summary = report_text.split("---RAPPORT_START---")[0].strip() if "---RAPPORT_START---" in report_text else report_text[:800]
    
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
        return {"error": "Både 'command' och 'file_path' krävs för att köra run_and_fix."}
        
    try:
        verify_safe_shell_command(command)
    except ValueError as val_err:
        return {"error": f"Säkerhetsfel i run_and_fix: {str(val_err)}"}
        
    try:
        abs_file_path = resolve_within_project(file_path)
    except ValueError as val_err:
        return {"error": str(val_err)}
    if not os.path.exists(abs_file_path):
        return {"error": f"Målfilen hittades inte på: {abs_file_path}"}

    # Safety net: keep a backup of the original file so a bad AI rewrite can always
    # be rolled back, even if the file was never committed to git.
    backup_path = abs_file_path + ".codex_backup"
    try:
        shutil.copy2(abs_file_path, backup_path)
    except Exception as backup_err:
        return {"error": f"Kunde inte skapa säkerhetskopia ({backup_path}): {backup_err}"}

    history = []

    for attempt in range(max_retries + 1):
        # Execute the command
        res = await run_subprocess_command(command)
        exit_code = res["exit_code"]
        output = res["output"]
        
        if exit_code == 0:
            return {
                "status": "success",
                "message": f"Kommando lyckades på försök {attempt + 1}!",
                "output": output,
                "backup_file": backup_path,
                "history": history
            }

        if attempt == max_retries:
            return {
                "status": "failed",
                "message": f"Misslyckades efter {max_retries} auto-fix försök. "
                           f"Originalfilen kan återställas från: {backup_path}",
                "output": output,
                "backup_file": backup_path,
                "history": history
            }
            
        # Try to fix: read current code
        try:
            with open(abs_file_path, "r", encoding="utf-8") as f:
                current_code = f.read()
        except Exception as read_err:
            return {"error": f"Kunde inte läsa källkodsfilen: {str(read_err)}"}
            
        # Ask Gemini to fix it
        prompt = f"""Kommando-exekveringen `{command}` misslyckades för filen `{file_path}`.

Kommando-output:
{output}

Nuvarande kod i `{file_path}`:
```
{current_code}
```

Hitta felet och rätta källkoden. 
VIKTIGT: Returnera enbart den fullständiga, uppdaterade källkoden. Skriv inga kommentarer, förklaringar eller markdown-kodblock (som ```python). Ge mig enbart rå, ren kod som kan skrivas direkt till filen.
"""
        
        try:
            fixed_code = await call_gemini_api(prompt, "Du är en AI-kodningsassistent som svarar med enbart ren kod utan markdown-omslag.")

            # Clean markdown code block wraps if LLM ignored instructions
            if fixed_code.startswith("```"):
                fixed_code = fixed_code.split("\n", 1)[1]
            if fixed_code.endswith("```"):
                fixed_code = fixed_code.rsplit("\n", 1)[0]

            fixed_code = fixed_code.strip()

            # Refuse suspicious rewrites: an empty or drastically shrunken response
            # is almost always a truncated/failed generation, not a real fix.
            if len(fixed_code) < 10 or len(fixed_code) < len(current_code) * 0.2:
                history.append(f"Försök {attempt + 1}: AI-svaret förkastades (tomt eller misstänkt kort) - filen lämnades orörd.")
                continue

            # Re-run the same AST sandbox check used for execute_codex_code before writing
            # LLM-generated code to disk, so auto-fix can't silently reintroduce blocked
            # imports/calls (os, subprocess, eval, etc.) that the sandbox exists to prevent.
            if abs_file_path.endswith(".py"):
                try:
                    verify_safe_python_code(fixed_code)
                except ValueError as val_err:
                    return {
                        "error": f"Säkerhetsfel: Genererad fix-kod blockerades: {str(val_err)}",
                        "backup_file": backup_path,
                        "history": history
                    }

            diff = "\n".join(difflib.unified_diff(
                current_code.splitlines(), fixed_code.splitlines(),
                fromfile=f"{file_path} (före)", tofile=f"{file_path} (efter)", lineterm=""
            ))

            # Write fixed code back to file
            with open(abs_file_path, "w", encoding="utf-8") as f:
                f.write(fixed_code + "\n")

            log_codex_execution("codex_run_and_fix", command, exit_code,
                                f"rewrote {file_path} (attempt {attempt + 1})")
            history.append(
                f"Försök {attempt + 1}: Korrigerade källkoden baserat på exekveringsfel. "
                f"Säkerhetskopia: {backup_path}\nDiff:\n{diff[:4000]}"
            )
        except Exception as e:
            return {
                "error": f"Fel uppstod vid generering av auto-fix på försök {attempt + 1}: {str(e)}",
                "backup_file": backup_path,
                "history": history
            }

    return {"error": "Oväntat slut på loopen."}
