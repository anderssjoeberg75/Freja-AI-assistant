# 🌌 F.R.E.J.A. // Fully Responsive Electronic Judicial Assistant

> **Holographic Neural Interface powered by Gemini AI, Web Audio, & Web Speech APIs**

Welcome to **F.R.E.J.A. (Freja)** – a premium, cyberpunk-inspired AI assistant equipped with advanced voice controls, a holographic particle reactor, long-term neural memory, health & fitness integrations (Garmin, Strava, Withings, Google Calendar, AI Personal Trainer), and real-time multimodal optical scanning (webcam support).

F.R.E.J.A. is built using pure modern web standards (Vanilla HTML5, CSS3, ES6 Javascript) and has been fully modularized into clean, independent files with an event-driven architecture (`FrejaEventBus`) to ensure maximum performance, maintainability, and code readability.

---

## 📥 Installation

F.R.E.J.A. requires **Python 3.10+** and **Git** installed on your system. Follow the steps below for your operating system to set up the environment and install all dependencies.

### 🪟 Windows Setup

1. **Clone the Repository:**
   Open PowerShell or Command Prompt and run:
   ```bash
   git clone https://github.com/anderssjoeberg75/Freja-AI-assistant.git
   cd Freja-AI-assistant
   ```

2. **Create a Virtual Environment:**
   * **PowerShell**:
     ```powershell
     python -m venv venv
     # If script execution is disabled, run: Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
     .\venv\Scripts\Activate.ps1
     ```
   * **Command Prompt**:
     ```cmd
     python -m venv venv
     .\venv\Scripts\activate.bat
     ```

3. **Install Dependencies:**
   ```cmd
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

4. **Install Playwright Browsers:**
   ```cmd
   playwright install chromium
   ```

---

### 🐧 Linux Setup (Ubuntu/Debian)

1. **Install System Prerequisites:**
   Open your terminal and ensure Python, pip, virtual environment tools, and Git are installed:
   ```bash
   sudo apt update
   sudo apt install python3 python3-pip python3-venv git -y
   ```

2. **Clone the Repository:**
   ```bash
   git clone https://github.com/anderssjoeberg75/Freja-AI-assistant.git
   cd Freja-AI-assistant
   ```

3. **Create and Activate Virtual Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

4. **Install Dependencies:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

5. **Install Playwright Browsers & System Dependencies:**
   ```bash
   playwright install chromium
   playwright install-deps chromium
   ```

### ⚙️ Autostart Backend on Ubuntu (systemd Service)

To run the F.R.E.J.A. Backend automatically in the background on system boot (e.g. on an Ubuntu VPS/server):

1. **Create a systemd Service File:**
   ```bash
   sudo nano /etc/systemd/system/freja-backend.service
   ```

2. **Add the following configuration:**
   > [!IMPORTANT]
   > Run `whoami` to get your exact username, and `pwd` inside the project folder to get your exact path.
   > Make sure to replace `YOUR_USERNAME` and `/path/to/Freja-AI-assistant` with your actual values below.

   ```ini
   [Unit]
   Description=F.R.E.J.A. Neural Backend Service
   After=network.target

   [Service]
   Type=simple
   User=YOUR_USERNAME
   WorkingDirectory=/path/to/Freja-AI-assistant
   ExecStart=/path/to/Freja-AI-assistant/venv/bin/python server.py
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

3. **Enable and Start the Service:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now freja-backend
   ```

4. **Verify Service Status & Logs:**
   ```bash
   sudo systemctl status freja-backend
   sudo journalctl -u freja-backend -f
   ```

> [!TIP]
> **Troubleshooting Systemd Exit Errors:**
> - `status=217/USER`: The `User=` setting in the service file is set to a user that doesn't exist on Ubuntu. Check with `whoami`.
> - `status=203/EXEC`: Systemd cannot find the python executable at `ExecStart`. Run `ls -l $(pwd)/venv/bin/python` to verify the exact path to your python binary in your virtual environment. If using system python instead of venv, use `/usr/bin/python3`.

---

## 🌐 Client-Backend Architecture

F.R.E.J.A. is strictly divided into a **Frontend Client HUD** and a **FastAPI Backend Server & Admin Control Panel**:
- **Backend Admin GUI (`http://localhost:8000/`)**: Served by `server.py`. Contains strictly server-side configurations, API key database management, integration credentials (Telegram, Garmin, Strava, Withings, Google Calendar, Instagram/Meta), backend tool permission toggles, and live server status.
- **Client HUD (`http://localhost:5000/` or `http://localhost:8000/client/`)**: Holographic AI Assistant interface containing voice controls, speech synthesis, optics camera scanner, Arc Reactor visualizer, personal health & fitness dashboards, and neural memory vault.

