/**
 * F.R.E.J.A. - Procedural Sound Synthesizer Node
 * 
 * This module leverages the Web Audio API to procedurally generate holographic
 * synthesizer feedback, interface hums, startup sweeps, chimes, and failure warning chirps.
 * It operates without relying on pre-recorded external audio assets.
 */

class FrejaSoundSynth {
    constructor() {
        this.ctx = null;
        this.analyser = null;
        this.microphoneStream = null;
        this.initialized = false;
    }

    /**
     * Initializes the Web Audio context and builds the master analyser node.
     * Must be triggered by a direct user interaction due to modern browser autoplay policies.
     */
    init() {
        if (this.initialized) return;
        try {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            this.ctx = new AudioContextClass();
            
            // Build the master frequency analyser node used for the HUD visualization
            this.analyser = this.ctx.createAnalyser();
            this.analyser.fftSize = 256;
            this.analyser.connect(this.ctx.destination);
            
            this.initialized = true;
            console.log("[AUDIO] Synth Engine Initialized");
        } catch (e) {
            console.error("[AUDIO] Failed to initialize Web Audio context", e);
        }
    }

    /**
     * Resumes the AudioContext if it has been suspended by the browser.
     */
    resume() {
        if (this.ctx && this.ctx.state === 'suspended') {
            this.ctx.resume();
        }
    }

    /**
     * Plays a high-frequency click synthesizer chime for general button/HUD clicks.
     */
    playClick() {
        if (!this.initialized) return;
        this.resume();
        
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();
        
        osc.connect(gain);
        gain.connect(this.ctx.destination);
        
        osc.type = 'sine';
        osc.frequency.setValueAtTime(1500, this.ctx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(100, this.ctx.currentTime + 0.05);
        
        gain.gain.setValueAtTime(0.08, this.ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + 0.05);
        
        osc.start();
        osc.stop(this.ctx.currentTime + 0.06);
    }

    /**
     * Plays a deep bass swell and rising high-frequency sweep during central core boot-up.
     */
    playStartupSweep() {
        if (!this.initialized) return;
        this.resume();

        const duration = 1.2;
        const now = this.ctx.currentTime;
        
        // Channel A: Bass Power Hum (Sawtooth waveform)
        const osc1 = this.ctx.createOscillator();
        const gain1 = this.ctx.createGain();
        osc1.connect(gain1);
        gain1.connect(this.ctx.destination);
        osc1.type = 'sawtooth';
        osc1.frequency.setValueAtTime(60, now);
        osc1.frequency.linearRampToValueAtTime(120, now + duration);
        gain1.gain.setValueAtTime(0.0, now);
        gain1.gain.linearRampToValueAtTime(0.12, now + 0.4);
        gain1.gain.exponentialRampToValueAtTime(0.001, now + duration);
        
        // Channel B: Futuristic High Sweep (Sine waveform)
        const osc2 = this.ctx.createOscillator();
        const gain2 = this.ctx.createGain();
        osc2.connect(gain2);
        gain2.connect(this.ctx.destination);
        osc2.type = 'sine';
        osc2.frequency.setValueAtTime(300, now);
        osc2.frequency.exponentialRampToValueAtTime(2200, now + duration - 0.2);
        gain2.gain.setValueAtTime(0.0, now);
        gain2.gain.linearRampToValueAtTime(0.08, now + 0.3);
        gain2.gain.exponentialRampToValueAtTime(0.001, now + duration);

        osc1.start();
        osc2.start();
        osc1.stop(now + duration);
        osc2.stop(now + duration);
    }

    /**
     * Plays an elegant arpeggiated tri-tone chime for success states or received notifications.
     */
    playNotify() {
        if (!this.initialized) return;
        this.resume();

        const now = this.ctx.currentTime;
        const notes = [880, 1318.5, 1760]; // A5, E6, A6 (Futuristic major chime)
        
        notes.forEach((freq, index) => {
            const osc = this.ctx.createOscillator();
            const gain = this.ctx.createGain();
            
            osc.connect(gain);
            gain.connect(this.ctx.destination);
            
            osc.type = 'sine';
            osc.frequency.setValueAtTime(freq, now + index * 0.08);
            
            gain.gain.setValueAtTime(0, now + index * 0.08);
            gain.gain.linearRampToValueAtTime(0.06, now + index * 0.08 + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.001, now + index * 0.08 + 0.3);
            
            osc.start(now + index * 0.08);
            osc.stop(now + index * 0.08 + 0.35);
        });
    }

    /**
     * Plays a double flat buzzed pulse warning for failures, access denies, or warnings.
     */
    playError() {
        if (!this.initialized) return;
        this.resume();

        const now = this.ctx.currentTime;
        
        [0, 0.15].forEach((delay) => {
            const osc = this.ctx.createOscillator();
            const gain = this.ctx.createGain();
            
            osc.connect(gain);
            gain.connect(this.ctx.destination);
            
            osc.type = 'sawtooth';
            osc.frequency.setValueAtTime(120, now + delay);
            
            gain.gain.setValueAtTime(0.06, now + delay);
            gain.gain.exponentialRampToValueAtTime(0.001, now + delay + 0.12);
            
            osc.start(now + delay);
            osc.stop(now + delay + 0.13);
        });
    }

    /**
     * Acquires real-time microphone stream input and links it to the analyser.
     */
    async getMicrophoneStream() {
        if (this.microphoneStream) return this.microphoneStream;
        
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
            this.microphoneStream = stream;
            if (this.initialized) {
                const source = this.ctx.createMediaStreamSource(stream);
                source.connect(this.analyser);
                console.log("[AUDIO] Microphone stream connected to analyser");
            }
            return stream;
        } catch (e) {
            console.warn("[AUDIO] Microphone access denied or unavailable", e);
            throw e;
        }
    }
}

// Instantiates a single master sound synth object in global scope
const soundSynth = new FrejaSoundSynth();
