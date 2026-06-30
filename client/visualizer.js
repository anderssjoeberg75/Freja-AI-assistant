/**
 * F.R.E.J.A. - HTML5 Canvas Arc Reactor Core Visualizer
 * 
 * Draws the spinning neon circular reactor rings, pulsing core, inner measuring
 * ticks, and audio frequency bar spectrums responding dynamically to microphone inputs
 * and speaking states.
 */

class ArcReactorVisualizer {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        
        // Visualizer States: 'SLEEPING', 'LISTENING', 'PROCESSING', 'SPEAKING'
        this.state = 'SLEEPING'; 
        this.rotation = 0;
        this.pulse = 0;
        this.pulseDir = 1;
        this.audioData = new Uint8Array(128);
        this.glowHue = 185; // Accent theme color (e.g. Cyan: 185, Emerald: 145, etc.)
        
        this.bindEvents();
    }

    /**
     * Sets the active theme color hue in degrees (0-360).
     */
    setThemeHue(hue) {
        this.glowHue = hue;
    }

    /**
     * Configures click bindings on the center core element.
     */
    bindEvents() {
        const core = document.querySelector('.arc-core-inner');
        if (core) {
            core.addEventListener('click', () => {
                soundSynth.playClick();
                this.triggerReaction();
            });
        }
    }

    /**
     * Triggers a temporary processor processing spin reaction.
     */
    triggerReaction() {
        if (this.state === 'SLEEPING') {
            this.state = 'PROCESSING';
            setTimeout(() => {
                this.state = 'SLEEPING';
            }, 1000);
        }
    }

    /**
     * Begins the infinite canvas render frame request animation loop.
     */
    startAnimation() {
        const render = () => {
            this.update();
            this.draw();
            requestAnimationFrame(render);
        };
        requestAnimationFrame(render);
    }

    /**
     * Updates physics properties, rotational angles, and tracks Web Audio analyser frequencies.
     */
    update() {
        // Rotational speed values and pulsing amplitudes depend on current state
        if (this.state === 'SLEEPING') {
            this.rotation += 0.005;
            this.pulse += 0.008 * this.pulseDir;
            if (this.pulse > 1 || this.pulse < 0) this.pulseDir *= -1;
        } else if (this.state === 'LISTENING') {
            this.rotation += 0.015;
            this.pulse += 0.03 * this.pulseDir;
            if (this.pulse > 1 || this.pulse < 0) this.pulseDir *= -1;
        } else if (this.state === 'PROCESSING') {
            this.rotation += 0.04;
            this.pulse += 0.06 * this.pulseDir;
            if (this.pulse > 1 || this.pulse < 0) this.pulseDir *= -1;
        } else if (this.state === 'SPEAKING') {
            this.rotation += 0.01;
            this.pulse += 0.02 * this.pulseDir;
            if (this.pulse > 1 || this.pulse < 0) this.pulseDir *= -1;
        }

        // Retrieve actual audio frequencies if listening, or decay them otherwise
        if (this.state === 'LISTENING' && soundSynth.initialized && soundSynth.analyser) {
            soundSynth.analyser.getByteFrequencyData(this.audioData);
        } else {
            // Decay frequency bars back to zero when idle
            for (let i = 0; i < this.audioData.length; i++) {
                this.audioData[i] *= 0.92;
            }
        }
    }

    /**
     * Redraws all layers onto the HTML5 Canvas context.
     */
    draw() {
        const w = this.canvas.width;
        const h = this.canvas.height;
        const cx = w / 2;
        const cy = h / 2;
        
        // Clear canvas context
        this.ctx.clearRect(0, 0, w, h);
        
        // Resolve target glowing palette values matching colors
        let themeColor = `hsla(${this.glowHue}, 100%, 50%, 1)`;
        let glowColor = `hsla(${this.glowHue}, 100%, 50%, 0.4)`;
        
        if (this.state === 'LISTENING') {
            themeColor = `hsla(${this.glowHue}, 100%, 55%, 1)`;
            glowColor = `hsla(${this.glowHue}, 100%, 55%, 0.6)`;
        } else if (this.state === 'PROCESSING') {
            themeColor = `hsla(${this.glowHue}, 100%, 60%, 1)`;
            glowColor = `hsla(${this.glowHue}, 100%, 60%, 0.7)`;
        }

        this.ctx.save();
        
        // 1. BACK AMBIENT PROCESSOR RADIATING GLOW
        const ambientGlow = this.ctx.createRadialGradient(cx, cy, 10, cx, cy, 180);
        const intensity = 0.15 + (this.pulse * 0.1);
        ambientGlow.addColorStop(0, `hsla(${this.glowHue}, 100%, 50%, ${intensity})`);
        ambientGlow.addColorStop(0.5, `hsla(${this.glowHue}, 100%, 50%, ${intensity * 0.3})`);
        ambientGlow.addColorStop(1, 'rgba(0,0,0,0)');
        this.ctx.fillStyle = ambientGlow;
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, 180, 0, Math.PI * 2);
        this.ctx.fill();

        // 2. OUTER MEASUREMENT DASHED TICKS
        this.ctx.strokeStyle = `hsla(${this.glowHue}, 100%, 50%, 0.15)`;
        this.ctx.lineWidth = 1;
        this.ctx.setLineDash([4, 8]);
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, 170, 0, Math.PI * 2);
        this.ctx.stroke();

        // 3. MAIN ROTATING ENERGY ACCELERATOR ARC SEGMENTS
        this.ctx.restore();
        this.ctx.save();
        this.ctx.translate(cx, cy);
        this.ctx.rotate(this.rotation);
        
        this.ctx.strokeStyle = themeColor;
        this.ctx.lineWidth = 3;
        this.ctx.shadowBlur = 10;
        this.ctx.shadowColor = glowColor;
        
        // Draw 3 primary mechanical arc quadrants
        for (let i = 0; i < 3; i++) {
            const startAngle = (i * Math.PI * 2 / 3) + 0.1;
            const endAngle = ((i + 1) * Math.PI * 2 / 3) - 0.3;
            this.ctx.beginPath();
            this.ctx.arc(0, 0, 140, startAngle, endAngle);
            this.ctx.stroke();
        }

        // Draw inner mechanical reverse rotation ticker ring
        this.ctx.rotate(-this.rotation * 2.5);
        this.ctx.strokeStyle = `hsla(${this.glowHue}, 100%, 50%, 0.3)`;
        this.ctx.lineWidth = 1;
        this.ctx.setLineDash([15, 5, 2, 5]);
        this.ctx.beginPath();
        this.ctx.arc(0, 0, 115, 0, Math.PI * 2);
        this.ctx.stroke();
        this.ctx.restore();

        // 4. CIRCULAR SPECTRUM EQUALIZER NODES (reacting to frequency data or synthetic speech waveforms)
        this.ctx.save();
        this.ctx.translate(cx, cy);
        this.ctx.shadowBlur = 8;
        this.ctx.shadowColor = glowColor;
        
        const numBars = 48;
        const minRadius = 56;
        const maxRadius = 90;
        
        for (let i = 0; i < numBars; i++) {
            const angle = (i / numBars) * Math.PI * 2;
            
            // Map audio data index
            let dataIdx = Math.floor((i / numBars) * (this.audioData.length / 2));
            if (i > numBars / 2) {
                // Symmetrical visualizer mapping
                dataIdx = Math.floor(((numBars - i) / numBars) * (this.audioData.length / 2));
            }
            
            const rawVal = this.audioData[dataIdx] || 0;
            let val = rawVal / 255;
            
            // Simulate synthetic speech wobble if speaking natively without microphone hooks active
            if (this.state === 'SPEAKING' && val === 0) {
                val = (Math.sin(Date.now() * 0.015 + i * 0.3) * 0.5 + 0.5) * (0.3 + Math.sin(Date.now() * 0.003) * 0.2);
            }
            
            const height = val * (maxRadius - minRadius);
            
            const rStart = minRadius;
            const rEnd = minRadius + height + (this.state === 'LISTENING' ? 4 : 1);
            
            const x1 = Math.cos(angle) * rStart;
            const y1 = Math.sin(angle) * rStart;
            const x2 = Math.cos(angle) * rEnd;
            const y2 = Math.sin(angle) * rEnd;
            
            this.ctx.strokeStyle = `hsla(${this.glowHue}, 100%, 65%, ${0.4 + (val * 0.6)})`;
            this.ctx.lineWidth = 3;
            this.ctx.beginPath();
            this.ctx.moveTo(x1, y1);
            this.ctx.lineTo(x2, y2);
            this.ctx.stroke();
        }
        
        this.ctx.restore();

        // 5. INNER REACTOR MECHANICAL VENT LINES
        this.ctx.save();
        this.ctx.strokeStyle = `hsla(${this.glowHue}, 100%, 50%, 0.25)`;
        this.ctx.lineWidth = 2;
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, 50, 0, Math.PI * 2);
        this.ctx.stroke();
        
        // Draw 8 radial vents divider ticks
        this.ctx.strokeStyle = `hsla(${this.glowHue}, 100%, 50%, 0.12)`;
        this.ctx.lineWidth = 1;
        this.ctx.translate(cx, cy);
        for (let i = 0; i < 8; i++) {
            this.ctx.rotate(Math.PI / 4);
            this.ctx.beginPath();
            this.ctx.moveTo(45, 0);
            this.ctx.lineTo(54, 0);
            this.ctx.stroke();
        }
        this.ctx.restore();
    }
}
