# Terminal Mux

A web-based terminal multiplexer with Google OAuth authentication. Access your terminal sessions from anywhere through a browser.

## Features

- Multi-tab terminal interface
- Session persistence (sessions survive page refresh)
- Google OAuth authentication (restrict access to your email)
- WebSocket-based real-time terminal

## Prerequisites

- Python 3.10+
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
WS_PORT=8866
HTTP_PORT=8045
```

### 4. Run

```bash
./index.py
```

Open http://localhost:8045

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
| `WS_PORT` | WebSocket server port (default: 8866) |

## License

MIT
