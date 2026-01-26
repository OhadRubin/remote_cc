#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#     "fastapi",
#     "uvicorn",
#     "websockets",
#     "pyte",
#     "authlib",
#     "httpx",
#     "itsdangerous",
#     "python-dotenv",
# ]
# ///
"""
Multi-tab terminal UI with inlined txtmux server.

Run:
    ./index.py

    # Then open http://localhost:8045
"""

import asyncio
import atexit
import fcntl
import os
import pty
import signal
import struct
import sys
import termios
import time
from dataclasses import dataclass, field
from enum import IntEnum

from dotenv import load_dotenv
import pyte
import websockets
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from authlib.integrations.starlette_client import OAuth
from itsdangerous import URLSafeSerializer
from websockets.asyncio.server import ServerConnection

load_dotenv()

ALLOWED_EMAIL = os.environ["ALLOWED_EMAIL"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
SESSION_SECRET = os.environ["SESSION_SECRET"]
WS_PORT = int(os.environ["WS_PORT"])
HTTP_PORT = int(os.environ["HTTP_PORT"])

# =============================================================================
# Protocol
# =============================================================================


class MessageType(IntEnum):
    IDENTIFY = 0
    NEW_SESSION = 1
    ATTACH = 2
    DETACH = 3
    LIST_SESSIONS = 4
    RESIZE = 5
    INPUT = 6
    OUTPUT = 7
    ERROR = 8
    SESSION_INFO = 9
    SHELL_EXITED = 10


HEADER_FORMAT = "!II"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


@dataclass
class Message:
    msg_type: MessageType
    payload: bytes

    def encode(self) -> bytes:
        header = struct.pack(HEADER_FORMAT, self.msg_type, len(self.payload))
        return header + self.payload


def decode(data: bytes) -> tuple[Message | None, bytes]:
    if len(data) < HEADER_SIZE:
        return (None, data)
    msg_type_int, payload_len = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    total_len = HEADER_SIZE + payload_len
    if len(data) < total_len:
        return (None, data)
    payload = data[HEADER_SIZE:total_len]
    remaining = data[total_len:]
    return (Message(MessageType(msg_type_int), payload), remaining)


def decode_identify(payload: bytes) -> tuple[int, int]:
    width, height = struct.unpack("!HH", payload)
    return (width, height)


def decode_new_session(payload: bytes) -> str:
    name_len = struct.unpack("!I", payload[:4])[0]
    name = payload[4 : 4 + name_len].decode("utf-8")
    return name


def decode_attach(payload: bytes) -> int:
    session_id: int = struct.unpack("!I", payload)[0]
    return session_id


def decode_resize(payload: bytes) -> tuple[int, int]:
    width, height = struct.unpack("!HH", payload)
    return (width, height)


def decode_input(payload: bytes) -> bytes:
    return payload


def encode_output(data: bytes) -> Message:
    return Message(MessageType.OUTPUT, data)


def encode_error(message: str) -> Message:
    msg_bytes = message.encode("utf-8")
    payload = struct.pack("!I", len(msg_bytes)) + msg_bytes
    return Message(MessageType.ERROR, payload)


def encode_session_info(
    session_id: int,
    name: str,
    pane_id: int,
    pid: int,
    width: int,
    height: int,
    created_at: float,
    attached_count: int,
) -> Message:
    name_bytes = name.encode("utf-8")
    payload = (
        struct.pack("!I", session_id)
        + struct.pack("!I", len(name_bytes))
        + name_bytes
        + struct.pack("!I", pane_id)
        + struct.pack("!I", pid)
        + struct.pack("!HH", width, height)
        + struct.pack("!d", created_at)
        + struct.pack("!I", attached_count)
    )
    return Message(MessageType.SESSION_INFO, payload)


def encode_shell_exited(session_id: int, pane_id: int) -> Message:
    payload = struct.pack("!II", session_id, pane_id)
    return Message(MessageType.SHELL_EXITED, payload)


# =============================================================================
# PTY Handler
# =============================================================================


def spawn_shell(shell: str) -> tuple[int, int]:
    master_fd, slave_fd = pty.openpty()
    pid = os.fork()
    if pid == 0:
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.environ["TERM"] = "xterm-256color"
        os.execvp(shell, [shell])
        raise RuntimeError("execvp failed")
    os.close(slave_fd)
    return (master_fd, pid)


def set_pty_size(fd: int, width: int, height: int) -> None:
    winsize = struct.pack("HHHH", height, width, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


async def read_pty(fd: int, size: int) -> bytes:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, os.read, fd, size)
    except OSError as e:
        raise OSError(f"Failed to read from PTY fd {fd}: {e}") from e


def write_pty(fd: int, data: bytes) -> int:
    try:
        return os.write(fd, data)
    except OSError as e:
        raise OSError(f"Failed to write to PTY fd {fd}: {e}") from e


def close_pty(fd: int) -> None:
    try:
        os.close(fd)
    except OSError as e:
        raise OSError(f"Failed to close PTY fd {fd}: {e}") from e


# =============================================================================
# Session Management
# =============================================================================


class Pane:
    def __init__(self, id: int, pty_fd: int, pid: int, width: int, height: int) -> None:
        self.id = id
        self.pty_fd = pty_fd
        self.pid = pid
        self.width = width
        self.height = height
        self.screen: pyte.HistoryScreen = pyte.HistoryScreen(height, width, history=2000)
        self.stream: pyte.Stream = pyte.Stream(self.screen)
        self.is_dead: bool = False
        self.exit_code: int | None = None

    def feed(self, data: bytes) -> None:
        self.stream.feed(data.decode("utf-8", errors="replace"))

    def resize_screen(self, width: int, height: int) -> None:
        self.screen.resize(height, width)

    def render_to_ansi(self) -> bytes:
        parts: list[bytes] = []
        for row in self.screen.history.top:
            line = "".join(row[col].data for col in sorted(row.keys()))
            parts.append(line.encode("utf-8", errors="replace"))
            parts.append(b"\r\n")
        parts.append(b"\x1b[H")
        for row, line in enumerate(self.screen.display):
            parts.append(f"\x1b[{row + 1};1H".encode("utf-8"))
            parts.append(line.encode("utf-8"))
        cx, cy = self.screen.cursor.x, self.screen.cursor.y
        parts.append(f"\x1b[{cy + 1};{cx + 1}H".encode("utf-8"))
        return b"".join(parts)


@dataclass
class Session:
    id: int
    name: str
    panes: dict[int, Pane] = field(repr=False)
    active_pane_id: int
    created_at: float


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[int, Session] = {}
        self._sessions_by_name: dict[str, Session] = {}
        self._attached_clients: dict[int, set[int]] = {}
        self._next_session_id: int = 0
        self._next_pane_id: int = 0

    def create_session(self, name: str, shell: str, width: int, height: int) -> Session:
        if name in self._sessions_by_name:
            raise ValueError(f"Session with name '{name}' already exists")
        session_id = self._next_session_id
        self._next_session_id += 1
        pane_id = self._next_pane_id
        self._next_pane_id += 1
        pty_fd, pid = spawn_shell(shell)
        set_pty_size(pty_fd, width, height)
        pane = Pane(id=pane_id, pty_fd=pty_fd, pid=pid, width=width, height=height)
        session = Session(
            id=session_id,
            name=name,
            panes={pane_id: pane},
            active_pane_id=pane_id,
            created_at=time.time(),
        )
        self._sessions[session_id] = session
        self._sessions_by_name[name] = session
        self._attached_clients[session_id] = set()
        return session

    def destroy_session(self, session_id: int) -> None:
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        session = self._sessions[session_id]
        for pane in session.panes.values():
            close_pty(pane.pty_fd)
            try:
                os.kill(pane.pid, signal.SIGTERM)
                os.waitpid(pane.pid, os.WNOHANG)
            except OSError:
                pass
        del self._sessions[session_id]
        del self._sessions_by_name[session.name]
        del self._attached_clients[session_id]

    def find_session(self, session_id: int | None, name: str | None) -> Session | None:
        if session_id is not None:
            return self._sessions.get(session_id)
        if name is not None:
            return self._sessions_by_name.get(name)
        raise ValueError("Must provide either session_id or name")

    def list_sessions(self) -> list[tuple[int, str]]:
        return [(s.id, s.name) for s in self._sessions.values()]

    def attach_client(self, session_id: int, client_id: int) -> None:
        if session_id not in self._attached_clients:
            raise KeyError(f"Session {session_id} not found")
        self._attached_clients[session_id].add(client_id)

    def detach_client(self, session_id: int, client_id: int) -> None:
        if session_id not in self._attached_clients:
            raise KeyError(f"Session {session_id} not found")
        self._attached_clients[session_id].discard(client_id)

    def get_attached_clients(self, session_id: int) -> set[int]:
        if session_id not in self._attached_clients:
            raise KeyError(f"Session {session_id} not found")
        return self._attached_clients[session_id].copy()


# =============================================================================
# WebSocket Server
# =============================================================================


def get_socket_path() -> str:
    tmpdir = os.environ.get("TMUX_TMPDIR")
    if tmpdir:
        return os.path.join(tmpdir, "default")
    uid = os.getuid()
    return f"/tmp/terminal-mux-{uid}/default"


def get_pid_file_path() -> str:
    socket_path = get_socket_path()
    return socket_path + ".pid"


class TerminalServer:
    def __init__(self, socket_path: str, ws_port: int, http_port: int) -> None:
        self._socket_path = socket_path
        self._ws_port = ws_port
        self._http_port = http_port
        self._session_manager: SessionManager = SessionManager()
        self._ws_server: websockets.asyncio.server.Server | None = None
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._next_client_id: int = 0
        self._ws_clients: dict[int, ServerConnection] = {}
        self._client_dimensions: dict[int, tuple[int, int]] = {}
        self._client_sessions: dict[int, int] = {}
        self._pty_tasks: dict[int, asyncio.Task[None]] = {}

    async def start(self) -> None:
        socket_dir = os.path.dirname(self._socket_path)
        if not os.path.exists(socket_dir):
            os.makedirs(socket_dir, mode=0o700)

        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, self._signal_stop)
        loop.add_signal_handler(signal.SIGCHLD, self._reap_children)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        atexit.register(self._atexit_cleanup)

        self._ws_server = await websockets.serve(
            self._handle_websocket_client,
            "0.0.0.0",
            self._ws_port,
        )

        import uvicorn

        config = uvicorn.Config(app, host="0.0.0.0", port=self._http_port, log_level="warning")
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())

        await self._shutdown_event.wait()

    async def stop(self) -> None:
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()

        for session_id, _ in list(self._session_manager.list_sessions()):
            session = self._session_manager.find_session(session_id=session_id, name=None)
            if session:
                for pane in session.panes.values():
                    try:
                        os.kill(pane.pid, signal.SIGKILL)
                    except OSError:
                        pass

        await asyncio.sleep(0.1)

        for task in self._pty_tasks.values():
            task.cancel()
        self._pty_tasks.clear()

        for session_id, _ in list(self._session_manager.list_sessions()):
            self._session_manager.destroy_session(session_id)

        for ws in self._ws_clients.values():
            await ws.close()
        self._ws_clients.clear()

        pid_file = get_pid_file_path()
        if os.path.exists(pid_file):
            os.unlink(pid_file)

        self._shutdown_event.set()

    def _signal_stop(self) -> None:
        asyncio.create_task(self.stop())

    def _reap_children(self) -> None:
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
            except ChildProcessError:
                break

    def _atexit_cleanup(self) -> None:
        pid_file = get_pid_file_path()
        if os.path.exists(pid_file):
            os.unlink(pid_file)

    async def _pty_forward_loop(self, session_id: int, pane_id: int) -> None:
        session = self._session_manager._sessions.get(session_id)
        if session is None:
            raise RuntimeError(f"Session {session_id} not found")
        pane = session.panes.get(pane_id)
        if pane is None:
            raise RuntimeError(f"Pane {pane_id} not found in session {session_id}")

        while True:
            try:
                data = await read_pty(pane.pty_fd, 4096)
            except OSError:
                break
            if not data:
                break

            pane.feed(data)

            output_msg = encode_output(data)
            client_ids = self._session_manager.get_attached_clients(session_id)
            for client_id in client_ids:
                ws = self._ws_clients.get(client_id)
                if ws:
                    try:
                        await ws.send(output_msg.encode())
                    except Exception:
                        self._remove_dead_client(client_id)

        pane.is_dead = True
        await self._broadcast_shell_exited(session_id, pane_id)

    async def _broadcast_shell_exited(self, session_id: int, pane_id: int) -> None:
        client_ids = self._session_manager.get_attached_clients(session_id)
        exit_msg = encode_shell_exited(session_id, pane_id)
        for client_id in client_ids:
            ws = self._ws_clients.get(client_id)
            if ws:
                try:
                    await ws.send(exit_msg.encode())
                except Exception:
                    self._remove_dead_client(client_id)

    def _remove_dead_client(self, client_id: int) -> None:
        session_id = self._client_sessions.pop(client_id, None)
        if session_id is not None:
            try:
                self._session_manager.detach_client(session_id, client_id)
            except KeyError:
                pass
        self._ws_clients.pop(client_id, None)
        self._client_dimensions.pop(client_id, None)

    def _start_pty_forwarding(self, session_id: int, pane_id: int) -> None:
        if session_id in self._pty_tasks:
            return
        task = asyncio.create_task(self._pty_forward_loop(session_id, pane_id))
        self._pty_tasks[session_id] = task

    async def _handle_websocket_client(self, websocket: ServerConnection) -> None:
        cookie_header = websocket.request.headers.get("Cookie")
        email = get_email_from_cookie(cookie_header)
        if email != ALLOWED_EMAIL:
            await websocket.close(4001, "Unauthorized")
            return

        client_id = self._next_client_id
        self._next_client_id += 1
        self._ws_clients[client_id] = websocket

        buffer = b""

        try:
            async for raw_message in websocket:
                if isinstance(raw_message, str):
                    data = raw_message.encode()
                else:
                    data = raw_message

                buffer += data

                while True:
                    message, buffer = decode(buffer)
                    if message is None:
                        break
                    await self._dispatch_ws_message(client_id, message, websocket)

        except websockets.ConnectionClosed:
            pass
        except Exception as e:
            error_msg = encode_error(str(e))
            try:
                await websocket.send(error_msg.encode())
            except Exception:
                pass
        finally:
            session_id = self._client_sessions.pop(client_id, None)
            if session_id is not None:
                self._session_manager.detach_client(session_id, client_id)
            self._ws_clients.pop(client_id, None)
            self._client_dimensions.pop(client_id, None)

    async def _dispatch_ws_message(
        self,
        client_id: int,
        message: Message,
        websocket: ServerConnection,
    ) -> None:
        if message.msg_type == MessageType.IDENTIFY:
            width, height = decode_identify(message.payload)
            self._client_dimensions[client_id] = (width, height)

        elif message.msg_type == MessageType.LIST_SESSIONS:
            sessions = self._session_manager.list_sessions()
            for session_id, name in sessions:
                session = self._session_manager.find_session(session_id=session_id, name=None)
                if session is None:
                    raise RuntimeError(f"Session {session_id} not found")
                pane = session.panes[session.active_pane_id]
                attached_count = len(self._session_manager.get_attached_clients(session_id))
                info_msg = encode_session_info(
                    session_id=session.id,
                    name=session.name,
                    pane_id=pane.id,
                    pid=pane.pid,
                    width=pane.width,
                    height=pane.height,
                    created_at=session.created_at,
                    attached_count=attached_count,
                )
                await websocket.send(info_msg.encode())

        elif message.msg_type == MessageType.NEW_SESSION:
            name = decode_new_session(message.payload)
            if not name:
                existing = self._session_manager.list_sessions()
                if not existing:
                    name = "main"
                else:
                    existing_names = {n for _, n in existing}
                    i = 1
                    while f"session-{i}" in existing_names:
                        i += 1
                    name = f"session-{i}"
            dimensions = self._client_dimensions.get(client_id)
            if dimensions is None:
                raise RuntimeError("Client must send IDENTIFY before NEW_SESSION")
            width, height = dimensions
            shell = os.environ.get("SHELL", "/bin/sh")
            session = self._session_manager.create_session(
                name=name,
                shell=shell,
                width=width,
                height=height,
            )
            self._session_manager.attach_client(session.id, client_id)
            self._client_sessions[client_id] = session.id
            pane = session.panes[session.active_pane_id]
            self._start_pty_forwarding(session.id, pane.id)
            attached_count = len(self._session_manager.get_attached_clients(session.id))
            info_msg = encode_session_info(
                session_id=session.id,
                name=session.name,
                pane_id=pane.id,
                pid=pane.pid,
                width=pane.width,
                height=pane.height,
                created_at=session.created_at,
                attached_count=attached_count,
            )
            await websocket.send(info_msg.encode())

        elif message.msg_type == MessageType.ATTACH:
            session_id = decode_attach(message.payload)
            session = self._session_manager.find_session(session_id=session_id, name=None)
            if session is None:
                raise RuntimeError(f"Session {session_id} not found")
            pane = session.panes[session.active_pane_id]
            if pane.is_dead:
                exit_msg = encode_shell_exited(session.id, pane.id)
                await websocket.send(exit_msg.encode())
                return
            self._session_manager.attach_client(session.id, client_id)
            self._client_sessions[client_id] = session.id
            screen_data = pane.render_to_ansi()
            output_msg = encode_output(screen_data)
            await websocket.send(output_msg.encode())
            self._start_pty_forwarding(session.id, pane.id)
            attached_count = len(self._session_manager.get_attached_clients(session.id))
            info_msg = encode_session_info(
                session_id=session.id,
                name=session.name,
                pane_id=pane.id,
                pid=pane.pid,
                width=pane.width,
                height=pane.height,
                created_at=session.created_at,
                attached_count=attached_count,
            )
            await websocket.send(info_msg.encode())

        elif message.msg_type == MessageType.INPUT:
            session_id_opt: int | None = self._client_sessions.get(client_id)
            if session_id_opt is None:
                raise RuntimeError("Client not attached to any session")
            session_id = session_id_opt
            session = self._session_manager.find_session(session_id=session_id, name=None)
            if session is None:
                raise RuntimeError(f"Session {session_id} not found")
            pane = session.panes[session.active_pane_id]
            data = decode_input(message.payload)
            write_pty(pane.pty_fd, data)

        elif message.msg_type == MessageType.RESIZE:
            session_id_opt = self._client_sessions.get(client_id)
            if session_id_opt is None:
                raise RuntimeError("Client not attached to any session")
            session_id = session_id_opt
            session = self._session_manager.find_session(session_id=session_id, name=None)
            if session is None:
                raise RuntimeError(f"Session {session_id} not found")
            pane = session.panes[session.active_pane_id]
            width, height = decode_resize(message.payload)
            pane.width = width
            pane.height = height
            set_pty_size(pane.pty_fd, width, height)
            pane.resize_screen(width, height)

        elif message.msg_type == MessageType.DETACH:
            session_id_opt = self._client_sessions.get(client_id)
            if session_id_opt is not None:
                session_id = session_id_opt
                self._session_manager.detach_client(session_id, client_id)
                del self._client_sessions[client_id]

        else:
            error_msg = encode_error(f"Unhandled message type: {message.msg_type.name}")
            await websocket.send(error_msg.encode())


