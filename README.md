# 🌌 F.R.E.J.A. // Fully Responsive Electronic Judicial Assistant

> **Holographic Neural Interface powered by Gemini AI, Web Audio, & Web Speech APIs**

Welcome to **F.R.E.J.A. (Freja)** – a premium, cyberpunk-inspired AI assistant equipped with advanced voice controls, a holographic particle reactor, long-term neural memory, and real-time multimodal optical scanning (webcam support).

F.R.E.J.A. is built using pure modern web standards (Vanilla HTML5, CSS3, ES6 Javascript) and has been fully modularized into clean, independent files to ensure maximum performance, maintainability, and code readability.

---

## 🚀 Quick Start: Running the Project

Because F.R.E.J.A. accesses your webcam, microphone, and external AI APIs, we highly recommend running the application via a local web server (rather than double-clicking `index.html` directly). This guarantees that browser security protocols allow media capturing streams to initialize correctly.

### Step 1: Spin Up the Neural Server
Open your terminal, navigate to the project directory, and run the Python backend server:

```bash
python3 server.py
```

This starts the web server on port `8000` and initializes the secure SQLite database (`keys.db`) for API keys persistence.

### Step 2: Access the HUD Interface
Open your web browser (Chrome, Edge, or Safari are recommended for optimal Speech Recognition support) and go to:
```
http://localhost:8000/
```

### Step 3: Initialize the Assistant
1. Click the large, pulsing **STARTA GRÄNSSNITTET** (START INTERFACE) overlay button to unlock the browser's audio permissions and ignite the reactor.
2. Click the **gear icon** (Settings) in the top-right header to configure your API keys.

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

The application separates the browser interface from focused backend modules:

```
├── index.html                  # HUD panel layouts, diagnostics grids, and modals
├── style.css                   # Glassmorphism grids, animations, and theme variables
├── app.js                     # Browser-side UI orchestration and DOM bindings
├── gemini.js                  # Gemini client and multimodal context handling
├── memory.js                  # Mem0 integration and local memory fallback
├── speech.js                  # Speech-to-Text and Text-to-Speech engines
├── save_session.py            # Headful CLI Playwright session setup utility
├── tools/                     # Browser-side assistant tool declarations
├── server.py                  # Minimal backend composition and startup entry point
├── backend/
│   ├── config.py              # Runtime paths and environment configuration
│   ├── database.py            # SQLite schema initialization and migrations
│   ├── request_handler.py     # Central HTTP route dispatcher
│   ├── routes/                # Domain-specific HTTP handlers
│   │   ├── settings.py
│   │   ├── search.py
│   │   ├── garmin.py
│   │   ├── strava.py
│   │   ├── facebook.py        # Scraping controllers (abort, download status, trigger)
│   │   └── withings.py
│   └── services/              # External integration services
│       └── facebook_service.py # Playwright background browser automation scraper
└── tests/                     # Backend route regression tests
```

Backend routes are registered centrally but implemented in focused domain modules. This keeps the server entry point small and makes route behavior independently testable.

Install the Python runtime dependencies before starting the server:

```bash
python3 -m pip install -r requirements.txt
```

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
