#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#     "fastapi",
#     "uvicorn",
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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from authlib.integrations.starlette_client import OAuth
from itsdangerous import TimestampSigner, BadSignature
from urllib.parse import unquote
import base64
import json

load_dotenv()

ALLOWED_EMAIL = os.environ["ALLOWED_EMAIL"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
SESSION_SECRET = os.environ["SESSION_SECRET"]
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
    def __init__(self, socket_path: str, http_port: int) -> None:
        self._socket_path = socket_path
        self._http_port = http_port
        self._session_manager: SessionManager = SessionManager()
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._next_client_id: int = 0
        self._ws_clients: dict[int, WebSocket] = {}
        self._client_dimensions: dict[int, tuple[int, int]] = {}
        self._client_sessions: dict[int, int] = {}
        self._pty_tasks: dict[int, asyncio.Task[None]] = {}
        self.last_session_id: int | None = None

    async def start(self) -> None:
        socket_dir = os.path.dirname(self._socket_path)
        if not os.path.exists(socket_dir):
            os.makedirs(socket_dir, mode=0o700)

        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, self._signal_stop)
        loop.add_signal_handler(signal.SIGCHLD, self._reap_children)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        atexit.register(self._atexit_cleanup)

        import uvicorn

        config = uvicorn.Config(app, host="0.0.0.0", port=self._http_port, log_level="warning")
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())

        await self._shutdown_event.wait()

    async def stop(self) -> None:
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
                        await ws.send_bytes(output_msg.encode())
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
                    await ws.send_bytes(exit_msg.encode())
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

    async def handle_websocket_client(self, websocket: WebSocket, email: str) -> None:
        if email != ALLOWED_EMAIL:
            await websocket.close(4001)
            return

        await websocket.accept()

        client_id = self._next_client_id
        self._next_client_id += 1
        self._ws_clients[client_id] = websocket

        buffer = b""

        try:
            while True:
                raw_message = await websocket.receive_bytes()
                buffer += raw_message

                while True:
                    message, buffer = decode(buffer)
                    if message is None:
                        break
                    await self._dispatch_ws_message(client_id, message, websocket)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            error_msg = encode_error(str(e))
            try:
                await websocket.send_bytes(error_msg.encode())
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
        websocket: WebSocket,
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
                await websocket.send_bytes(info_msg.encode())

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
            self.last_session_id = session.id
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
            await websocket.send_bytes(info_msg.encode())

        elif message.msg_type == MessageType.ATTACH:
            session_id = decode_attach(message.payload)
            session = self._session_manager.find_session(session_id=session_id, name=None)
            if session is None:
                raise RuntimeError(f"Session {session_id} not found")
            pane = session.panes[session.active_pane_id]
            if pane.is_dead:
                exit_msg = encode_shell_exited(session.id, pane.id)
                await websocket.send_bytes(exit_msg.encode())
                return
            self._session_manager.attach_client(session.id, client_id)
            self.last_session_id = session.id
            self._client_sessions[client_id] = session.id
            screen_data = pane.render_to_ansi()
            output_msg = encode_output(screen_data)
            await websocket.send_bytes(output_msg.encode())
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
            await websocket.send_bytes(info_msg.encode())

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
            await websocket.send_bytes(error_msg.encode())


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
app.mount("/assets", StaticFiles(directory="."), name="assets")

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email"},
)

session_signer = TimestampSigner(SESSION_SECRET)

terminal_server: TerminalServer | None = None


def get_email_from_cookie(cookie_header: str | None) -> str | None:
    if cookie_header is None:
        return None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("session="):
            session_value = unquote(part[len("session="):])
            try:
                unsigned = session_signer.unsign(session_value.encode("utf-8"))
                data = json.loads(base64.b64decode(unsigned))
                return data.get("user")
            except (BadSignature, Exception):
                return None
    return None


@app.get("/api/last-session")
async def api_last_session(request: Request):
    if request.session.get("user") != ALLOWED_EMAIL:
        return {"session_id": None}
    if terminal_server is None:
        return {"session_id": None}
    return {"session_id": terminal_server.last_session_id}


@app.get("/api/sessions")
async def api_sessions(request: Request):
    if request.session.get("user") != ALLOWED_EMAIL:
        return {"sessions": []}
    if terminal_server is None:
        return {"sessions": []}
    result = []
    for session_id, name in terminal_server._session_manager.list_sessions():
        session = terminal_server._session_manager.find_session(session_id=session_id, name=None)
        if session is None:
            continue
        pane = session.panes[session.active_pane_id]
        attached_count = len(terminal_server._session_manager.get_attached_clients(session_id))
        result.append({
            "id": session.id,
            "name": session.name,
            "width": pane.width,
            "height": pane.height,
            "pid": pane.pid,
            "created_at": session.created_at,
            "attached_count": attached_count,
        })
    return {"sessions": result}


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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    if terminal_server is None:
        raise RuntimeError("Terminal server not initialized")
    cookie_header = websocket.headers.get("cookie")
    email = get_email_from_cookie(cookie_header)
    if email is None:
        await websocket.close(4001)
        return
    await terminal_server.handle_websocket_client(websocket, email)


@app.get("/", response_class=HTMLResponse)
async def terminal_ui(request: Request):
    if request.session.get("user") != ALLOWED_EMAIL:
        return RedirectResponse(url="/login")
    html = open("index.html").read()
    return html


# =============================================================================
# Main
# =============================================================================



async def run_server(socket_path: str, http_port: int) -> None:
    global terminal_server
    terminal_server = TerminalServer(socket_path=socket_path, http_port=http_port)
    await terminal_server.start()


if __name__ == "__main__":
    socket_path = get_socket_path()
    pid_file = get_pid_file_path()
    # daemonize(pid_file)
    asyncio.run(run_server(socket_path, HTTP_PORT))