---

## 🚀 Running the Project

### 1. Start the Backend Server & Admin Panel
Ensure your virtual environment is active, then run:
```bash
python server.py
```
- Open **Backend Admin Control Panel**: `http://localhost:8000/`

### 2. Start the Client Assistant HUD (Standalone — Recommended)

Leave the backend running from step 1 and follow these steps in a **second** terminal:

1. **Go to the project folder:**
   ```bash
   cd Freja-AI-assistant
   ```

2. **Activate the same virtual environment** you created during installation:
   ```powershell
   # Windows (PowerShell)
   .\venv\Scripts\Activate.ps1
   ```
   ```bash
   # Linux / macOS
   source venv/bin/activate
   ```

3. **Start the client:**
   ```bash
   python run_client.py
   ```

4. **Open the HUD.** Your browser opens automatically at **`http://localhost:5000/`**. If it doesn't, open that address yourself.

5. **Stop the client** at any time by pressing `Ctrl+C` in this terminal.

> [!TIP]
> Don't want to activate the venv? Call its Python directly instead of step 3:
> ```bash
> .\venv\Scripts\python.exe run_client.py   # Windows
> ./venv/bin/python run_client.py           # Linux / macOS
> ```

> [!NOTE]
> **What `run_client.py` does:** starts a small static web server for the `client/` folder on port `5000` and proxies every `/api/` request to the backend.

**Alternative — Bundled Mode (no second terminal):** with the backend running, just open **`http://localhost:8000/client/`** in your browser.

#### Client Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `CLIENT_PORT` | `5000` | Port the standalone client server listens on. |
| `BACKEND_URL` | `http://localhost:8000` | Backend target that `/api/` requests are proxied to. |

```powershell
# Windows (PowerShell) – client on port 5500 against a remote backend
$env:CLIENT_PORT = "5500"; $env:BACKEND_URL = "http://192.168.1.50:8000"; python run_client.py
```
```bash
# Linux
CLIENT_PORT=5500 BACKEND_URL=http://192.168.1.50:8000 python3 run_client.py
```

> [!NOTE]
> The client binds to `0.0.0.0`, so other machines on your network can reach the HUD at `http://<your-ip>:5000/`. The browser auto-open is best-effort and is silently skipped on headless machines.

### 3. Connect the Client to the Backend

The first time you open the HUD, tell it where the backend is and give it the access token. You only need to do this once:

1. **Open Settings** — click the **gear icon** (⚙️) at the top of the HUD to open **Interface Settings**.

2. **Find your access token** — look in the terminal where the backend is running (from step 1). On startup it prints a line like:
   ```
   Active Access Token: <your-token>
   ```
   Copy that value. (On the very first run the backend generates one automatically.)

3. **Enter the Backend API URL** — in the **Backend API URL** field, type your backend address, e.g. `http://localhost:8000`.
   > Leave it empty if you opened the HUD in **Bundled Mode** (`http://localhost:8000/client/`) — it then uses the current address automatically.

4. **Paste the access token** — put the value from step 2 into the **Freja Access Token** field (click the 👁 icon to reveal it while typing).
   > Client and backend on the **same machine via `localhost`** work even without a token; a token is **required for remote access**.

5. **Save** — click **SAVE CONFIGURATION**. The HUD is now connected and can talk to the backend.

6. **(Optional) Open the Admin Portal** — use the **Open the Backend Admin Portal** link in Settings to manage server-side API keys and integrations (Gemini, Garmin, Strava, Withings, Instagram, …).

---

## 🔁 Autostart on Boot

Both the backend (`server.py`) and the client (`run_client.py`) are long-running processes, so each needs its own autostart entry. The backend must be running before the client is useful, but the client tolerates a backend that is not up yet — it simply returns `502 Bad Gateway` on `/api/` calls until the backend answers.

> [!IMPORTANT]
> Always use **absolute paths** in autostart configuration, and point at the virtual environment's Python (`venv\Scripts\python.exe` / `venv/bin/python`) — not the bare `python` on `PATH`, which may resolve differently or not at all in a service context.

### 🪟 Windows Autostart

#### Option A: Startup folder (simplest, requires the user to be logged in)

This runs Freja when *you* log in, in your own desktop session — which is what you want for the Client HUD, since it opens a browser window and reports the **Client Activity Heartbeat**.

