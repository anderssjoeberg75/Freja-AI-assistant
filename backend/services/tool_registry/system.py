"""system_update, read_project_file and run_windows_command tools."""

from backend.services.codex_service import run_subprocess_exec
from ._registry import registry

@registry.register(
    name="system_update",
    description="Downloads the latest code from GitHub (git pull) and restarts F.R.E.J.A. to apply the updates.",
    permission_key="freja_tool_system_update_allowed",
    parameters={"type": "OBJECT", "properties": {}},
)
async def exec_system_update(args):
    """Executes git pull from GitHub and schedules a process exit/restart.

    The restart is deliberately delayed ~1.5s so this function can return its result to
    Gemini (and the user can be told an update is starting) before the process dies.
    Coming back up is the supervisor's job - systemd `Restart=always` on Linux, or the
    Task Scheduler restart settings on Windows. Without a supervisor, Freja stays down."""
    import os
    import asyncio
    from backend.config import PROJECT_ROOT

    print("[SYSTEM UPDATE] Initiating remote codebase update via git pull...")
    res = await run_subprocess_exec(["git", "pull"], cwd=str(PROJECT_ROOT))

    output = res.get("stdout", "").strip()
    errors = res.get("stderr", "").strip()
    full_log = output + ("\n" + errors if errors else "")

    if res.get("exit_code", -1) != 0:
        return {"error": f"Git pull failed (exit code {res.get('exit_code')}): {full_log}"}

    print("[SYSTEM UPDATE] Git pull successful. Scheduling uvicorn process restart.")

    async def _delayed_restart():
        await asyncio.sleep(1.5)
        os._exit(0)

    asyncio.create_task(_delayed_restart())
    return {
        "status": "success",
        "message": "Update downloaded from GitHub. F.R.E.J.A. is restarting to apply the changes...",
        "log": full_log
    }


@registry.register(
    name="read_project_file",
    description="Reads the contents of a source file or audit report inside the project (e.g. 'docs/code_audit_20260709.md' or 'backend/routes/settings.py'). Blocked for files holding sensitive data such as databases or .env files.",
    permission_key="freja_tool_read_project_file_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Relative path to the file inside the project directory."
            }
        },
        "required": ["file_path"]
    },
)
async def exec_read_project_file(args):
    """Safely reads the contents of a non-sensitive codebase or audit file."""
    import os
    from backend.config import PROJECT_ROOT
    from backend.services.codex_service import (
        resolve_within_project,
        redact_secrets,
        SENSITIVE_FILENAME_MARKERS
    )

    file_path = args.get("file_path", "").strip()
    if not file_path:
        return {"error": "File name/path is missing."}

    lower_path = file_path.lower()
    # First gate: reject on the requested name before touching the filesystem, so a
    # secret-bearing path is refused even if it does not exist yet.
    if (
        any(marker in lower_path for marker in SENSITIVE_FILENAME_MARKERS) or
        lower_path.endswith(('.db', '.db-wal', '.db-shm', '.key', '.env'))
    ):
        return {"error": "Security error: Access to this file is blocked for security reasons."}

    try:
        # Second gate: resolve_within_project() raises if the path escapes PROJECT_ROOT
        # (e.g. via '..' or a symlink), which is what stops directory traversal.
        abs_path = resolve_within_project(file_path)
        if not os.path.exists(abs_path):
            return {"error": f"The file '{file_path}' was not found."}
        if os.path.isdir(abs_path):
            return {"error": f"The path '{file_path}' is a directory, not a file."}

        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # Third gate: even an allowed file may embed a key, so scrub before returning.
        safe_content = redact_secrets(content)
        return {
            "file_path": file_path,
            "content": safe_content
        }
    except Exception as e:
        return {"error": f"Failed to read the file: {str(e)}"}


