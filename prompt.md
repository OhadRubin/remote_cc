# Prompt
Port the frontend from vanilla JS to Vite + React + Dockview.

## Current files to migrate
- index.html (entry point)
- assets/js/main.js (event listeners, keyboard nav)
- assets/js/pane.js (terminal creation, WebSocket connection, grid layout)
- assets/js/state.js (shared state)
- assets/js/sessions.js (session modal, reconnect logic)
- assets/js/protocol.js (binary protocol encoding/decoding)
- assets/css/style.css

## Requirements

1. **Preserve all existing functionality**:
   - New tab creation with xterm.js terminal
   - WebSocket connection to /ws (binary protocol unchanged)
   - Sessions modal (list/attach to existing sessions)
   - Auto-reconnect to last session
   - Keyboard navigation (Alt+arrows/hjkl)

2. **Add Dockview tab dragging** (see tab_dragging.md):
   - Drag to reorder tabs within window
   - Drag tab out to create new browser window (popout)
   - Drag tabs between windows to merge

3. **Backend compatibility**:
   - index.py serves static files from /assets
   - Keep /ws WebSocket endpoint working
   - Update index.py to serve Vite build output (likely from /dist)
   - Running `./index.py` should automatically build the frontend (run `npm run build` if dist is missing or stale)

4. **Project structure**:
   - Initialize with `npm create vite@latest` (React + TypeScript)
   - Install: dockview, xterm, xterm-addon-fit
   - Output build to a directory that index.py can serve
   - Update .gitignore for Node.js (node_modules/, dist/, etc.)

## Non-goals
- No changes to the WebSocket protocol
- No changes to Python session management logic

Key improvements:
- Lists all files that need migration
- Explicit about what to preserve vs. add
- Specifies the backend integration point (serving build output)
- Clear non-goals to prevent scope creep
- Mentions TypeScript (Vite default) — remove if you prefer JS



# Plan: Port Frontend to Vite + React + Dockview

## Overview
Migrate vanilla JS frontend to Vite + React + TypeScript + Dockview for tab dragging support.

## Project Structure

```
/home/ohadr/remote_cc/
├── index.py                    # Backend (modify lines 703, 799-804, 813)
├── frontend/                   # New React frontend
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html              # Vite entry
│   ├── src/
│   │   ├── main.tsx            # React entry
│   │   ├── App.tsx             # Main app with Dockview
│   │   ├── protocol.ts         # Binary protocol (port from protocol.js:1-95)
│   │   ├── types.ts            # TypeScript interfaces
│   │   ├── hooks/
│   │   │   └── useTerminalSession.ts  # WS + terminal integration
│   │   ├── components/
│   │   │   ├── TerminalPanel.tsx      # Dockview panel
│   │   │   ├── SessionsModal.tsx      # Sessions list/attach
│   │   │   └── Toolbar.tsx            # Top buttons
│   │   └── styles/
│   │       └── global.css             # Port from style.css
│   └── dist/                   # Build output (served by backend)
└── assets/                     # Legacy (keep until migration complete)
```

## Implementation Steps

### 1. Initialize Vite Project
```bash
cd /home/ohadr/remote_cc
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install dockview xterm xterm-addon-fit
```

### 2. Create `frontend/vite.config.ts`
- Set `base: '/'`
- Set `build.outDir: 'dist'`, `build.assetsDir: 'assets'`
- Configure dev proxy: `/ws` -> `ws://localhost:8045`, `/api` -> `http://localhost:8045`

### 3. Create `frontend/src/types.ts`
```typescript
export interface SessionInfo {
  sessionId: number;
  name: string;
  width: number;
  height: number;
  pid: number;
  createdAt: number;
  attachedCount: number;
}

export interface DecodedMessage {
  type: number;
  payload: Uint8Array;
}
```

### 4. Port `frontend/src/protocol.ts` (from `assets/js/protocol.js:1-95`)
- Convert to TypeScript with enum `MessageType`
- Keep exact same binary encoding/decoding logic
- Export all encode/decode functions with proper types

