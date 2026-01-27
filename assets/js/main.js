import { panes } from './state.js';
import { createPane, closeAllPanes, setActivePane, getActivePane } from './pane.js';
import { showSessionsModal, hideSessionsModal, refreshSessionsList, autoReconnectLastSession } from './sessions.js';

function setupEventListeners() {
    document.getElementById('add-btn').addEventListener('click', createPane);
    document.getElementById('sessions-btn').addEventListener('click', showSessionsModal);
    document.getElementById('close-btn').addEventListener('click', closeAllPanes);
    document.querySelector('.close-modal-btn').addEventListener('click', hideSessionsModal);
    document.getElementById('refresh-sessions-btn').addEventListener('click', refreshSessionsList);
    document.getElementById('sessions-modal').addEventListener('click', (e) => {
        if (e.target.id === 'sessions-modal') hideSessionsModal();
    });

    window.addEventListener('resize', () => {
        panes.forEach(p => p.fitAddon.fit());
    });

    // TODO: Dockview has built-in keyboard navigation API
    document.addEventListener('keydown', (e) => {
        if (e.altKey && panes.length > 1) {
            const active = getActivePane();
            const currentIdx = active ? panes.indexOf(active) : 0;
            let newIdx = currentIdx;

            if (e.key === 'ArrowRight' || e.key === 'l') {
                newIdx = (currentIdx + 1) % panes.length;
            } else if (e.key === 'ArrowLeft' || e.key === 'h') {
                newIdx = (currentIdx - 1 + panes.length) % panes.length;
            } else if (e.key === 'ArrowDown' || e.key === 'j') {
                const cols = Math.ceil(Math.sqrt(panes.length));
                newIdx = Math.min(currentIdx + cols, panes.length - 1);
            } else if (e.key === 'ArrowUp' || e.key === 'k') {
                const cols = Math.ceil(Math.sqrt(panes.length));
                newIdx = Math.max(currentIdx - cols, 0);
            }

            if (newIdx !== currentIdx) {
                e.preventDefault();
                setActivePane(panes[newIdx]);
            }
        }
    });
}

function init() {
    setupEventListeners();
    // TODO: Add Dockview popout support - tabs can be dragged to new OS windows
    autoReconnectLastSession();
}

init();
