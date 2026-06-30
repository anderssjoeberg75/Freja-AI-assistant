/**
 * F.R.E.J.A. - Vocal Recognition & Synthesis (STT/TTS) Engine
 * 
 * Provides webkitSpeechRecognition triggers for hands-free operations
 * and synthesizes voice replies using ElevenLabs neural API models (if key provided)
 * or falls back to native Web SpeechSynthesis voices.
 */

class FrejaSpeechEngine {
    constructor() {
        this.recognition = null;
        this.synth = window.speechSynthesis;
        this.lang = 'sv-SE'; // Standard language (Swedish)
        this.isListening = false;
        this.continuous = true;
        this.voiceIndex = null;
        this.rate = 1.0;
        this.pitch = 1.0;
        this.autoSpeak = true;
        
        // ElevenLabs API Configurations
        this.elevenApiKey = "";
        this.elevenVoice = "21m00Tcm4TlvDq8ikWAM"; // Default: Rachel voice
        this.elevenCustomVoice = "";
        this.activeElevenNode = null;
        
        // Orchestrator callbacks
        this.speechEndCallback = null;
        this.speechStartCallback = null;
        this.transcriptCallback = null;
        this.voiceUpdateCallback = null;
        
        this.initRecognition();
        this.initVoices();
    }

    /**
     * Initializes the Web Speech SpeechRecognition subsystem.
     */
    initRecognition() {
        const SpeechRecognitionClass = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognitionClass) {
            console.error("[SPEECH] Speech Recognition not supported in this browser");
            return;
        }

        this.recognition = new SpeechRecognitionClass();
        this.recognition.continuous = true;
        this.recognition.interimResults = false;
        this.recognition.lang = this.lang;

        this.recognition.onstart = () => {
            this.isListening = true;
            console.log("[SPEECH] Recognition started:", this.lang);
            if (this.speechStartCallback) this.speechStartCallback();
        };

        this.recognition.onresult = (event) => {
            const lastIdx = event.results.length - 1;
            const transcript = event.results[lastIdx][0].transcript.trim();
            const confidence = event.results[lastIdx][0].confidence;
            
            console.log(`[SPEECH] Heard: "${transcript}" (Confidence: ${confidence.toFixed(2)})`);
            if (this.transcriptCallback && transcript) {
                this.transcriptCallback(transcript);
            }
        };

        this.recognition.onerror = (event) => {
            console.error("[SPEECH] Recognition error:", event.error);
            if (event.error === 'not-allowed') {
                this.isListening = false;
                if (this.speechEndCallback) this.speechEndCallback();
            }
        };

