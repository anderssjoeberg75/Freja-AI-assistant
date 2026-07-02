"""Local Codex Developer Tools Service.

Provides sandbox-free code execution, Git operations, codebase self-auditing,
and autonomous debugging capabilities using the local host environment and Gemini.
"""

import os
import sys
import subprocess
import hashlib
import datetime
import httpx
import json
import asyncio
import re
from backend.config import PROJECT_ROOT
from backend.database import get_api_key

# Ignored patterns for codebase auditing
IGNORED_DIRS = {
    '.git', '__pycache__', 'venv', 'node_modules', 'backend/cache', 
    'freja_io_temp', '.garminconnect', '.pytest_cache', 'htmlcov'
}
ALLOWED_EXTENSIONS = {'.py', '.js', '.html', '.css', '.json', '.md', '.sh', '.txt'}
IGNORED_FILES = {'keys.db', 'freja.db', '.telegram_bot.lock', 'package-lock.json'}


import ast

def verify_safe_python_code(code: str):
    """Parses the Python code into an AST and throws ValueError if suspicious operations are detected."""
    blocked_calls = {
        'eval', 'exec', 'open', 'compile', 'input', '__import__', 'getattr', 'setattr', 
        'globals', 'locals', 'vars', 'classmethod', 'staticmethod', 'breakpoint'
    }
    blocked_modules = {
        'os', 'sys', 'subprocess', 'shutil', 'pty', 'platform', 'socket', 'urllib', 
        'http', 'httpx', 'requests', 'sqlite3', 'ctypes', 'importlib', 'builtins'
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

def build_sandbox_env() -> dict:
    """Builds a minimal subprocess environment, excluding secrets set as process env vars."""
    return {k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV_ALLOWLIST}

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


async def run_subprocess_exec(args_list, cwd: str = PROJECT_ROOT, timeout: int = DEFAULT_SUBPROCESS_TIMEOUT) -> dict:
    """Runs a command via exec (no shell), so arguments can't be parsed by a shell. Prevents command injection."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args_list,
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


async def execute_codex_code_impl(args: dict) -> dict:
    """Executes Python code or shell commands locally on the host."""
    language = args.get("language", "python").lower()
    code = args.get("code", "")
    
    if not code:
        return {"error": "Ingen kod eller kommando angavs."}
        
    try:
        if language == "python":
            verify_safe_python_code(code)
        else:
            verify_safe_shell_command(code)
    except ValueError as val_err:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(val_err),
            "output": f"Säkerhetsfel: {str(val_err)}"
        }
        
    os.makedirs(SHELL_SANDBOX_DIR, exist_ok=True)

    if language == "python":
        # Create temp folder inside backend/cache/ if it doesn't exist
        cache_dir = os.path.join(PROJECT_ROOT, "backend", "cache")
        os.makedirs(cache_dir, exist_ok=True)
        temp_file = os.path.join(cache_dir, f"temp_codex_{int(datetime.datetime.now().timestamp())}.py")

        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(code)

            # Execute python script from the isolated sandbox dir, not PROJECT_ROOT,
            # so relative-path filesystem access can't reach keys.db/.env by accident.
            cmd = f"{sys.executable} {temp_file}"
            res = await run_subprocess_command(cmd, cwd=SHELL_SANDBOX_DIR)
            return {
                "message": "Körde Python-kod lokalt på värdmaskinen.",
                "exit_code": res["exit_code"],
                "stdout": res["stdout"],
                "stderr": res["stderr"],
                "output": res["output"]
            }
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
    else:
        # Run shell command directly, isolated from the real project tree.
        res = await run_subprocess_command(code, cwd=SHELL_SANDBOX_DIR)
        return {
            "message": "Körde kommandot i lokalt skal.",
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

    # Commit is two steps: stage everything, then commit with the message as a single argv element.
    if action == "commit":
        add_res = await run_subprocess_exec(["git", "add", "."])
        if add_res["exit_code"] != 0:
            return {
                "action": action,
                "command": "git add .",
                "exit_code": add_res["exit_code"],
                "stdout": add_res["stdout"],
                "stderr": add_res["stderr"],
                "output": add_res["output"]
            }
        res = await run_subprocess_exec(["git", "commit", "-m", argument])
        return {
            "action": action,
            "command": f"git add . && git commit -m '{argument}'",
            "exit_code": res["exit_code"],
            "stdout": res["stdout"],
            "stderr": res["stderr"],
            "output": res["output"]
        }

    # Build argv lists (no shell) so the argument can never be parsed as a shell command.
    git_cmds = {
        "status": ["git", "status"],
        "log": ["git", "log", "-n", "5"],
        "push": ["git", "push"],
        "clone": ["git", "clone", argument],
        "checkout": ["git", "checkout", argument],
    }

    cmd_list = git_cmds.get(action)
    if not cmd_list:
        return {"error": f"Okänd git-åtgärd: '{action}'."}

    res = await run_subprocess_exec(cmd_list)
    return {
        "action": action,
        "command": " ".join(cmd_list),
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
        "output": res["output"]
    }


async def call_gemini_api(prompt: str, system_instruction: str = "") -> str:
    """Helper to query the official Google Gemini API using local settings credentials."""
    # Fetch API key from db
    api_key = get_api_key('freja_gemini_apikey') or ""
    if not api_key:
        raise Exception("Gemini API-nyckel saknas i databasen.")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    contents = []
    if system_instruction:
        # System instructions are placed in systemInstruction block
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {"temperature": 0.2}
        }
    else:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}
        }
        
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=60.0)
        resp.raise_for_status()
        resp_json = resp.json()
        
    text = resp_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    return text


async def codex_audit_codebase_impl(args: dict = None) -> dict:
    """Analyzes the Freja python/JS codebase for bugs and code quality, writing the review to a file."""
    # 1. Read files recursively
    code_content = ""
    file_count = 0
    
    for root, dirs, files in os.walk(PROJECT_ROOT):
        # Filter directories in place
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        
        for file in files:
            ext = os.path.splitext(file)[1]
            if ext in ALLOWED_EXTENSIONS and file not in IGNORED_FILES:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, PROJECT_ROOT)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        code_content += f"\n--- FIL: {rel_path} ---\n{f.read()}\n"
                        file_count += 1
                except Exception as e:
                    print(f"[CODEX AUDIT] Kunde inte läsa {rel_path}: {e}")
                    
    if file_count == 0:
        return {"error": "Inga filer hittades att granska."}
        
    # Limit character context size to prevent token exhaustion (e.g. max 180,000 chars)
    if len(code_content) > 180000:
        code_content = code_content[:180000] + "\n\n... [TRUNCATED DUE TO SIZE LIMITS] ..."
        
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
        
    abs_file_path = os.path.abspath(os.path.join(PROJECT_ROOT, file_path))
    if not os.path.exists(abs_file_path):
        return {"error": f"Målfilen hittades inte på: {abs_file_path}"}
        
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
                "history": history
            }
            
        if attempt == max_retries:
            return {
                "status": "failed",
                "message": f"Misslyckades efter {max_retries} auto-fix försök.",
                "output": output,
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
                
            # Write fixed code back to file
            with open(abs_file_path, "w", encoding="utf-8") as f:
                f.write(fixed_code.strip() + "\n")
                
            history.append(f"Försök {attempt + 1}: Korrigerade källkoden baserat på exekveringsfel.")
        except Exception as e:
            return {
                "error": f"Fel uppstod vid generering av auto-fix på försök {attempt + 1}: {str(e)}",
                "history": history
            }
            
    return {"error": "Oväntat slut på loopen."}
