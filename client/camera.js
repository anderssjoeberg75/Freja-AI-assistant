/**
 * F.R.E.J.A. Optics & Camera Module
 */
window.FrejaCamera = {
    cameraStream: null,
    savedCameraId: null,

    init() {
        this.savedCameraId = localStorage.getItem("freja_camera_device_id") || null;
    },

    async loadCameraDevices() {
        const selectCam = document.getElementById('select-camera');
        if (!selectCam) return;
        
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            const videoDevices = devices.filter(d => d.kind === 'videoinput');
            
            selectCam.innerHTML = '<option value="off">Scanner avstängd</option>';
            
            if (videoDevices.length === 0) {
                console.warn("[CAMERA] No camera devices found.");
                return;
            }
            
            videoDevices.forEach((device, index) => {
                const option = document.createElement('option');
                option.value = device.deviceId;
                option.textContent = device.label || `Kamera ${index + 1}`;
                
                if (this.savedCameraId && device.deviceId === this.savedCameraId) {
                    option.selected = true;
                }
                
                selectCam.appendChild(option);
            });
            
            // Auto start camera if we have a saved, active camera stream
            if (this.savedCameraId && this.savedCameraId !== 'off' && !this.cameraStream) {
                if (videoDevices.some(d => d.deviceId === this.savedCameraId)) {
                    selectCam.value = this.savedCameraId;
                    this.startCameraStream(this.savedCameraId);
                }
            }
            
            console.log("[CAMERA] Enumerated video input devices:", videoDevices);
        } catch (e) {
            console.error("[CAMERA] Failed to enumerate devices:", e);
        }
    },

    async startCameraStream(deviceId) {
        const video = document.getElementById('webcam-video');
        const status = document.getElementById('scanner-status');
        const capCamera = document.getElementById('cap-camera');
        
        this.stopCameraStream();
        
        if (deviceId === 'off') {
            localStorage.setItem("freja_camera_device_id", "off");
            this.savedCameraId = 'off';
            return;
        }

        localStorage.setItem("freja_camera_device_id", deviceId);
        this.savedCameraId = deviceId;

        if (window.uiController) {
            window.uiController.writeLog("ESTABLISHING OPTICAL LINK...", "sys");
        }
        if (window.soundSynth) window.soundSynth.playClick();
        
        try {
            const constraints = {
                video: {
                    deviceId: deviceId ? { ideal: deviceId } : undefined,
                    width: { ideal: 640 },
                    height: { ideal: 480 }
                }
            };
            
            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia(constraints);
            } catch (innerErr) {
                console.warn("[CAMERA] Detailed constraints failed, trying basic video fallback...", innerErr);
                stream = await navigator.mediaDevices.getUserMedia({ video: true });
            }
            
            this.cameraStream = stream;
            
            if (video) {
                video.srcObject = stream;
                video.classList.add('active');
            }
            
            if (status) {
                status.textContent = "SCANNING: SUBJECT ACTIVE";
            }
            
            if (capCamera) {
                capCamera.classList.add('active');
            }
            
            if (window.uiController) {
                window.uiController.writeLog("OPTICAL CHANNEL SECURED", "sys");
            }
            if (window.soundSynth) window.soundSynth.playNotify();
            
            setTimeout(() => this.loadCameraDevices(), 500);
            
        } catch (e) {
            console.error("[CAMERA] Failed to acquire stream:", e);
            if (window.uiController) {
                window.uiController.writeLog("OPTICAL CAPTURE DENIED OR FAILED", "err");
            }
            if (window.soundSynth) window.soundSynth.playError();
            
            const selectCam = document.getElementById('select-camera');
            if (selectCam) selectCam.value = 'off';
            this.stopCameraStream();
        }
    },

    stopCameraStream() {
        const video = document.getElementById('webcam-video');
        const status = document.getElementById('scanner-status');
        const capCamera = document.getElementById('cap-camera');
        
        if (this.cameraStream) {
            this.cameraStream.getTracks().forEach(track => track.stop());
            this.cameraStream = null;
        }
        
        if (video) {
            video.srcObject = null;
            video.classList.remove('active');
        }
        
        if (status) {
            status.textContent = "OPTICS OFFLINE";
        }
        
        if (capCamera) {
            capCamera.classList.remove('active');
        }
    }
};