### 5. Create `frontend/src/hooks/useTerminalSession.ts`
Logic from `assets/js/pane.js:71-135` and `assets/js/sessions.js:119-176`:
- Create xterm Terminal with same config (fontSize: 12, theme: #1a1a2e/#eee)
- Connect WebSocket to wsUrl (`${protocol}://${host}/ws`)
- Send IDENTIFY, then NEW_SESSION or ATTACH based on sessionId prop
- Handle messages: OUTPUT -> term.write, SESSION_INFO -> mark active, ERROR -> show error, SHELL_EXITED -> mark inactive
- Wire term.onData -> INPUT, term.onResize -> RESIZE
- Return: { termRef, sessionActive, sessionInfo, disconnect }

### 6. Create `frontend/src/components/TerminalPanel.tsx`
Dockview panel component:
- Accept `IDockviewPanelProps<{ sessionId?: number }>`
- Use `useTerminalSession` hook
- Handle `api.onDidDimensionsChange` -> fitAddon.fit()
- Update tab title via `api.setTitle(sessionInfo.name)` on SESSION_INFO

### 7. Create `frontend/src/components/SessionsModal.tsx`
Port from `assets/js/sessions.js:22-72`:
- Fetch `/api/sessions` on mount
- Render session list with same styling
- onClick -> call `onSelectSession(sessionId)` prop

### 8. Create `frontend/src/components/Toolbar.tsx`
Buttons: "+ New Tab", "Sessions", "Close All" (hidden when 0 panels)

### 9. Create `frontend/src/App.tsx`
- DockviewReact with `components={{ terminal: TerminalPanel }}`
- `onReady`: save api ref, call autoReconnectLastSession
- `addNewTab`: `api.addPanel({ id, component: 'terminal', title })`
- `attachToSession`: `api.addPanel({ ..., params: { sessionId } })`
- Keyboard nav: Alt+arrows/hjkl using `api.panels` and `api.activePanel`
- Render: Toolbar, DockviewReact, SessionsModal (conditional)

### 10. Create `frontend/src/main.tsx`
```tsx
import { createRoot } from 'react-dom/client';
import { App } from './App';
import 'dockview/dist/styles/dockview.css';
import './styles/global.css';

createRoot(document.getElementById('root')!).render(<App />);
```

### 11. Create `frontend/src/styles/global.css`
Port from `assets/css/style.css:1-179`:
- Keep body, button, modal styles
- Remove grid layout (#panes-container grid rules) - Dockview handles this
- Remove .pane-header styles - Dockview provides tab headers
- Keep .pane-terminal, .modal*, .session-item styles

### 12. Create `frontend/index.html`
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Terminal</title>
</head>
<body>
  <div id="root"></div>
  <script type="module" src="/src/main.tsx"></script>
</body>
</html>
```

### 13. Modify `index.py`

**Line 703** - Change static mount:
```python
app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="assets")
```

**Lines 799-804** - Serve from dist:
```python
@app.get("/", response_class=HTMLResponse)
async def terminal_ui(request: Request):
    if request.session.get("user") != ALLOWED_EMAIL:
        return RedirectResponse(url="/login")
    html = open("frontend/dist/index.html").read()
    return html
```

**After line 50** - Add auto-build function:
```python
import subprocess
from pathlib import Path

def ensure_frontend_built():
    frontend_dir = Path(__file__).parent / "frontend"
    dist_dir = frontend_dir / "dist"
    if not (dist_dir / "index.html").exists():
        print("Building frontend...")
        subprocess.run(["npm", "install"], cwd=frontend_dir, check=True)
        subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
    else:
        src_dir = frontend_dir / "src"
        if src_dir.exists():
            src_mtime = max(f.stat().st_mtime for f in src_dir.rglob("*") if f.is_file())
            dist_mtime = (dist_dir / "index.html").stat().st_mtime
            if src_mtime > dist_mtime:
                print("Frontend sources changed, rebuilding...")
                subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
```

**Line 813** - Call in run_server:
```python
async def run_server(socket_path: str, http_port: int) -> None:
    ensure_frontend_built()
    ...
```

### 14. Update `.gitignore`
Add:
```
node_modules/
frontend/dist/
*.tsbuildinfo
```

## Features Preserved
- New tab creation with xterm.js
- WebSocket binary protocol (unchanged)
- Sessions modal (list/attach)
- Auto-reconnect to last session
- Keyboard nav: Alt+arrows/hjkl

## Features Added (Dockview)
- Drag tabs to reorder
- Drag tab out to popout window
- Drag tabs between windows to merge
- Built-in tab close buttons

## Verification
1. `cd frontend && npm run build` - should produce dist/
2. `./index.py` - should auto-build if needed, serve on :8045
3. Open browser, create new tab - terminal should connect
4. Open Sessions modal - should list existing sessions
5. Drag tab to reorder - should work
6. Alt+arrows - should navigate between tabs
7. Close tab/all tabs - should work
