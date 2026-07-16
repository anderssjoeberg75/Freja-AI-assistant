/**
 * F.R.E.J.A. Theme Manager
 */
window.FrejaTheme = {
    applyTheme(theme) {
        const isLight = document.body.classList.contains('light-mode');
        document.body.className = `theme-${theme}`;
        if (isLight) {
            document.body.classList.add('light-mode');
        }
        localStorage.setItem("freja_theme", theme);
        
        const cards = document.querySelectorAll('.theme-choice-card');
        cards.forEach(card => {
            if (card.getAttribute('data-theme') === theme) {
                card.classList.add('active');
            } else {
                card.classList.remove('active');
            }
        });

        const hue = this.getCurrentThemeHue();
        if (window.visualizer) {
            window.visualizer.setThemeHue(hue);
        }
    },

    setLightMode(enabled) {
        const chk = document.getElementById('chk-light-mode');
        if (chk) {
            chk.checked = enabled;
        }
        if (enabled) {
            document.body.classList.add('light-mode');
            localStorage.setItem("freja_light_mode", "true");
        } else {
            document.body.classList.remove('light-mode');
            localStorage.setItem("freja_light_mode", "false");
        }
    },

    getCurrentThemeHue() {
        if (document.body.classList.contains('theme-amber')) return 38;
        if (document.body.classList.contains('theme-crimson')) return 355;
        if (document.body.classList.contains('theme-emerald')) return 145;
        return 185; // Default Cyan
    }
};
