# 🌌 F.R.E.J.A. // Fully Responsive Electronic Judicial Assistant

> **Holographic Neural Interface powered by Gemini AI, Web Audio, & Web Speech APIs**

Welcome to **F.R.E.J.A. (Freja)** – a premium, cyberpunk-inspired AI assistant equipped with advanced voice controls, a holographic particle reactor, long-term neural memory, and real-time multimodal optical scanning (webcam support).

F.R.E.J.A. is built using pure modern web standards (Vanilla HTML5, CSS3, ES6 Javascript) and has been fully modularized into clean, independent files to ensure maximum performance, maintainability, and code readability.

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
   This installs the Chromium browser binary required for the Facebook scraper:
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
   Install Chromium and any missing system libraries needed to run the browser in GUI mode:
   ```bash
   playwright install chromium
   playwright install-deps chromium
   ```

---

## 🌐 Client-Backend Architecture

F.R.E.J.A. is split into a **Frontend Client** and a **FastAPI Backend Server**. This architecture allows you to run the backend on a remote server/VPS while running the client locally or on a separate static web hosting provider.

### CORS Enabled
The backend includes Cross-Origin Resource Sharing (CORS) middleware to allow client connections from different hosts, ports, or domains.

---

## 🚀 Running the Project

Because F.R.E.J.A. accesses your webcam, microphone, and external APIs, you must run the client via a web server (rather than opening the `index.html` file directly). This guarantees that browser security protocols allow media capturing streams to initialize.

### Option A: Integrated Local Setup (Simultaneous)

If you run everything on the same machine, the backend server can automatically serve the client files from the `client/` subdirectory.

1. **Start the Backend Server:**
   Ensure your virtual environment is active, then run:
   ```bash
   python server.py
   ```
2. **Access the HUD Interface:**
   Navigate your browser to `http://localhost:8000/`.

### Option B: Separated Setup (Remote Backend + Static Client)

If you run the backend on a server and the client elsewhere:

1. **Deploy & Start the Backend Server:**
   Deploy the code to your server and start the backend:
   ```bash
   python server.py
   ```
   Ensure port `8000` is open or behind a reverse proxy.

2. **Serve the Client (Frontend):**
   Serve the contents of the `client/` directory using any static web server (Nginx, Apache, or a simple python script):
   ```bash
   python -m http.server 5000 --directory client
   ```
3. **Connect the Client to the Backend:**
   - Open your browser to the client URL (e.g. `http://localhost:5000/`).
   - Click the **gear icon** (Settings) in the top-right header.
   - Enter your backend URL (e.g., `http://your-server-ip:8000` or `https://your-domain.com`) in the **Backend API URL** field and save settings.
   - The client will automatically connect and authenticate using your security token.

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
* 🎨 **Accent Themes**: Swap between multiple cyberpunk neon color themes in real-time.
* 🎛️ **Terminal Console Log**: Displays diagnostic startup indicators, network transaction payloads, and audio/webcam links in a live feed terminal at the bottom-right.
* 👥 **Facebook Photo Downloader & Scraper Tool**:
  - Automatically logins and scrapes high-resolution photos from specific Facebook profiles directly from conversational triggers (e.g., *"Ladda ner bilder från..."*).
  - **Pre-authenticated Session Persistence**: Includes a standalone utility `python3 save_session.py` to log in once via Playwright and save session cookies/local storage to `facebook_state.json`, bypassing login walls.
  - **Dynamic Context Cleaning (Anti-Bias)**: Implements dynamic conversation history filtering in the frontend client. Whenever a user submits a download query, historical responses claiming lack of permissions or partial results are purged from active memory to keep the LLM focused on running the tool.
  - **Cancelable Background Job**: Abort active download queues dynamically via explicit chat messages (e.g., *"Sluta ladda ner"* / *"Avbryt"*) or direct UI button controls.
  - **Resilient Multi-Strategy Scrolling**: Combines smooth window scrolling, recursive scrollable container traversal, and simulated keyboard commands (`End`/`PageDown`) to unlock infinite scroll content and capture large photo sets.
  - **Auto-save at Termination**: Updated session states/cookies are automatically saved in the `finally` block before closing the headless browser, protecting authentication from expiring prematurely.

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
│       ├── ui-init.js
│       ├── ui-events.js
│       ├── ui-tools.js
│       └── ui-dashboards.js
├── server.py                   # FastAPI backend server launcher (optional static server)
├── save_session.py             # Headful CLI Playwright session setup utility
├── backend/                    # Python Backend Application
│   ├── config.py               # Runtime paths and environment configuration
│   ├── database.py             # SQLite schema initialization and migrations
│   ├── middleware/             # Backend middleware (CORS, Auth)
│   ├── routes/                 # Domain-specific HTTP handlers
│   └── services/               # External integration services
└── tests/                      # Backend route regression tests
```

Backend routes are registered centrally but implemented in focused domain modules. This keeps the server entry point small and makes route behavior independently testable.



Run backend regression tests with:

```bash
python3 -m unittest discover -v
```

---

## 🛠️ Troubleshooting & Tips

* **Webcam Streams Fail / Overconstrained Error**:
  - We have implemented soft constraints using `ideal` specifications to eliminate the browser `OverconstrainedError`. If your camera does not initialize, check your browser's address bar to ensure camera permissions have been granted.
* **Microphone Disconnects or Pauses**:
  - Some browsers suspend microphonic listeners if the tab remains inactive in the background. Simply click the microphone button on the HUD to reconnect the interface.
* **Scraper Fails to Access Profile**:
  - Run `python3 save_session.py` in your terminal to complete a headful login sequence on Facebook. Playwright will capture the cookies and save them, allowing the background assistant to run smoothly.
