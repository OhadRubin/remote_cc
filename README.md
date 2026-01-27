# Terminal Mux

A web-based terminal multiplexer with Google OAuth authentication. Access your terminal sessions from anywhere through a browser.

## Features

- **Dockview-powered UI** - Drag tabs to reorder, split into groups, pop out to new windows
- **Session persistence** - Sessions survive page refresh, layout saved to localStorage
- **Keyboard navigation** - Alt+arrows/hjkl to switch between tabs
- **Auto-reconnect** - Automatically reconnects to last session on page load
- **Google OAuth** - Restrict access to your email

## Prerequisites

- Python 3.10+
- Node.js 18+ (for frontend build)
- [uv](https://github.com/astral-sh/uv) package manager
- Google Cloud account (for OAuth)

## Setup

### 1. Clone and configure

```bash
cp .env.example .env
```

### 2. Set up Google OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. Create a new project (or select existing)
3. Navigate to **APIs & Services > OAuth consent screen**
   - Choose "External" user type
   - Fill in app name and your email
   - Add your email as a test user
4. Navigate to **APIs & Services > Credentials**
   - Click **Create Credentials > OAuth client ID**
   - Choose "Web application"
   - Add authorized redirect URI: `http://localhost:8045/callback` (and/or your domain)
5. Copy the Client ID and Client Secret

### 3. Configure .env

```bash
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
SESSION_SECRET=run-python-c-"import secrets; print(secrets.token_urlsafe(32))"
ALLOWED_EMAIL=your-email@gmail.com
HTTP_PORT=8045
```

### 4. Run

```bash
./index.py
```

The server auto-builds the frontend on first run (or when sources change).

Open http://localhost:8045

## Development

For frontend development with hot reload:

```bash
cd frontend
npm install
npm run dev
```

This runs Vite dev server on port 5173 with proxy to the backend.

## Remote Access (Cloudflare Tunnel)

For secure remote access with a stable URL:

1. Install [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)

2. Authenticate with Cloudflare:
   ```bash
   cloudflared tunnel login
   ```

3. Create a tunnel:
   ```bash
   cloudflared tunnel create terminal-mux
   ```

4. Configure DNS (replace with your subdomain):
   ```bash
   cloudflared tunnel route dns terminal-mux your-subdomain.yourdomain.com
   ```

5. Run the tunnel:
   ```bash
   cloudflared tunnel --url http://localhost:8045 run terminal-mux
   ```

6. Add `https://your-subdomain.yourdomain.com/callback` to your Google OAuth redirect URIs

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `SESSION_SECRET` | Secret for session encryption |
| `ALLOWED_EMAIL` | Email address allowed to access |
| `HTTP_PORT` | HTTP server port (default: 8045) |

## Tech Stack

- **Backend**: Python/FastAPI, WebSocket binary protocol
- **Frontend**: React, TypeScript, Vite, Dockview, xterm.js

## License

MIT
