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
* 🎨 **Accent Themes**: Swap between multiple cyberpunk neon color themes in real-time:
  - `Cyan Accent` (Default)
  - `Emerald Uplink` (Green)
  - `Crimson Protocol` (Red)
  - `Amber Diagnostic` (Amber/Gold)
* 🎛️ **Terminal Console Log**: Displays diagnostic startup indicators, network transaction payloads, and audio/webcam links in a live feed terminal at the bottom-right.

---

## 📂 Codebase Architecture

The application has been modularized into 6 clean, decoupled Javascript files:

```
├── index.html          # HUD panel layouts, diagnostics grids, and modals
├── style.css           # Glassmorphism grids, sweeping laser animations, and theme variables
├── app.js              # Central orchestrator (FrejaUIController) & DOM bindings
├── sound.js            # Procedural synthesizer (Web Audio API) for chimes and alert sweeps
├── visualizer.js       # HTML5 Canvas Arc Reactor physics rendering loops
├── speech.js           # Speech-to-Text (STT) and ElevenLabs/Native Text-to-Speech (TTS)
├── memory.js           # Mem0 API integration & Local Storage Virtual Sandbox Fallback
└── gemini.js           # Gemini API client & webcam Base64 frame snapshot encoders
```

*Every file contains comprehensive, structured documentation and comments in English to support seamless extensions.*

---

## 🛠️ Troubleshooting & Tips

* **Webcam Streams Fail / Overconstrained Error**:
  - We have implemented soft constraints using `ideal` specifications to eliminate the browser `OverconstrainedError`. If your camera does not initialize, check your browser's address bar to ensure camera permissions have been granted.
* **Microphone Disconnects or Pauses**:
  - Some browsers suspend microphonic listeners if the tab remains inactive in the background. Simply click the microphone button on the HUD to reconnect the interface.
