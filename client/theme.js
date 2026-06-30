/**
 * F.R.E.J.A. Theme Manager
 */
window.FrejaTheme = {
    applyTheme(theme) {
        document.body.className = `theme-${theme}`;
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

    getCurrentThemeHue() {
        if (document.body.classList.contains('theme-amber')) return 38;
        if (document.body.classList.contains('theme-crimson')) return 355;
        if (document.body.classList.contains('theme-emerald')) return 145;
        return 185; // Default Cyan
    }
};