        this.recognition.onend = () => {
            console.log("[SPEECH] Recognition engine closed");
            // Auto-restart stream if user is still actively keeping the mic active
            if (this.isListening) {
                try {
                    this.recognition.start();
                    console.log("[SPEECH] Automatically restarted recognition");
                } catch (e) {
                    console.warn("[SPEECH] Failed to restart recognition:", e);
                }
            }
        };
    }

    /**
     * Fetches and caches speech synthesizers from the browser.
     */
    initVoices() {
        if (!this.synth) return;
        
        const load = () => {
            this.voices = this.synth.getVoices();
            console.log(`[SPEECH] Loaded ${this.voices.length} synthesized voices`);
            if (this.voiceUpdateCallback) this.voiceUpdateCallback(this.voices);
        };

        load();
        if (this.synth.onvoiceschanged !== undefined) {
            this.synth.onvoiceschanged = load;
        }
    }

    /**
     * Updates active recognition language locale (e.g. 'sv-SE', 'en-US').
     */
    setLanguage(lang) {
        this.lang = lang;
        if (this.recognition) {
            const wasRunning = this.isListening;
            this.stopListening();
            this.recognition.lang = lang;
            if (wasRunning) {
                // Short timeout delay gives browser subsystem sufficient context release time
                setTimeout(() => this.startListening(), 300);
            }
        }
    }

    /**
     * Starts listening to audio input streams.
     */
    startListening() {
        if (!this.recognition) return;
        this.isListening = true;
        try {
            this.recognition.start();
        } catch (e) {
            // Already active
        }
    }

    /**
     * Shuts off microphone capturing streams.
     */
    stopListening() {
        this.isListening = false;
        if (this.recognition) {
            try {
                this.recognition.stop();
            } catch (e) {
                // Already inactive
            }
        }
    }

    /**
     * Master speak trigger. Chooses ElevenLabs if credentialed, or falls back to SpeechSynthesis natively.
     */
    speak(text) {
        if (!this.autoSpeak) {
            return Promise.resolve();
        }

        // Cancel any active ElevenLabs stream before speaking a new message
        if (this.activeElevenNode) {
            try {
                this.activeElevenNode.stop();
            } catch (e) {}
            this.activeElevenNode = null;
        }

        // Suspend speech recognition so F.R.E.J.A. does not hear its own voice feedback
        const wasListening = this.isListening;
        if (wasListening) {
            this.recognition.abort();
        }

        // Stop any currently running native voice
        if (this.synth) {
            this.synth.cancel();
        }

        // Strip formatting characters from prompt text
        let cleanText = text.replace(/[*_`#]/g, '');
        
        // Translate "m/s" to "meter per sekund" for proper spoken pronunciation
        cleanText = cleanText.replace(/(\b|(?<=\d))m\s*\/\s*s\b/gi, 'meter per sekund');

        // Translate decimal numbers (e.g. 25.5 -> 25 komma 5) for natural Swedish speech
        cleanText = cleanText.replace(/\b(\d+)\.(\d+)\b/g, '$1 komma $2');

        // Translate "°C" to "grader celsius" for proper spoken pronunciation
        cleanText = cleanText.replace(/\s*°\s*C\b/gi, ' grader celsius');

        if (this.elevenApiKey) {
            return this.speakElevenLabs(cleanText, wasListening);
        }

        return this.speakNative(cleanText, wasListening);
    }

    /**
     * Fetches and feeds raw multi-lingual neural voice buffers from ElevenLabs.
     */
    speakElevenLabs(text, wasListening) {
        return new Promise((resolve) => {
            console.log("[ELEVENLABS] Requesting neural speech synthesis...");
            document.getElementById('vocal-status').textContent = "SPEAKING";
            document.getElementById('vocal-status').classList.add('active');
            if (window.visualizer) {
                window.visualizer.state = 'SPEAKING';
            }

            const voiceId = this.elevenVoice === 'custom' ? this.elevenCustomVoice : this.elevenVoice;
            const endpoint = `/api/elevenlabs/tts/${voiceId}`;

            fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    text: text,
                    model_id: "eleven_multilingual_v2",
                    voice_settings: {
                        stability: 0.55,
                        similarity_boost: 0.75
                    }
                })
            })
            .then(async (response) => {
                if (!response.ok) {
                    const errText = await response.text();
                    throw new Error(`HTTP ${response.status}: ${errText}`);
                }
                return response.arrayBuffer();
            })
            .then(async (arrayBuffer) => {
                if (!soundSynth.initialized) {
                    soundSynth.init();
                }
                soundSynth.resume();

                // Decode buffered array directly to soundSynth channel node
                const audioBuffer = await soundSynth.ctx.decodeAudioData(arrayBuffer);
                const source = soundSynth.ctx.createBufferSource();
                source.buffer = audioBuffer;

                // Connect to visualizer AND main outputs!
                source.connect(soundSynth.analyser);
                source.connect(soundSynth.ctx.destination);

                const finishSpeaking = () => {
                    console.log("[ELEVENLABS] Audio playing finished");
                    document.getElementById('vocal-status').textContent = wasListening ? "LISTENING" : "STANDBY";
                    if (!wasListening) {
                        document.getElementById('vocal-status').classList.remove('active');
                    }
                    if (window.visualizer) {
                        window.visualizer.state = wasListening ? 'LISTENING' : 'SLEEPING';
                    }
                    if (wasListening) {
                        setTimeout(() => this.startListening(), 200);
                    }
                    resolve();
                };

                source.onended = finishSpeaking;
                source.start(0);
                this.activeElevenNode = source;
            })
            .catch((e) => {
                console.error("[ELEVENLABS] Synthesis failed:", e);
                if (window.uiController) {
                    window.uiController.writeLog(`ELEVENLABS ERROR: ${e.message.substring(0, 45)}...`, 'err');
                }
                
                // Fall back to native voice synthesis if API error occurred
                console.log("[ELEVENLABS] Falling back to native browser speech synthesis");
                this.speakNative(text, wasListening).then(resolve);
            });
        });
    }

    /**
     * Synthesizes responses using the built-in browser SpeechSynthesisUtterance.
     */
    speakNative(text, wasListening) {
        return new Promise((resolve) => {
            if (!this.synth) {
                resolve();
                return;
            }

            const utterance = new SpeechSynthesisUtterance(text);
            utterance.lang = this.lang;
            utterance.rate = this.rate;
            utterance.pitch = this.pitch;

            // Load selected customized voice Index
            if (this.voiceIndex !== null && this.voices[this.voiceIndex]) {
                utterance.voice = this.voices[this.voiceIndex];
            } else {
                // Heuristics matcher: Pick natural online voices if available
                const voicesFiltered = this.voices.filter(v => v.lang.startsWith(this.lang.substring(0, 2)));
                if (voicesFiltered.length > 0) {
                    voicesFiltered.sort((a, b) => {
                        const score = (v) => {
                            const name = v.name.toLowerCase();
                            let s = 0;
                            if (name.includes('natural')) s += 5;
                            if (name.includes('online')) s += 4;
                            if (name.includes('neural')) s += 3;
                            if (name.includes('google')) s += 2;
                            if (name.includes('microsoft')) s += 1;
                            return s;
                        };
                        return score(b) - score(a);
                    });
                    utterance.voice = voicesFiltered[0];
                }
            }

            utterance.onstart = () => {
                console.log("[SPEECH] Speaking response natively...");
                document.getElementById('vocal-status').textContent = "SPEAKING";
                document.getElementById('vocal-status').classList.add('active');
                if (window.visualizer) {
                    window.visualizer.state = 'SPEAKING';
                }
            };

            const finishSpeaking = () => {
                console.log("[SPEECH] Native speaking finished");
                document.getElementById('vocal-status').textContent = wasListening ? "LISTENING" : "STANDBY";
                if (!wasListening) {
                    document.getElementById('vocal-status').classList.remove('active');
                }
                
                if (window.visualizer) {
                    window.visualizer.state = wasListening ? 'LISTENING' : 'SLEEPING';
                }

                if (wasListening) {
                    setTimeout(() => {
                        this.startListening();
                    }, 200);
                }
                resolve();
            };

            utterance.onend = finishSpeaking;
            utterance.onerror = (e) => {
                console.warn("[SPEECH] Native utterance error", e);
                finishSpeaking();
            };

            this.synth.speak(utterance);
        });
    }
}
