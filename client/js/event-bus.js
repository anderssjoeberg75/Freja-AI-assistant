/**
 * F.R.E.J.A. Neural Interface - Event Bus & State Management Engine
 * Provides a lightweight, decoupled Pub/Sub event system for UI modules and diagnostic feeds.
 */
class FrejaEventBus {
    constructor() {
        this.listeners = new Map();
        this.state = {
            speechActive: false,
            opticsActive: false,
            currentTheme: 'theme-cyan',
            connectedServices: new Set(),
            lastLog: null
        };
    }

    /**
     * Subscribe to an event topic.
     * @param {string} event - Event topic name
     * @param {Function} callback - Callback handler
     * @returns {Function} Unsubscribe function
     */
    on(event, callback) {
        if (!this.listeners.has(event)) {
            this.listeners.set(event, []);
        }
        this.listeners.get(event).push(callback);

        return () => this.off(event, callback);
    }

    /**
     * Unsubscribe from an event topic.
     * @param {string} event - Event topic name
     * @param {Function} callback - Callback handler to remove
     */
    off(event, callback) {
        if (!this.listeners.has(event)) return;
        const callbacks = this.listeners.get(event).filter(cb => cb !== callback);
        this.listeners.set(event, callbacks);
    }

    /**
     * Emit an event to all registered listeners.
     * @param {string} event - Event topic name
     * @param {any} payload - Data payload sent with event
     */
    emit(event, payload) {
        if (this.listeners.has(event)) {
            this.listeners.get(event).forEach(callback => {
                try {
                    callback(payload);
                } catch (err) {
                    console.error(`[FrejaEventBus] Error in listener for "${event}":`, err);
                }
            });
        }
    }

    /**
     * Update global system state and notify subscribers if state changes.
     * @param {Object} partialState - State slice updates
     */
    setState(partialState) {
        const prevState = { ...this.state };
        this.state = { ...this.state, ...partialState };
        this.emit('state:changed', { prevState, state: this.state, changes: partialState });
    }

    /**
     * Helper to dispatch system diagnostic log messages across HUD modules.
     * @param {string} message - Diagnostic message
     * @param {string} level - Log level ('info' | 'warn' | 'error' | 'sys')
     */
    log(message, level = 'info') {
        const logItem = { message, level, timestamp: new Date().toISOString() };
        this.state.lastLog = logItem;
        this.emit('sys:log', logItem);
    }
}

// Global EventBus instance for Freja UI
window.frejaEventBus = new FrejaEventBus();
