/**
 * F.R.E.J.A. Markdown Parser & Copy Helper
 */
window.FrejaMarkdown = {
    escapeHTML(text) {
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    },

    parseMarkdown(text) {
        const codeBlocks = [];
        let html = text;
        
        // 1. Extract and escape code blocks
        html = html.replace(/```([\s\S]*?)```/g, (match, p1) => {
            const id = `__CODE_BLOCK_${codeBlocks.length}__`;
            const escaped = this.escapeHTML(p1.trim());
            codeBlocks.push(`<pre><code>${escaped}</code><button class="copy-code-btn" title="Kopiera kod" onclick="window.FrejaMarkdown.copyCode(this)"><i class="fa-solid fa-copy"></i></button></pre>`);
            return id;
        });
        
        // 2. Parse inline code ticks
        html = html.replace(/`([^`]+)`/g, (match, p1) => {
            return `<code>${this.escapeHTML(p1)}</code>`;
        });
        
        // 3. Parse bold symbols
        html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        
        // 4. Parse italics
        html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
        
        // 5. Parse links: [label](url) - supports both http(s) and relative URLs (e.g. /api/docs/...)
        html = html.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/)[^\s)]+)\)/g, (match, text, url) => {
            let finalUrl = url;
            if (url.startsWith('/api/docs/')) {
                const token = localStorage.getItem('freja_access_token') || '';
                if (token && !finalUrl.includes('token=')) {
                    finalUrl += (finalUrl.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
                }
            }
            return `<a href="${finalUrl}" target="_blank" class="hud-link">${text} <i class="fa-solid fa-arrow-up-right-from-square" style="font-size: 8px;"></i></a>`;
        });
        
        // 6. Parse list items (lines starting with * or - or •)
        html = html.replace(/^[-*•]\s+(.+)$/gm, '• $1');
        
        // 7. Replace newlines with <br>
        html = html.replace(/\n/g, '<br>');
        
        // 8. Restore code blocks
        codeBlocks.forEach((block, index) => {
            html = html.replace(`__CODE_BLOCK_${index}__`, block);
        });
        
        return html;
    },

    copyCode(button) {
        const pre = button.parentElement;
        const code = pre.querySelector('code');
        if (!code) return;
        
        navigator.clipboard.writeText(code.innerText).then(() => {
            if (window.soundSynth) window.soundSynth.playNotify();
            const originalHTML = button.innerHTML;
            button.innerHTML = '<i class="fa-solid fa-check" style="color: var(--color-primary);"></i>';
            if (window.uiController) {
                window.uiController.writeLog("CODE COPIED TO SYSTEM CLIPBOARD", "sys");
            }
            setTimeout(() => {
                button.innerHTML = originalHTML;
            }, 2000);
        }).catch(err => {
            console.error("Failed to copy code: ", err);
            if (window.soundSynth) window.soundSynth.playError();
        });
    }
};