def daemonize(pid_file_path: str) -> None:
    pid_dir = os.path.dirname(pid_file_path)
    if not os.path.exists(pid_dir):
        os.makedirs(pid_dir, mode=0o700)

    pid = os.fork()
    if pid > 0:
        os._exit(0)

    os.setsid()

    pid = os.fork()
    if pid > 0:
        os._exit(0)

    sys.stdin.close()
    sys.stdout.close()
    sys.stderr.close()

    null_fd = os.open("/dev/null", os.O_RDWR)
    os.dup2(null_fd, 0)
    os.dup2(null_fd, 1)
    os.dup2(null_fd, 2)
    if null_fd > 2:
        os.close(null_fd)

    with open(pid_file_path, "w") as f:
        f.write(str(os.getpid()))


# =============================================================================
# FastAPI Web UI
# =============================================================================

app = FastAPI(title="Terminal")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email"},
)

session_serializer = URLSafeSerializer(SESSION_SECRET, "cookie-session")


def get_email_from_cookie(cookie_header: str | None) -> str | None:
    if cookie_header is None:
        return None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("session="):
            session_value = part[len("session="):]
            try:
                data = session_serializer.loads(session_value)
                return data.get("user")
            except Exception:
                return None
    return None


@app.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/callback")
async def callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")
    if user_info is None:
        raise RuntimeError("No userinfo in token response")
    if user_info.get("email") != ALLOWED_EMAIL:
        return HTMLResponse("Unauthorized email", status_code=403)
    request.session["user"] = user_info["email"]
    return RedirectResponse(url="/")


