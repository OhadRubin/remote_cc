# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Terminal Mux is a web-based terminal multiplexer with Google OAuth authentication. It provides browser-accessible terminal sessions with persistence, multi-tab UI, and real-time WebSocket communication.

## Commands

### Running the Application

```bash
./index.py                    # Runs backend (auto-builds frontend if needed)
```

### Frontend Development

```bash
cd frontend
npm install                   # Install dependencies
npm run dev                   # Dev server with HMR on :5173 (proxies to backend :8045)
npm run build                 # Production build to dist/
npm run lint                  # ESLint
```

## Architecture

### Backend (`index.py`)

Single-file Python backend using FastAPI with uv script dependencies (no requirements.txt). Key sections:

- **Protocol** (lines 165-267): Binary WebSocket message encoding/decoding using `struct` format `!II` (type + length + payload)
- **PTY Utilities** (lines 274-298): PTY size setting, async read/write helpers
- **Tmux Integration** (lines 306-424): Session create/kill/rename, spawning `tmux attach-session` in a PTY
- **Terminal Server** (lines 455-813): `TerminalServer` class - WebSocket handler, PTY forwarding loop, client lifecycle. Each client gets its own PTY sized to its dimensions; tmux handles multi-client sizing internally via `aggressive-resize`.
- **FastAPI Routes** (lines 936-1085): HTTP endpoints, OAuth, static file serving

### REST API Endpoints

- `GET /api/sessions` - List all sessions with metadata
- `GET /api/last-session` - Get last active session ID
- `DELETE /api/sessions/{id}` - Kill a session (terminates shell process)
- `PATCH /api/sessions/{id}` - Rename a session (body: `{"name": "new-name"}`)

### Frontend (`frontend/src/`)

React + TypeScript + Vite application:

- **App.tsx**: Root component with Dockview layout manager, keyboard nav (Alt+arrows/hjkl), layout persistence, header actions integrated into tab bar
- **hooks/useTerminalSession.ts**: Core hook managing xterm.js terminal and WebSocket connection per panel
- **hooks/useLayoutSync.ts**: Yjs-based real-time layout synchronization across browser tabs/windows
- **protocol.ts**: Binary protocol encode/decode functions (mirrors backend)
- **components/**:
  - `TerminalPanel.tsx` - Dockview panel wrapper for xterm.js terminal
  - `SessionsSidebar.tsx` - Collapsible sidebar showing all sessions with orphaned indicators (⚠) and delete buttons
  - `ContextMenu.tsx` - Right-click context menu for tabs (Rename, Kill, Close Tab)
  - `StatusBar.tsx` - Bottom bar showing connection status and tab count

### Communication Flow

1. Browser opens WebSocket to `/ws`
2. Client sends `IDENTIFY` (terminal dimensions)
3. Client sends `NEW_SESSION` or `ATTACH` (session ID)
4. Server spawns/attaches PTY, sends `SESSION_INFO`
5. Bidirectional: `INPUT` → server → PTY, PTY → server → `OUTPUT`
6. Each terminal panel maintains its own WebSocket connection

### Message Types (shared protocol)

```
IDENTIFY=0, NEW_SESSION=1, ATTACH=2, DETACH=3, LIST_SESSIONS=4,
RESIZE=5, INPUT=6, OUTPUT=7, ERROR=8, SESSION_INFO=9, SHELL_EXITED=10
```

### Layout Synchronization (Yjs)

Real-time layout sync across browser tabs using Yjs CRDT:

- **WebSocket endpoint**: `/yjs` - y-websocket provider connection
- **State storage**: Dockview's `toJSON()` serialization stored in Y.Map
- **Sync mechanism**:
  1. Local changes → serialize via `toJSON()` → save to Yjs
  2. Remote changes → apply via `fromJSON()` with `{ reuseExistingPanels: true }`
- **Echo prevention**: `lastSavedRef` tracks last saved/applied state to prevent loops
- **Panel preservation**: `reuseExistingPanels` option keeps existing terminal connections alive during layout updates

## Configuration

Environment variables in `.env` (copy from `.env.example`):

- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`: OAuth credentials
- `SESSION_SECRET`: Cookie signing key
- `ALLOWED_EMAIL`: Single email with access
- `HTTP_PORT`: Server port (default 8045)

## UI Features

- **Tab Bar Actions**: Integrated into Dockview header (+ button, menu icon, maximize, close all)
- **Right-Click Context Menu**: On tabs for Rename, Kill, Close Tab
- **Sessions Sidebar**: Click menu icon to open; shows all sessions with status indicators
- **Keyboard Navigation**: Alt+arrows or Alt+hjkl to switch tabs
- **Empty State**: When no panels exist, centered buttons appear to create tab or open sessions

## Key Dependencies

- **Backend**: FastAPI, uvicorn, pyte (terminal emulation), authlib (OAuth)
- **Frontend**: React 19, Dockview (tab/split UI), xterm.js (terminal rendering), Yjs + y-websocket (layout sync), Material Symbols (icons)
