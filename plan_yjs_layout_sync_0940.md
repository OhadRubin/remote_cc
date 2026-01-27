# Plan: Yjs-Based Layout Synchronization

## Goal

Replace localStorage-based layout persistence with Yjs CRDT sync, enabling seamless real-time layout synchronization across multiple browser sessions.

## Overview

- **Backend**: Mount pycrdt-websocket server alongside existing FastAPI app
- **Frontend**: Use Yjs + y-websocket to sync Dockview layout state
- **Persistence**: Store layout to disk via pycrdt Store for server restart survival

---

## Backend Changes (`index.py`)

### 1. Add dependencies (lines 3-11)

```python
# /// script
# dependencies = [
#     "fastapi",
#     "uvicorn",
#     "pyte",
#     "authlib",
#     "httpx",
#     "itsdangerous",
#     "python-dotenv",
#     "pycrdt",
#     "pycrdt-websocket @ git+https://github.com/y-crdt/pycrdt-websocket",
#     "pycrdt-store",
# ]
# ///
```

### 2. Add imports (after line 46)

```python
from contextlib import asynccontextmanager
from pathlib import Path
from pycrdt.websocket import ASGIServer, WebsocketServer
from pycrdt.store import FileYStore
```

### 3. Add Yjs server setup with persistence (after line 54, before line 56)

```python
LAYOUT_STORE_PATH = Path(__file__).parent / ".yjs_store"


class PersistentWebsocketServer(WebsocketServer):
    """WebsocketServer that persists room data to disk using FileYStore."""

    async def get_room(self, name: str):
        from pycrdt.websocket.yroom import YRoom
        from functools import partial

        if name not in self.rooms.keys():
            store_path = LAYOUT_STORE_PATH / f"{name}.ystore"
            ystore = FileYStore(path=str(store_path))
            provider_factory = (
                partial(self.provider_factory, path=name)
                if self.provider_factory is not None
                else None
            )
            self.rooms[name] = YRoom(
                ready=self.rooms_ready,
                log=self.log,
                ystore=ystore,
                provider_factory=provider_factory,
            )
        room = self.rooms[name]
        await self.start_room(room)
        return room


yjs_server: PersistentWebsocketServer | None = None
```

### 4. Replace FastAPI app initialization (lines 744-745)

**Before:**
```python
app = FastAPI(title="Terminal")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
```

**After:**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    global yjs_server
    LAYOUT_STORE_PATH.mkdir(parents=True, exist_ok=True)
    yjs_server = PersistentWebsocketServer()
    async with yjs_server:
        yield
    yjs_server = None

