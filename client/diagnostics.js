/**
 * F.R.E.J.A. Diagnostics & Logging Module
 */
window.FrejaDiagnostics = {
    writeLog(msg, type = 'sys') {
        const logContainer = document.getElementById('terminal-log');
        if (!logContainer) return;
        const now = new Date();
        const timeStr = now.toTimeString().split(' ')[0];
        
        const line = document.createElement('div');
        line.className = 'log-line';
        
        let tag = "[SYS]";
        if (type === 'user') tag = "[USER]";
        if (type === 'gemini') tag = "[GMNI]";
        if (type === 'warn') tag = "[WARN]";
        if (type === 'err') tag = "[ERR ]";
        
        line.innerHTML = `
            <span class="log-time">${timeStr}</span>
            <span class="log-tag tag-${type}">${tag}</span>
            ${msg.toUpperCase()}
        `;
        
        logContainer.appendChild(line);
        logContainer.scrollTop = logContainer.scrollHeight;
    },

    startDiagnosticSimulation() {
        const cpuVal = document.getElementById('val-cpu');
        const cpuBar = document.getElementById('bar-cpu');
        const tempVal = document.getElementById('val-temp');
        const tempBar = document.getElementById('bar-temp');
        const ramVal = document.getElementById('val-ram');
        const ramBar = document.getElementById('bar-ram');
        const pingVal = document.getElementById('val-ping');
        const pingBar = document.getElementById('bar-ping');

        if (!cpuVal) return; // Grid layout might not be loaded yet

        let ramUsage = 6.2;

        setInterval(() => {
            const cpu = Math.floor(Math.random() * 20) + 12; // 12-32% CPU usage
            if (cpuVal) cpuVal.textContent = `${cpu}%`;
            if (cpuBar) cpuBar.style.width = `${cpu}%`;

            const temp = 40.5 + (cpu * 0.15) + (Math.random() * 0.4);
            if (tempVal) tempVal.textContent = `${temp.toFixed(1)} °C`;
            if (tempBar) tempBar.style.width = `${Math.min(temp, 100)}%`;

            ramUsage += (Math.random() * 0.1 - 0.05);
            ramUsage = Math.max(5.8, Math.min(6.8, ramUsage));
            const ramPercent = (ramUsage / 16) * 100;
            if (ramVal) ramVal.textContent = `${ramUsage.toFixed(1)} GB / 16 GB`;
            if (ramBar) ramBar.style.width = `${ramPercent}%`;

            const ping = Math.floor(Math.random() * 8) + 10; // 10-18ms network latency
            if (pingVal) pingVal.textContent = `${ping} ms`;
            if (pingBar) pingBar.style.width = `${ping * 4}%`;

        }, 3000);
    }
};