1. Create `start-freja.bat` in the project root:
   ```bat
   @echo off
   cd /d "C:\path\to\Freja-AI-assistant"
   start "FREJA Backend" /min "%CD%\venv\Scripts\python.exe" server.py
   timeout /t 5 /nobreak >nul
   start "FREJA Client" /min "%CD%\venv\Scripts\python.exe" run_client.py
   ```
   The `timeout` gives the backend a few seconds head start before the client (and the browser it opens) connects.

2. Press `Win + R`, type `shell:startup`, and press Enter. This opens your per-user Startup folder.

3. Place a shortcut to `start-freja.bat` in that folder (right-click the `.bat` → **Copy**, then right-click inside the Startup folder → **Paste shortcut**).

4. Reboot, or double-click the shortcut to test. Two minimized console windows should appear and the HUD should open in your browser.

Swap `python.exe` for `pythonw.exe` if you want no console windows at all — you then lose the live console log output.

#### Option B: Task Scheduler (runs before/without login, survives crashes)

Run PowerShell **as Administrator** from the project directory:

```powershell
$root   = "C:\path\to\Freja-AI-assistant"
$python = "$root\venv\Scripts\python.exe"

# Backend – starts at boot, no login needed
$action  = New-ScheduledTaskAction -Execute $python -Argument "server.py" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "FREJA Backend" -Action $action -Trigger $trigger -Settings $settings -User "SYSTEM" -RunLevel Highest

# Client – starts at logon, in your desktop session so the browser can open
$action  = New-ScheduledTaskAction -Execute $python -Argument "run_client.py" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "FREJA Client" -Action $action -Trigger $trigger -Settings $settings
```

Verify and control the tasks:
```powershell
Get-ScheduledTask -TaskName "FREJA *"
Start-ScheduledTask -TaskName "FREJA Backend"     # test without rebooting
Get-ScheduledTaskInfo -TaskName "FREJA Backend"   # LastRunTime / LastTaskResult
Unregister-ScheduledTask -TaskName "FREJA Client" -Confirm:$false   # remove
```

> [!TIP]
> **Windows troubleshooting:**
> - Task result `0x1`: usually a wrong `-WorkingDirectory` or a missing `venv`. Run the exact `ExecStart` command manually to see the traceback.
> - The **Windows OS Automation** (`run_windows_command`) and **Client Activity Heartbeat** features need the process in an interactive desktop session. Do not run the client as `SYSTEM`.
> - Port `5000` may be occupied by another app; set `CLIENT_PORT` (see the table above) or free the port with `Get-NetTCPConnection -LocalPort 5000`.

### 🐧 Linux Autostart (systemd)

The backend service is documented above under **Autostart Backend on Ubuntu (systemd Service)**. Add the client with a second unit.

1. **Create the service file:**
   ```bash
   sudo nano /etc/systemd/system/freja-client.service
   ```

2. **Add the following configuration** (replace `YOUR_USERNAME` from `whoami` and the path from `pwd`):
   ```ini
   [Unit]
   Description=F.R.E.J.A. Client HUD Server
   After=network.target freja-backend.service
   Wants=freja-backend.service

   [Service]
   Type=simple
   User=YOUR_USERNAME
   WorkingDirectory=/path/to/Freja-AI-assistant
   ExecStart=/path/to/Freja-AI-assistant/venv/bin/python run_client.py
   Environment=CLIENT_PORT=5000
   Environment=BACKEND_URL=http://localhost:8000
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```

3. **Enable and start both services:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now freja-backend freja-client
   ```

4. **Verify status and follow logs:**
   ```bash
   systemctl status freja-backend freja-client
   sudo journalctl -u freja-client -f
   ```

5. **Stop or disable:**
   ```bash
   sudo systemctl stop freja-client
   sudo systemctl disable freja-client
   ```

> [!TIP]
> **Linux troubleshooting:**
> - `Address already in use`: another process holds port 5000. Check with `sudo ss -ltnp | grep 5000` and change `CLIENT_PORT` in the unit file.
> - On a headless server the client's browser auto-open silently does nothing — open `http://<server-ip>:5000/` from your own machine instead.
> - Running Freja on a desktop Linux session where you *want* the browser to open? Use a **user** service instead: put the same unit in `~/.config/systemd/user/freja-client.service` (drop the `User=` line), then run `systemctl --user enable --now freja-client`.

---

## 🔑 API Keys Configuration

To unlock F.R.E.J.A.'s full cognitive capabilities, configure your credentials inside the Settings modal:

1. **Google Gemini API Key** (Required for intelligence):
   - Establishes connection to the `gemini-2.5-flash` model.
   - *If no key is configured, F.R.E.J.A. will automatically operate in a restricted Offline Mock Mode.*