@app.get("/", response_class=HTMLResponse)
async def terminal_ui(request: Request):
    if request.session.get("user") != ALLOWED_EMAIL:
        return RedirectResponse(url="/login")
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Terminal</title>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css">
        <style>
            * { box-sizing: border-box; }
            body {
                margin: 0;
                padding: 0;
                background: #1a1a2e;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
                color: #ccc;
                height: 100vh;
                display: flex;
                flex-direction: column;
            }
            #config-panel {
                padding: 12px 16px;
                background: #16213e;
                border-bottom: 1px solid #333;
                display: flex;
                flex-wrap: wrap;
                gap: 12px;
                align-items: center;
            }
            #config-panel button {
                background: #28a745;
                color: #fff;
                border: none;
                padding: 8px 16px;
                cursor: pointer;
                font-family: monospace;
                font-size: 12px;
                border-radius: 3px;
            }
            #config-panel button:hover { background: #218838; }
            #config-panel button.danger { background: #dc3545; }
            #config-panel button.danger:hover { background: #c82333; }
            #config-panel .hidden { display: none; }
            #status {
                padding: 6px 16px;
                background: #0f0f23;
                font-size: 11px;
                color: #888;
                border-bottom: 1px solid #333;
            }
            #panes-container {
                flex: 1;
                display: grid;
                gap: 2px;
                background: #000;
                overflow: hidden;
            }
            .pane {
                background: #1a1a2e;
                display: flex;
                flex-direction: column;
                overflow: hidden;
            }
            .pane-header {
                background: #16213e;
                padding: 4px 8px;
                font-size: 11px;
                color: #aaa;
                border-bottom: 1px solid #333;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .pane-header.active { background: #28a745; color: #fff; }
            .pane-header .close-btn {
                background: transparent;
                border: none;
                color: #888;
                cursor: pointer;
                font-size: 14px;
                padding: 0 4px;
            }
            .pane-header .close-btn:hover { color: #dc3545; }
            .pane-terminal {
                flex: 1;
                overflow: hidden;
            }
            .modal {
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0,0,0,0.7);
                display: flex;
                justify-content: center;
                align-items: center;
                z-index: 1000;
            }
            .modal.hidden { display: none; }
            .modal-content {
                background: #16213e;
                border-radius: 8px;
                min-width: 400px;
                max-width: 600px;
                max-height: 80vh;
                overflow: hidden;
                display: flex;
                flex-direction: column;
            }
            .modal-header {
                padding: 12px 16px;
                background: #0f0f23;
                display: flex;
                justify-content: space-between;
                align-items: center;
                font-weight: bold;
            }
            .close-modal-btn {
                background: transparent;
                border: none;
                color: #888;
                font-size: 20px;
                cursor: pointer;
            }
            .close-modal-btn:hover { color: #dc3545; }
            #sessions-list {
                padding: 12px;
                overflow-y: auto;
                flex: 1;
            }
            .session-item {
                padding: 10px 12px;
                background: #1a1a2e;
                border-radius: 4px;
                margin-bottom: 8px;
                cursor: pointer;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .session-item:hover { background: #28a745; }
            .session-item .session-name { font-weight: bold; }
            .session-item .session-meta { font-size: 11px; color: #888; }
            .session-item:hover .session-meta { color: #ccc; }
            .modal-footer {
                padding: 12px 16px;
                background: #0f0f23;
                text-align: right;
            }
            .modal-footer button {
                background: #444;
                color: #fff;
                border: none;
                padding: 8px 16px;
                cursor: pointer;
                border-radius: 3px;
            }
            .modal-footer button:hover { background: #555; }
            .no-sessions { color: #888; text-align: center; padding: 20px; }
        </style>
    </head>
    <body>
        <div id="config-panel">
            <button id="add-btn">+ New Tab</button>
            <button id="sessions-btn">Sessions</button>
            <button id="close-btn" class="danger hidden">Close All</button>
        </div>
        <div id="sessions-modal" class="modal hidden">
            <div class="modal-content">
                <div class="modal-header">
                    <span>Available Sessions</span>
                    <button class="close-modal-btn">×</button>
                </div>
                <div id="sessions-list"></div>
                <div class="modal-footer">
                    <button id="refresh-sessions-btn">Refresh</button>
                </div>
            </div>
        </div>
        <div id="status">Click "New Tab" to open a terminal.</div>
        <div id="panes-container"></div>

        <script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"></script>
        <script>
            const wsUrl = `ws://${window.location.hostname}:8866`;
            const panes = [];
            let activePane = null;
            let tabCounter = 0;

            const MessageType = {
                IDENTIFY: 0, NEW_SESSION: 1, ATTACH: 2, DETACH: 3,
                LIST_SESSIONS: 4, RESIZE: 5, INPUT: 6, OUTPUT: 7,
                ERROR: 8, SESSION_INFO: 9, SHELL_EXITED: 10,
            };

            function encodeMessage(type, payload) {
                const payloadBytes = payload instanceof Uint8Array ? payload : new TextEncoder().encode(payload);
                const buffer = new ArrayBuffer(8 + payloadBytes.length);
                const view = new DataView(buffer);
                view.setUint32(0, type, false);
                view.setUint32(4, payloadBytes.length, false);
                new Uint8Array(buffer, 8).set(payloadBytes);
                return new Uint8Array(buffer);
            }

            function encodeIdentify(width, height) {
                const payload = new ArrayBuffer(4);
                const view = new DataView(payload);
                view.setUint16(0, width, false);
                view.setUint16(2, height, false);
                return encodeMessage(MessageType.IDENTIFY, new Uint8Array(payload));
            }

            function encodeNewSession(name) {
                const nameBytes = new TextEncoder().encode(name);
                const payload = new ArrayBuffer(4 + nameBytes.length);
                const view = new DataView(payload);
                view.setUint32(0, nameBytes.length, false);
                new Uint8Array(payload, 4).set(nameBytes);
                return encodeMessage(MessageType.NEW_SESSION, new Uint8Array(payload));
            }

            function encodeInput(data) {
                return encodeMessage(MessageType.INPUT, data);
            }

            function encodeResize(width, height) {
                const payload = new ArrayBuffer(4);
                const view = new DataView(payload);
                view.setUint16(0, width, false);
                view.setUint16(2, height, false);
                return encodeMessage(MessageType.RESIZE, new Uint8Array(payload));
            }

            function encodeAttach(sessionId) {
                const payload = new ArrayBuffer(4);
                const view = new DataView(payload);
                view.setUint32(0, sessionId, false);
                return encodeMessage(MessageType.ATTACH, new Uint8Array(payload));
            }

            function encodeListSessions() {
                return encodeMessage(MessageType.LIST_SESSIONS, new Uint8Array(0));
            }

            function decodeMessages(buffer) {
                const messages = [];
                let offset = 0;
                while (offset + 8 <= buffer.byteLength) {
                    const view = new DataView(buffer, offset);
                    const type = view.getUint32(0, false);
                    const length = view.getUint32(4, false);
                    if (offset + 8 + length > buffer.byteLength) break;
                    const payload = new Uint8Array(buffer, offset + 8, length);
                    messages.push({ type, payload });
                    offset += 8 + length;
                }
                return { messages, remaining: buffer.slice(offset) };
            }

            function updateGridLayout() {
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

            function createPane() {
                tabCounter++;
                const tabName = `Tab ${tabCounter}`;

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

            function closePane(pane) {
                if (pane.ws) pane.ws.close();
                pane.paneDiv.remove();
                const idx = panes.indexOf(pane);
                if (idx > -1) panes.splice(idx, 1);
                if (activePane === pane) {
                    activePane = panes.length > 0 ? panes[0] : null;
                    if (activePane) setActivePane(activePane);
                }
                updateGridLayout();
                if (panes.length === 0) {
                    document.getElementById('close-btn').classList.add('hidden');
                    document.getElementById('status').textContent = 'All tabs closed.';
                } else {
                    document.getElementById('status').textContent = `${panes.length} tab(s) open`;
                }
            }

            function setActivePane(pane) {
                if (activePane) {
                    activePane.header.classList.remove('active');
                }
                activePane = pane;
                pane.header.classList.add('active');
                pane.term.focus();
            }

            function connectPane(pane) {
                try {
                    pane.ws = new WebSocket(wsUrl);
                } catch (e) {
                    pane.term.write(`\\x1b[31mFailed to connect: ${e.message}\\x1b[0m\\r\\n`);
                    return;
                }

                pane.ws.binaryType = 'arraybuffer';

                pane.ws.onopen = () => {
                    pane.term.write(`\\x1b[33mConnecting...\\x1b[0m\\r\\n`);
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
                                pane.term.write(`\\x1b[32mSession ready.\\x1b[0m\\r\\n`);
                            }
                        } else if (msg.type === MessageType.ERROR) {
                            const text = new TextDecoder().decode(msg.payload);
                            pane.term.write(`\\x1b[31mError: ${text}\\x1b[0m\\r\\n`);
                        } else if (msg.type === MessageType.SHELL_EXITED) {
                            pane.term.write(`\\x1b[33mShell exited\\x1b[0m\\r\\n`);
                            pane.sessionActive = false;
                        }
                    }
                };

                pane.ws.onclose = () => {
                    pane.term.write(`\\x1b[31mDisconnected\\x1b[0m\\r\\n`);
                    pane.sessionActive = false;
                };

                pane.ws.onerror = () => {
                    pane.term.write(`\\x1b[31mConnection error\\x1b[0m\\r\\n`);
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

            function closeAllPanes() {
                panes.forEach(pane => {
                    if (pane.ws) pane.ws.close();
                    pane.paneDiv.remove();
                });
                panes.length = 0;
                activePane = null;
                document.getElementById('close-btn').classList.add('hidden');
                document.getElementById('status').textContent = 'All tabs closed.';
            }

            function decodeSessionInfo(payload) {
                const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
                let offset = 0;
                const sessionId = view.getUint32(offset, false); offset += 4;
                const nameLen = view.getUint32(offset, false); offset += 4;
                const name = new TextDecoder().decode(payload.slice(offset, offset + nameLen)); offset += nameLen;
                const paneId = view.getUint32(offset, false); offset += 4;
                const pid = view.getUint32(offset, false); offset += 4;
                const width = view.getUint16(offset, false); offset += 2;
                const height = view.getUint16(offset, false); offset += 2;
                const createdAt = view.getFloat64(offset, false); offset += 8;
                const attachedCount = view.getUint32(offset, false);
                return { sessionId, name, paneId, pid, width, height, createdAt, attachedCount };
            }

            function showSessionsModal() {
                document.getElementById('sessions-modal').classList.remove('hidden');
                refreshSessionsList();
            }

            function hideSessionsModal() {
                document.getElementById('sessions-modal').classList.add('hidden');
            }

            function refreshSessionsList() {
                const listEl = document.getElementById('sessions-list');
                listEl.innerHTML = '<div class="no-sessions">Loading...</div>';

                const ws = new WebSocket(wsUrl);
                ws.binaryType = 'arraybuffer';
                const sessions = [];
                let recvBuffer = new ArrayBuffer(0);

                ws.onopen = () => {
                    ws.send(encodeIdentify(80, 24));
                    ws.send(encodeListSessions());
                };

                ws.onmessage = (event) => {
                    const combined = new Uint8Array(recvBuffer.byteLength + event.data.byteLength);
                    combined.set(new Uint8Array(recvBuffer), 0);
                    combined.set(new Uint8Array(event.data), recvBuffer.byteLength);
                    const { messages, remaining } = decodeMessages(combined.buffer);
                    recvBuffer = remaining;

                    for (const msg of messages) {
                        if (msg.type === MessageType.SESSION_INFO) {
                            sessions.push(decodeSessionInfo(msg.payload));
                        }
                    }
                };

                setTimeout(() => {
                    ws.close();
                    renderSessionsList(sessions);
                }, 300);
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

            function attachToSession(sessionId) {
                tabCounter++;
                const tabName = `Tab ${tabCounter} (reconnect)`;

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
                    term.write('\\x1b[33mReconnecting to session...\\x1b[0m\\r\\n');
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
                                term.write('\\x1b[32mReconnected!\\x1b[0m\\r\\n');
                            }
                        } else if (msg.type === MessageType.ERROR) {
                            const errLen = new DataView(msg.payload.buffer, msg.payload.byteOffset).getUint32(0, false);
                            const errText = new TextDecoder().decode(msg.payload.slice(4, 4 + errLen));
                            term.write(`\\x1b[31mError: ${errText}\\x1b[0m\\r\\n`);
                        } else if (msg.type === MessageType.SHELL_EXITED) {
                            term.write('\\x1b[33mShell exited\\x1b[0m\\r\\n');
                            pane.sessionActive = false;
                        }
                    }
                };

                pane.ws.onclose = () => {
                    term.write('\\x1b[31mDisconnected\\x1b[0m\\r\\n');
                    pane.sessionActive = false;
                };

                pane.ws.onerror = () => {
                    term.write('\\x1b[31mConnection error\\x1b[0m\\r\\n');
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

            document.addEventListener('keydown', (e) => {
                if (e.altKey && panes.length > 1) {
                    const currentIdx = panes.indexOf(activePane);
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
        </script>
    </body>
    </html>
    """
    return html


# =============================================================================
# Main
# =============================================================================



async def run_server(socket_path: str, ws_port: int, http_port: int) -> None:
    server = TerminalServer(socket_path=socket_path, ws_port=ws_port, http_port=http_port)
    await server.start()


if __name__ == "__main__":
    socket_path = get_socket_path()
    pid_file = get_pid_file_path()
    # daemonize(pid_file)
    asyncio.run(run_server(socket_path, WS_PORT, HTTP_PORT))