@registry.register(
    name="run_windows_command",
    description="Performs system actions on the user's Windows computer, such as launching applications (open_app), opening web addresses (open_url), opening folders in Explorer (open_folder) or running Windows commands (run_cmd).",
    permission_key="freja_tool_run_windows_command_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "action_type": {
                "type": "STRING",
                "description": "The type of action to perform.",
                "enum": ["open_app", "open_url", "open_folder", "run_cmd"]
            },
            "target": {
                "type": "STRING",
                "description": "The target of the action (e.g. 'notepad.exe', 'https://google.com', 'C:\\Pictures' or 'ipconfig')."
            }
        },
        "required": ["action_type", "target"]
    },
)
async def exec_run_windows_command(args):
    """Executes actions on the user's host Windows machine safely."""
    import os
    import re
    import webbrowser
    import subprocess
    import asyncio

    if os.name != "nt":
        return {"error": "This tool is currently only available on Windows systems."}

    action_type = args.get("action_type", "").strip()
    target = args.get("target", "").strip()

    if not action_type or not target:
        return {"error": "The 'action_type' and 'target' parameters are required."}

    if action_type == "open_app":
        # os.startfile launches files, executables AND registered protocol handlers (URLs of
        # any scheme, including file:/javascript: - exactly what open_url below exists to
        # block) - so this needs the same scheme check, plus a denylist of the shell/script
        # interpreters that would turn "launch an app" into arbitrary code execution.
        target_lower = target.lower()
        if "://" in target_lower and not (
            target_lower.startswith("http://") or target_lower.startswith("https://") or target_lower.startswith("mailto:")
        ):
            return {"error": "Security error: Only http://, https:// and mailto: URLs are allowed."}
        if target.startswith("\\\\"):
            return {"error": "Security error: UNC network paths are not allowed."}
        app_base = os.path.basename(target.lower()).rsplit(".", 1)[0]
        BLOCKED_APPS = {
            "powershell", "pwsh", "cmd", "cscript", "wscript", "mshta", "rundll32",
            "regsvr32", "certutil", "bitsadmin", "wmic", "reg", "schtasks", "at",
        }
        if app_base in BLOCKED_APPS:
            return {"error": f"Security error: '{app_base}' is not allowed via open_app."}
        try:
            os.startfile(target)
            return {"status": "success", "message": f"Launched the application '{target}'."}
        except Exception as e:
            return {"error": f"Could not launch the application '{target}': {str(e)}"}

    elif action_type == "open_url":
        # Restrict the scheme: 'file:' or 'javascript:' would turn this into a local-file
        # read or script execution primitive via the default browser.
        target_lower = target.lower()
        if not (target_lower.startswith("http://") or target_lower.startswith("https://") or target_lower.startswith("mailto:")):
            return {"error": "Security error: Only http://, https:// and mailto: addresses are allowed."}
        try:
            webbrowser.open(target)
            return {"status": "success", "message": f"Opened the web address '{target}'."}
        except Exception as e:
            return {"error": f"Could not open the web address '{target}': {str(e)}"}

    elif action_type == "open_folder":
        # Open a directory path in Windows Explorer.
        if not os.path.exists(target):
            return {"error": f"The path '{target}' was not found."}
        if not os.path.isdir(target):
            return {"error": f"The path '{target}' is not a folder/directory."}
        try:
            os.startfile(target)
            return {"status": "success", "message": f"Opened the folder '{target}' in Explorer."}
        except Exception as e:
            return {"error": f"Could not open the folder '{target}': {str(e)}"}

    elif action_type == "run_cmd":
        import shlex
        try:
            # Parse the command safely as a structured argument list
            args_list = shlex.split(target, posix=False)
        except Exception as pe:
            return {"error": f"Invalid command format: {str(pe)}"}

        if not args_list:
            return {"error": "Empty command string."}

        # Clean/sanitize arguments (strip enclosing quotes if posix=False preserved them)
        cmd_args = [arg.strip('"\'') for arg in args_list]
        base_cmd = cmd_args[0].lower()

        # Strip path / file extension (e.g. C:\Windows\System32\ping.exe -> ping). Was
        # previously `.rstrip(".exe")`, which strips trailing characters in the set
        # {'.','e','x'} rather than the literal suffix - "hostname.exe" lost five trailing
        # chars (down to "hostnam") and was silently denied despite being allowlisted.
        base_cmd_name = os.path.basename(base_cmd)
        if base_cmd_name.endswith(".exe"):
            base_cmd_name = base_cmd_name[:-4]

        # Strict allowlist of safe executables to prevent arbitrary command execution
        SAFE_EXECUTABLES = {"ping", "ipconfig", "systeminfo", "hostname", "whoami", "tasklist", "netstat", "git", "echo"}
        if base_cmd_name not in SAFE_EXECUTABLES:
            return {"error": f"Security error: The executable '{base_cmd_name}' is not in the list of approved commands."}

        # Prevent directory traversal or local hijacked binary execution
        if "/" in base_cmd or "\\" in base_cmd:
            return {"error": "Security error: Absolute or relative paths are not allowed in the command executable."}

        # git supports config-driven command execution (`-c core.pager=<cmd>`,
        # `-c core.fsmonitor=<cmd>`, `alias.*=!<cmd>`) - a documented technique to get git
        # itself to spawn an arbitrary process even though this call uses exec (no shell).
        # Reject any flag-like argument so only bare subcommands (status/log/diff/...) reach
        # git through this free-form channel.
        if base_cmd_name == "git" and any(a.startswith("-") for a in cmd_args[1:]):
            return {"error": "Security error: git arguments starting with '-' are not allowed here."}

        try:
            # Execute command directly with safe structured argument array (bypassing the shell)
            proc = await asyncio.create_subprocess_exec(
                cmd_args[0],
                *cmd_args[1:],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            out_str = stdout.decode('utf-8', errors='ignore').strip()
            err_str = stderr.decode('utf-8', errors='ignore').strip()

            return {
                "status": "success" if proc.returncode == 0 else "error",
                "exit_code": proc.returncode,
                "stdout": out_str,
                "stderr": err_str
            }
        except Exception as e:
            return {"error": f"Could not run the command: {str(e)}"}

    else:
        return {"error": f"Unknown action type '{action_type}'."}