2. **Mem0 API Key** (Optional):
   - Integrates long-term semantic memory synchronized with the Mem0.ai cloud platform.
   - *If no key is provided, the engine gracefully falls back to a browser-based **Virtual Local Sandbox** (storing facts locally inside LocalStorage).*
3. **ElevenLabs API Key** (Optional):
   - Synthesizes highly realistic, lifelike human neural voices.
   - *If no key is provided, the assistant uses your computer's built-in native speech synthesis voice.*

---

## ⚡ Core Features

* 🌀 **Holographic Arc Reactor Core**: An interactive HTML5 Canvas visualizer that pulses, rotates, and displays real-time frequency equalizers reflecting your active microphone capture or assistant speech.
* 🎙️ **Hands-free Voice Controls**: Speak naturally after activating the microphone. F.R.E.J.A. automatically pauses speech recognition while speaking to prevent capturing its own voice loop.
* 👁️ **Neural Optics Scanner**: Choose your webcam directly from the HUD panel. F.R.E.J.A. captures frames in the background to analyze objects, expressions, or visual queries via Gemini's multimodal vision model.
* 🧠 **Neural Memory Vault**: Remembers personal details, names, cities, and habits across sessions. Open the Vault modal (brain icon in the header) to view, add, or purge engram cards manually.
* 🏃 **Health & Fitness Dashboards**: Synchronize Garmin Fit metrics, Strava activity logs, Withings measurements, and Google Calendar events into an AI-powered Personal Trainer dashboard.
* 🧑‍🏫 **PT Profile & Onboarding**: Fill in your training goal, target event/date, fitness level, weekly availability, location and health baselines directly in the HUD's Personal Trainer panel — the coach's assumptions become visible and editable, saved via `PUT /api/trainer/profile`.
* 🏋️ **Strength Progression**: Strength sessions carry a structured `exercises` block (name, sets, reps, target weight/RPE). Log completed loads in the HUD (`/api/trainer/strength/log`); the coach reads the latest load per exercise and applies progressive overload week to week.
* 📉 **Self-Updating Health Baselines**: The resting-HR, sleep and HRV baselines used for trend alerts recompute automatically from a rolling 28-day window after each Garmin sync (self-limited to a weekly cadence), so the deviation alerts stay accurate with zero manual upkeep. A manual `POST /api/trainer/baselines/refresh` is also available.
* 📸 **Instagram Social Publishing**: Link an Instagram Business/Creator account via Meta OAuth, then have F.R.E.J.A. publish photos or reels/videos, read the latest feed, and fetch or reply to post comments — all through the official Instagram Graph API.
* 🎨 **Accent Themes**: Swap between multiple cyberpunk neon color themes in real-time.
* 🎛️ **Terminal Console Log**: Displays diagnostic startup indicators, network transaction payloads, and audio/webcam links in a live feed terminal at the bottom-right.
* 📡 **Remote GitHub Updates**: Tell F.R.E.J.A. to "uppdatera dig" (or send it via Telegram) to pull the latest changes from GitHub and automatically restart.
* 🛠️ **Codex Self-Analysis**: Request a codebase review ("analysera din kod") to trigger codebase auditing, display summary findings, and generate a downloadable Markdown report.
* 🔒 **Encrypted & Masked Credentials**: Sensitive credentials (API keys, passwords, and tokens) are encrypted at rest in SQLite and masked (`••••••••`) when populated on the UI, preventing local plain-text leakage in browser `localStorage`.
* 💻 **Windows OS Automation**: Control the host computer natively (launch apps, open folders, launch URLs, run safe cmd/PowerShell commands) using the `run_windows_command` tool.
* 📡 **Client Activity Heartbeat**: Detects if the browser HUD client is active. You can ask the Telegram bot which computer the client is currently running on.

---

## 📂 Codebase Architecture

The application separates the client frontend from focused backend modules:

```
├── client/                     # Frontend Static Files
│   ├── index.html              # HUD panel layouts, diagnostics grids, and modals
│   ├── google_callback.html    # Cross-origin Google Calendar OAuth redirect handler
│   ├── style.css               # Glassmorphism grids, animations, and theme variables
│   ├── app.js                  # Browser-side UI orchestration and DOM bindings
│   ├── gemini.js               # Gemini client and multimodal context handling
│   ├── memory.js               # Mem0 integration and local memory fallback
│   ├── speech.js               # Speech-to-Text and Text-to-Speech engines
│   ├── camera.js               # Webcam capture modules
│   ├── diagnostics.js          # HUD diagnostics indicators
│   ├── sound.js                # UI sound synthesis
│   ├── theme.js                # System theme switcher
│   ├── visualizer.js           # Holographic Arc Reactor visualizer
│   └── js/                     # UI components modules
│       ├── event-bus.js        # Decoupled Pub/Sub event bus & state manager
│       ├── ui-init.js          # UI initialization
│       ├── ui-events.js        # Event listener bindings
│       ├── ui-tools.js         # Tool call & terminal log rendering
│       └── ui-dashboards.js    # Health & Fitness dashboard visualizations
├── server.py                   # FastAPI backend server launcher (optional static server)
├── backend/                    # Python Backend Application
│   ├── config.py               # Runtime paths and environment configuration
│   ├── database.py             # SQLite schema initialization and migrations
│   ├── middleware/             # Backend middleware (CORS, Auth)
│   ├── routes/                 # Domain-specific HTTP handlers (Garmin, Strava, Calendar, Trainer, etc.)
│   └── services/               # External integration services & Tool Registry
└── tests/                      # Backend route regression test suite
```

Backend routes are registered centrally but implemented in focused domain modules. This keeps the server entry point small and makes route behavior independently testable.

### 🌍 Language Convention

**All source text is English. Freja answers the user in Swedish.**

Comments, docstrings, log lines, exception messages, HTTP error details, Gemini tool descriptions, and every piece of UI copy in the HUD and admin panel are written in English. Freja still replies in Swedish because the *system prompts* say so — never because the surrounding code is Swedish. Those prompts live in [gemini.js](client/gemini.js) (HUD), [telegram_service.py](backend/services/telegram_service.py) (Telegram bot), [trainer.py](backend/routes/trainer.py) (coach), [learning_service.py](backend/services/learning_service.py), and [codex_service.py](backend/services/codex_service.py) (audit report). Each is English prose containing an explicit "answer in Swedish" instruction.

Some Swedish is deliberately kept because translating it would change behavior. It is commented in place; the categories are:

| Category | Example | Why it stays |
| --- | --- | --- |
| Text Freja speaks or writes to the user | `speech.speak("Nedladdningen ... är klar")` | It *is* Freja's Swedish answer. |
| Keywords matched against user speech | `includes("avbryt")`, vision keywords in [app.js](client/app.js) | The user speaks Swedish; translating breaks voice control. |
| Values persisted in the database | `Löpning`, `Styrketräning`, `Övertränad` | Shown in the HUD and matched by pace/recovery logic. Changing them needs a data migration. |
| Weekday names in generated plans | `Måndag` … `Söndag` | `book_plan_to_calendar` parses them back into dates. |
| Third-party UI strings | `"Tillåt alla cookies"` in [facebook_service.py](backend/services/facebook_service.py) | Facebook's own button labels, matched by visible text. |

When adding code, follow the same rule: write it in English, and if you need Freja to say something in Swedish, put that instruction in the prompt rather than in the code.

### Running Tests

Run backend regression tests with:

```bash
pytest -v tests
```

---

## 🔒 Codex Sandbox (AI Code Execution)

F.R.E.J.A.'s Codex tools can run AI-generated Python code and shell commands. How isolated that execution is depends on the `FREJA_CODEX_SANDBOX` environment variable:

* **`auto`** (default): Runs code inside a throwaway Docker container (no network, read-only root filesystem, capped memory/CPU/processes) if Docker is installed — otherwise falls back to local execution.
* **`docker`**: Always requires Docker; execution fails if the Docker daemon is unavailable.
* **`local`**: Runs directly on the host. Isolation then relies solely on AST/blocklist validation plus POSIX resource limits — **notably weaker**, and on Windows the resource limits are unavailable entirely. Installing Docker is strongly recommended.

The container image defaults to `python:3.12-alpine` and can be overridden with `FREJA_CODEX_DOCKER_IMAGE`. Every Codex execution (code, git operations, auto-fixes) is recorded in the `codex_audit_log` table in the SQLite database. The `codex_run_and_fix` tool always writes a `.codex_backup` copy of the target file before letting the AI rewrite it, and `git push` always requires a fresh confirmation in the UI — it can never be permanently allowed.

---

## 🛠️ Troubleshooting & Tips

* **Webcam Streams Fail / Overconstrained Error**:
  - We have implemented soft constraints using `ideal` specifications to eliminate the browser `OverconstrainedError`. If your camera does not initialize, check your browser's address bar to ensure camera permissions have been granted.
* **Microphone Disconnects or Pauses**:
  - Some browsers suspend microphonic listeners if the tab remains inactive in the background. Simply click the microphone button on the HUD to reconnect the interface.