app = FastAPI(title="Terminal", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
```

### 5. Mount Yjs ASGI server with auth middleware (after line 746)

The `/yjs` endpoint uses `ASGIServer` which bypasses FastAPI's session middleware.
We wrap it with a custom ASGI middleware that validates the session cookie before
allowing the WebSocket connection (same cookie-based auth used by `/ws`).

```python
class AuthASGIMiddleware:
    """Wraps an ASGI app to require valid session cookie for WebSocket connections."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            headers = dict(scope.get("headers", []))
            cookie_header = headers.get(b"cookie", b"").decode("utf-8")
            email = get_email_from_cookie(cookie_header)
            if email != ALLOWED_EMAIL:
                await send({"type": "websocket.close", "code": 4001})
                return
        await self.app(scope, receive, send)

# Yjs WebSocket endpoint for layout sync (auth via cookie, same as /ws)
app.mount("/yjs", AuthASGIMiddleware(ASGIServer(lambda: yjs_server)))
```

### 6. Add .yjs_store to .gitignore

```
.yjs_store/
```

---

## Frontend Changes

### 1. Install dependencies

```bash
cd frontend && npm install yjs y-websocket
```

### 2. Create Yjs layout hook (`frontend/src/hooks/useLayoutSync.ts`) - NEW FILE

```typescript
import { useEffect, useRef, useState, useCallback } from 'react';
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import type { DockviewApi } from 'dockview';

interface PanelState {
  id: string;
  component: string;
  title: string;
  params?: Record<string, unknown>;
}

export function useLayoutSync(dockviewApi: DockviewApi | null) {
  const ydocRef = useRef<Y.Doc | null>(null);
  const providerRef = useRef<WebsocketProvider | null>(null);
  const [connected, setConnected] = useState(false);
  const isLocalChangeRef = useRef(false);

  useEffect(() => {
    const ydoc = new Y.Doc();
    ydocRef.current = ydoc;

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const provider = new WebsocketProvider(
      `${wsProtocol}//${window.location.host}/yjs`,
      'layout',
      ydoc
    );
    providerRef.current = provider;

    provider.on('status', (event: { status: string }) => {
      setConnected(event.status === 'connected');
    });

    return () => {
      provider.destroy();
      ydoc.destroy();
    };
  }, []);

  // Observe remote changes and apply to Dockview
  useEffect(() => {
    if (!ydocRef.current || !dockviewApi) return;

    const panelsArray = ydocRef.current.getArray<PanelState>('panels');

    const observer = () => {
      if (isLocalChangeRef.current) {
        isLocalChangeRef.current = false;
        return;
      }

      const remotePanels = panelsArray.toArray();
      const localPanelIds = new Set(dockviewApi.panels.map(p => p.id));
      const remotePanelIds = new Set(remotePanels.map(p => p.id));

      // Add panels that exist remotely but not locally
      for (const panel of remotePanels) {
        if (!localPanelIds.has(panel.id)) {
          dockviewApi.addPanel({
            id: panel.id,
            component: panel.component,
            title: panel.title,
            params: panel.params,
          });
        }
      }

      // Close panels that exist locally but not remotely
      for (const panel of dockviewApi.panels) {
        if (!remotePanelIds.has(panel.id)) {
          panel.api.close();
        }
      }

      // Update titles for existing panels
      for (const panel of remotePanels) {
        const localPanel = dockviewApi.getPanel(panel.id);
        if (localPanel && localPanel.title !== panel.title) {
          localPanel.api.setTitle(panel.title);
        }
      }
    };

    panelsArray.observe(observer);
    return () => panelsArray.unobserve(observer);
  }, [dockviewApi]);

  const addPanel = useCallback((panel: PanelState) => {
    if (!ydocRef.current) return;
    isLocalChangeRef.current = true;
    const panelsArray = ydocRef.current.getArray<PanelState>('panels');
    panelsArray.push([panel]);
  }, []);

  const removePanel = useCallback((panelId: string) => {
    if (!ydocRef.current) return;
    isLocalChangeRef.current = true;
    const panelsArray = ydocRef.current.getArray<PanelState>('panels');
    const index = panelsArray.toArray().findIndex(p => p.id === panelId);
    if (index !== -1) {
      panelsArray.delete(index, 1);
    }
  }, []);

  const updatePanelTitle = useCallback((panelId: string, title: string) => {
    if (!ydocRef.current) return;
    isLocalChangeRef.current = true;
    const panelsArray = ydocRef.current.getArray<PanelState>('panels');
    const arr = panelsArray.toArray();
    const index = arr.findIndex(p => p.id === panelId);
    if (index !== -1) {
      const panel = arr[index];
      panelsArray.delete(index, 1);
      panelsArray.insert(index, [{ ...panel, title }]);
    }
  }, []);

  const clearAllPanels = useCallback(() => {
    if (!ydocRef.current) return;
    isLocalChangeRef.current = true;
    const panelsArray = ydocRef.current.getArray<PanelState>('panels');
    panelsArray.delete(0, panelsArray.length);
  }, []);

  return {
    connected,
    addPanel,
    removePanel,
    updatePanelTitle,
    clearAllPanels,
  };
}
```

### 3. Modify App.tsx

#### 3a. Add import (after line 8)

```typescript
import { useLayoutSync } from './hooks/useLayoutSync';
```

#### 3b. Add hook usage (after line 25, inside App component)

```typescript
const layoutSync = useLayoutSync(dockviewRef.current);
```

#### 3c. Replace localStorage.setItem in onReady (line 60)

**Before:**
```typescript
localStorage.setItem('terminal-layout', JSON.stringify(event.api.toJSON()));
```

**After:**
```typescript
// Layout sync handled by useLayoutSync hook - remove localStorage
```

#### 3d. Replace localStorage.getItem in onReady (lines 64-73)

**Before:**
```typescript
const saved = localStorage.getItem('terminal-layout');
if (saved) {
  try {
    event.api.fromJSON(JSON.parse(saved));
    updatePanelCount();
    return;
  } catch (e) {
    console.warn('Failed to restore layout:', e);
  }
}
```

**After:**
```typescript
// Initial layout loaded via Yjs sync - panels will appear automatically
// Just wait a moment for Yjs to sync, then fallback to auto-reconnect
setTimeout(() => {
  if (event.api.panels.length === 0) {
    autoReconnectLastSession(event.api);
  }
}, 500);
return;
```

#### 3e. Update addPanel calls to use layoutSync

In `LeftHeaderActions` (around line 244):
```typescript
const onClick = () => {
  panelCounter++;
  const id = `terminal-${panelCounter}`;
  const title = `Tab ${panelCounter}`;
  props.containerApi.addPanel({
    id,
    component: 'terminal',
    title,
    position: { referenceGroup: props.group },
  });
  layoutSync.addPanel({ id, component: 'terminal', title });
  updatePanelCount();
};
```

#### 3f. Update closeAllPanels (lines 46-52)

**Before:**
```typescript
const closeAllPanels = useCallback(() => {
  if (!dockviewRef.current) return;
  const panels = [...dockviewRef.current.panels];
  panels.forEach((panel) => panel.api.close());
  localStorage.removeItem('terminal-layout');
  updatePanelCount();
}, [updatePanelCount]);
```

**After:**
```typescript
const closeAllPanels = useCallback(() => {
  if (!dockviewRef.current) return;
  const panels = [...dockviewRef.current.panels];
  panels.forEach((panel) => panel.api.close());
  layoutSync.clearAllPanels();
  updatePanelCount();
}, [updatePanelCount, layoutSync]);
```

#### 3g. Update handleCloseTab (lines 219-226)

**Before:**
```typescript
const handleCloseTab = useCallback((panelId: string) => {
  const panel = dockviewRef.current?.getPanel(panelId);
  if (panel) {
    panel.api.close();
    panelSessionMap.delete(panelId);
    updatePanelCount();
  }
}, [updatePanelCount]);
```

**After:**
```typescript
const handleCloseTab = useCallback((panelId: string) => {
  const panel = dockviewRef.current?.getPanel(panelId);
  if (panel) {
    panel.api.close();
    panelSessionMap.delete(panelId);
    layoutSync.removePanel(panelId);
    updatePanelCount();
  }
}, [updatePanelCount, layoutSync]);
```

#### 3h. Update attachToSession (lines 33-44)

```typescript
const attachToSession = useCallback((sessionId: number) => {
  if (!dockviewRef.current) return;
  panelCounter++;
  const id = `terminal-${panelCounter}`;
  const title = `Tab ${panelCounter} (reconnect)`;
  dockviewRef.current.addPanel({
    id,
    component: 'terminal',
    title,
    params: { sessionId },
  });
  layoutSync.addPanel({ id, component: 'terminal', title, params: { sessionId } });
  updatePanelCount();
}, [updatePanelCount, layoutSync]);
```

#### 3i. Update createFirstTab (lines 303-312)

```typescript
const createFirstTab = useCallback(() => {
  if (!dockviewRef.current) return;
  panelCounter++;
  const id = `terminal-${panelCounter}`;
  const title = `Tab ${panelCounter}`;
  dockviewRef.current.addPanel({
    id,
    component: 'terminal',
    title,
  });
  layoutSync.addPanel({ id, component: 'terminal', title });
  updatePanelCount();
}, [updatePanelCount, layoutSync]);
```

---

## Files Summary

| File | Action | Changes |
|------|--------|---------|
| `index.py` | Modify | Add deps, imports, lifespan, mount Yjs server (~25 lines) |
| `frontend/src/hooks/useLayoutSync.ts` | Create | New hook for Yjs sync (~120 lines) |
| `frontend/src/App.tsx` | Modify | Replace localStorage with layoutSync calls (~30 lines changed) |
| `frontend/package.json` | Modify | Add yjs, y-websocket deps |
| `.gitignore` | Modify | Add `.yjs_store/` |

---

## Testing Plan

1. Start server: `./index.py`
2. Open browser A at `localhost:8045`
3. Open browser B at `localhost:8045`
4. In browser A: create new tab
5. Verify: tab appears in browser B
6. In browser B: close the tab
7. Verify: tab closes in browser A
8. Restart server
9. Verify: layout persists after restart

---

## Open Questions

### 1. Authentication on `/yjs` endpoint ✅ RESOLVED

**Problem**: `ASGIServer` from pycrdt-websocket creates its own WebSocket handling, which bypasses FastAPI's session middleware and auth checks.

**Solution**: Option A - Wrap ASGIServer with `AuthASGIMiddleware` that validates the session cookie (same cookie-based auth used by `/ws`). See "Mount Yjs ASGI server with auth middleware" section above.

---

### 2. Panel ID collisions across browsers

**Problem**: `panelCounter` is local to each browser. If browser A creates `terminal-5` and browser B also creates `terminal-5`, they collide.

**Options**:

A. **UUID-based panel IDs** - Use `crypto.randomUUID()` instead of counter
```typescript
const id = `terminal-${crypto.randomUUID().slice(0, 8)}`;
```

B. **Include client ID in panel ID** - `terminal-${clientId}-${counter}`

C. **Server-assigned IDs** - Request panel ID from server before creating

**Recommendation**: Option A is simplest and sufficient.

---

### 3. Initial sync race condition

**Problem**: When a new browser connects, Yjs needs time to sync. The current plan uses `setTimeout(500ms)` which is fragile.

**Options**:

A. **Wait for Yjs sync event** - WebsocketProvider emits `'sync'` event when initial sync completes
```typescript
provider.on('sync', (isSynced: boolean) => {
  if (isSynced && dockviewApi.panels.length === 0) {
    autoReconnectLastSession(dockviewApi);
  }
});
```

B. **Check Yjs array before fallback** - Only auto-reconnect if Yjs array is explicitly empty (not just not-yet-loaded)

**Recommendation**: Option A is correct approach.

---

### 4. Full layout sync vs panel-only sync

**Problem**: Current plan only syncs panel existence and titles. It does NOT sync:
- Panel positions (which group, order)
- Split arrangements (horizontal/vertical splits)
- Panel sizes

**Options**:

A. **Accept limitation** - Panel-only sync is sufficient for basic use case

B. **Sync full Dockview JSON** - Store `api.toJSON()` in Yjs, but apply carefully to avoid destroying existing connections

C. **Sync group/position metadata** - Extend `PanelState` to include group info and position hints

**Recommendation**: Start with A, iterate if needed. Full layout sync (B) risks the "seamless" requirement.

---

### 5. Conflict: local panel close vs remote panel add

**Problem**: If browser A closes a panel while browser B is adding the same panel (race condition), what happens?

**Answer**: Yjs handles this automatically via CRDT - the operations are commutative. The final state will be consistent across all clients (either panel exists or doesn't). This is a feature of Yjs, not a bug.

---

## Notes

- The Yjs sync happens over a separate WebSocket connection (`/yjs`) from the terminal PTY connections (`/ws`)
- Panel positions/groups not synced in this basic implementation - only panel existence and titles
