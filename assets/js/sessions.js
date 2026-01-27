import { wsUrl, panes, incrementTabCounter } from './state.js';
import {
    MessageType,
    encodeIdentify,
    encodeAttach,
    encodeInput,
    encodeResize,
    decodeMessages,
    decodeSessionInfo,
} from './protocol.js';
import { updateGridLayout, setActivePane, closePane } from './pane.js';

export function showSessionsModal() {
    document.getElementById('sessions-modal').classList.remove('hidden');
    refreshSessionsList();
}

export function hideSessionsModal() {
    document.getElementById('sessions-modal').classList.add('hidden');
}

export async function refreshSessionsList() {
    const listEl = document.getElementById('sessions-list');
    listEl.innerHTML = '<div class="no-sessions">Loading...</div>';

    try {
        const response = await fetch('/api/sessions');
        const data = await response.json();
        const sessions = data.sessions.map(s => ({
            sessionId: s.id,
            name: s.name,
            width: s.width,
            height: s.height,
            pid: s.pid,
            createdAt: s.created_at,
            attachedCount: s.attached_count
        }));
        renderSessionsList(sessions);
    } catch (e) {
        console.error('Failed to fetch sessions:', e);
        listEl.innerHTML = '<div class="no-sessions">Failed to load sessions.</div>';
    }
}

function renderSessionsList(sessions) {
    const listEl = document.getElementById('sessions-list');
    if (sessions.length === 0) {
        listEl.innerHTML = '<div class="no-sessions">No existing sessions. Create a new tab first.</div>';
        return;
    }
    listEl.innerHTML = sessions.map(s => {
        const date = new Date(s.createdAt * 1000);
        const timeStr = date.toLocaleTimeString();
        return `
            <div class="session-item" data-session-id="${s.sessionId}">
                <div>
                    <div class="session-name">${s.name}</div>
                    <div class="session-meta">ID: ${s.sessionId} | ${s.width}x${s.height} | PID: ${s.pid}</div>
                </div>
                <div class="session-meta">${s.attachedCount} attached | ${timeStr}</div>
            </div>
        `;
    }).join('');

    listEl.querySelectorAll('.session-item').forEach(el => {
        el.addEventListener('click', () => {
            const sessionId = parseInt(el.dataset.sessionId, 10);
            hideSessionsModal();
            attachToSession(sessionId);
        });
    });
}

// TODO: Reuse TerminalPanel component, pass sessionId as prop
export function attachToSession(sessionId) {
    const tabNum = incrementTabCounter();
    const tabName = `Tab ${tabNum} (reconnect)`;

    const container = document.getElementById('panes-container');
    const paneDiv = document.createElement('div');
    paneDiv.className = 'pane';

    const header = document.createElement('div');
    header.className = 'pane-header';
    header.innerHTML = `<span>${tabName}</span><button class="close-btn" title="Close">×</button>`;
    paneDiv.appendChild(header);

    const termDiv = document.createElement('div');
    termDiv.className = 'pane-terminal';
    paneDiv.appendChild(termDiv);

    container.appendChild(paneDiv);

    const term = new Terminal({
        cursorBlink: true,
        fontSize: 12,
        fontFamily: 'Menlo, Monaco, "Courier New", monospace',
        theme: { background: '#1a1a2e', foreground: '#eee' }
    });
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(termDiv);

    const pane = {
        tabName, term, fitAddon, header, paneDiv,
        ws: null, recvBuffer: new ArrayBuffer(0), sessionActive: false,
    };

    header.querySelector('.close-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        closePane(pane);
    });

    paneDiv.addEventListener('click', () => setActivePane(pane));

    panes.push(pane);
    updateGridLayout();

    pane.ws = new WebSocket(wsUrl);
    pane.ws.binaryType = 'arraybuffer';

    pane.ws.onopen = () => {
        term.write('\x1b[33mReconnecting to session...\x1b[0m\r\n');
        pane.ws.send(encodeIdentify(term.cols, term.rows));
        pane.ws.send(encodeAttach(sessionId));
    };

    pane.ws.onmessage = (event) => {
        const combined = new Uint8Array(pane.recvBuffer.byteLength + event.data.byteLength);
        combined.set(new Uint8Array(pane.recvBuffer), 0);
        combined.set(new Uint8Array(event.data), pane.recvBuffer.byteLength);
        const { messages, remaining } = decodeMessages(combined.buffer);
        pane.recvBuffer = remaining;

        for (const msg of messages) {
            if (msg.type === MessageType.OUTPUT) {
                const text = new TextDecoder().decode(msg.payload);
                term.write(text);
            } else if (msg.type === MessageType.SESSION_INFO) {
                if (!pane.sessionActive) {
                    pane.sessionActive = true;
                    const info = decodeSessionInfo(msg.payload);
                    header.querySelector('span').textContent = `${info.name} (reconnected)`;
                    term.write('\x1b[32mReconnected!\x1b[0m\r\n');
                }
            } else if (msg.type === MessageType.ERROR) {
                const errLen = new DataView(msg.payload.buffer, msg.payload.byteOffset).getUint32(0, false);
                const errText = new TextDecoder().decode(msg.payload.slice(4, 4 + errLen));
                term.write(`\x1b[31mError: ${errText}\x1b[0m\r\n`);
            } else if (msg.type === MessageType.SHELL_EXITED) {
                term.write('\x1b[33mShell exited\x1b[0m\r\n');
                pane.sessionActive = false;
            }
        }
    };

    pane.ws.onclose = () => {
        term.write('\x1b[31mDisconnected\x1b[0m\r\n');
        pane.sessionActive = false;
    };

    pane.ws.onerror = () => {
        term.write('\x1b[31mConnection error\x1b[0m\r\n');
    };

    term.onData((data) => {
        if (pane.ws && pane.ws.readyState === WebSocket.OPEN && pane.sessionActive) {
            pane.ws.send(encodeInput(data));
        }
    });

    term.onResize(({ cols, rows }) => {
        if (pane.ws && pane.ws.readyState === WebSocket.OPEN && pane.sessionActive) {
            pane.ws.send(encodeResize(cols, rows));
        }
    });

    setActivePane(pane);
    document.getElementById('close-btn').classList.remove('hidden');
    document.getElementById('status').textContent = `${panes.length} tab(s) open`;
}

export async function autoReconnectLastSession() {
    try {
        const response = await fetch('/api/last-session');
        const data = await response.json();
        if (data.session_id !== null) {
            const sessionsResponse = await fetch('/api/sessions');
            const sessionsData = await sessionsResponse.json();
            const sessionExists = sessionsData.sessions.some(s => s.id === data.session_id);
            if (sessionExists) {
                attachToSession(data.session_id);
            }
        }
    } catch (e) {
        console.error('Failed to auto-reconnect:', e);
    }
}
