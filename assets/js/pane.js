import { wsUrl, panes, activePane, setActivePane as updateActivePane, incrementTabCounter } from './state.js';
import {
    MessageType,
    encodeIdentify,
    encodeNewSession,
    encodeInput,
    encodeResize,
    decodeMessages,
} from './protocol.js';

// TODO: Remove - Dockview handles layout automatically
export function updateGridLayout() {
    const container = document.getElementById('panes-container');
    const count = panes.length;
    if (count === 0) return;

    const cols = Math.ceil(Math.sqrt(count));
    const rows = Math.ceil(count / cols);
    container.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    container.style.gridTemplateRows = `repeat(${rows}, 1fr)`;

    setTimeout(() => {
        panes.forEach(p => p.fitAddon.fit());
    }, 100);
}

export function setActivePane(pane) {
    const current = panes.find(p => p.header.classList.contains('active'));
    if (current) {
        current.header.classList.remove('active');
    }
    updateActivePane(pane);
    pane.header.classList.add('active');
    pane.term.focus();
}

export function closePane(pane) {
    if (pane.ws) pane.ws.close();
    pane.paneDiv.remove();
    const idx = panes.indexOf(pane);
    if (idx > -1) panes.splice(idx, 1);

    const current = panes.find(p => p.header.classList.contains('active'));
    if (!current || current === pane) {
        const newActive = panes.length > 0 ? panes[0] : null;
        updateActivePane(newActive);
        if (newActive) setActivePane(newActive);
    }

    updateGridLayout();
    if (panes.length === 0) {
        document.getElementById('close-btn').classList.add('hidden');
        document.getElementById('status').textContent = 'All tabs closed.';
    } else {
        document.getElementById('status').textContent = `${panes.length} tab(s) open`;
    }
}

export function closeAllPanes() {
    panes.forEach(pane => {
        if (pane.ws) pane.ws.close();
        pane.paneDiv.remove();
    });
    panes.length = 0;
    updateActivePane(null);
    document.getElementById('close-btn').classList.add('hidden');
    document.getElementById('status').textContent = 'All tabs closed.';
}

// TODO: Extract to useTerminalSession() hook with reconnect logic
function connectPane(pane) {
    try {
        pane.ws = new WebSocket(wsUrl);
    } catch (e) {
        pane.term.write(`\x1b[31mFailed to connect: ${e.message}\x1b[0m\r\n`);
        return;
    }

    pane.ws.binaryType = 'arraybuffer';

    pane.ws.onopen = () => {
        pane.term.write(`\x1b[33mConnecting...\x1b[0m\r\n`);
        pane.ws.send(encodeIdentify(pane.term.cols, pane.term.rows));
        const sessionName = `session-${Date.now().toString(36)}`;
        pane.ws.send(encodeNewSession(sessionName));
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
                pane.term.write(text);
            } else if (msg.type === MessageType.SESSION_INFO) {
                if (!pane.sessionActive) {
                    pane.sessionActive = true;
                    pane.term.write(`\x1b[32mSession ready.\x1b[0m\r\n`);
                }
            } else if (msg.type === MessageType.ERROR) {
                const text = new TextDecoder().decode(msg.payload);
                pane.term.write(`\x1b[31mError: ${text}\x1b[0m\r\n`);
            } else if (msg.type === MessageType.SHELL_EXITED) {
                pane.term.write(`\x1b[33mShell exited\x1b[0m\r\n`);
                pane.sessionActive = false;
            }
        }
    };

    pane.ws.onclose = () => {
        pane.term.write(`\x1b[31mDisconnected\x1b[0m\r\n`);
        pane.sessionActive = false;
    };

    pane.ws.onerror = () => {
        pane.term.write(`\x1b[31mConnection error\x1b[0m\r\n`);
    };

    pane.term.onData((data) => {
        if (pane.ws && pane.ws.readyState === WebSocket.OPEN && pane.sessionActive) {
            pane.ws.send(encodeInput(data));
        }
    });

    pane.term.onResize(({ cols, rows }) => {
        if (pane.ws && pane.ws.readyState === WebSocket.OPEN && pane.sessionActive) {
            pane.ws.send(encodeResize(cols, rows));
        }
    });
}

// TODO: Replace with Dockview panel factory - each panel is a React component
export function createPane() {
    const tabNum = incrementTabCounter();
    const tabName = `Tab ${tabNum}`;

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
        theme: {
            background: '#1a1a2e',
            foreground: '#eee',
        }
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

    connectPane(pane);
    setActivePane(pane);

    document.getElementById('close-btn').classList.remove('hidden');
    document.getElementById('status').textContent = `${panes.length} tab(s) open`;

    return pane;
}

export function getActivePane() {
    return panes.find(p => p.header.classList.contains('active')) || null;
}
