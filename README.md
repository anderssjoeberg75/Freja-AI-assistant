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

2. **Add the following configuration:** (replace `/home/user/Freja-AI-assistant` and `user` with your actual path and username)
   ```ini
   [Unit]
   Description=F.R.E.J.A. Neural Backend Service
   After=network.target

   [Service]
   Type=simple
   User=user
   WorkingDirectory=/home/user/Freja-AI-assistant
   ExecStart=/home/user/Freja-AI-assistant/venv/bin/python server.py
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

---

## 🌐 Client-Backend Architecture

F.R.E.J.A. is strictly divided into a **Frontend Client HUD** and a **FastAPI Backend Server & Admin Control Panel**:
- **Backend Admin GUI (`http://localhost:8000/`)**: Served by `server.py`. Contains strictly server-side configurations, API key database management, integration credentials (Telegram, Garmin, Strava, Withings, Google Calendar), backend tool permission toggles, and live server status.
- **Client HUD (`http://localhost:5000/` or `http://localhost:8000/client/`)**: Holographic AI Assistant interface containing voice controls, speech synthesis, optics camera scanner, Arc Reactor visualizer, personal health & fitness dashboards, and neural memory vault.

---

## 🚀 Running the Project

### 1. Start the Backend Server & Admin Panel
Ensure your virtual environment is active, then run:
```bash
python server.py
```
- Open **Backend Admin Control Panel**: `http://localhost:8000/`

### 2. Start the Client Assistant HUD
You can run the Client HUD standalone or via bundled mode:

* **Standalone Mode (Recommended):**
  Run the dedicated client launcher:
  ```bash
  python run_client.py
  ```
  Open **Client HUD Interface**: `http://localhost:5000/`

* **Bundled Mode:**
  Access the client directly from the backend server at: `http://localhost:8000/client/`

3. **Connect Client to Backend:**
   - In the Client HUD, click the **gear icon** (Settings).
   - Enter your **Backend API URL** (e.g. `http://localhost:8000`) and **Freja Access Token**.
   - Use the **Backend Admin Portal** link in settings to manage server-side API keys and integration settings.

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
* 🎨 **Accent Themes**: Swap between multiple cyberpunk neon color themes in real-time.
* 🎛️ **Terminal Console Log**: Displays diagnostic startup indicators, network transaction payloads, and audio/webcam links in a live feed terminal at the bottom-right.

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

### Running Tests

Run backend regression tests with:

```bash
pytest -v tests
```

---

## 🛠️ Troubleshooting & Tips

* **Webcam Streams Fail / Overconstrained Error**:
  - We have implemented soft constraints using `ideal` specifications to eliminate the browser `OverconstrainedError`. If your camera does not initialize, check your browser's address bar to ensure camera permissions have been granted.
* **Microphone Disconnects or Pauses**:
  - Some browsers suspend microphonic listeners if the tab remains inactive in the background. Simply click the microphone button on the HUD to reconnect the interface.
