#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#     "fastapi",
#     "uvicorn[standard]",
#     "authlib",
#     "httpx",
#     "itsdangerous",
#     "python-dotenv",
#     "pycrdt",
#     "httpx-ws",
#     "hypercorn",
#     "anyio",
#     "sniffio",
#     "pytest",
#     "pycrdt-websocket @ git+https://github.com/y-crdt/pycrdt-websocket",
#     "pycrdt-store",
# ]
# ///
"""
Multi-tab terminal UI using tmux as backend.

Run:
./index.py

    # Then open http://localhost:8045
"""

import argparse
import asyncio
import atexit
import fcntl
import os
import pty
import signal
import struct
import subprocess
import sys
import termios
import time
from dataclasses import dataclass
from enum import IntEnum

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from authlib.integrations.starlette_client import OAuth
from itsdangerous import TimestampSigner, BadSignature
from urllib.parse import unquote
import base64
import json
from contextlib import asynccontextmanager
from pathlib import Path
from pycrdt.websocket import ASGIServer, WebsocketServer
from pycrdt.store import FileYStore

load_dotenv()

LOCAL_MODE = False

def load_config():
    global ALLOWED_EMAIL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SESSION_SECRET, HTTP_PORT
    if LOCAL_MODE:
        ALLOWED_EMAIL = "local@localhost"
        GOOGLE_CLIENT_ID = ""
        GOOGLE_CLIENT_SECRET = ""
        SESSION_SECRET = "local-dev-secret"
        HTTP_PORT = int(os.environ.get("HTTP_PORT", "8045"))
    else:
        ALLOWED_EMAIL = os.environ["ALLOWED_EMAIL"]
        GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
        GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
        SESSION_SECRET = os.environ["SESSION_SECRET"]
        HTTP_PORT = int(os.environ["HTTP_PORT"])

ALLOWED_EMAIL = ""
GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""
SESSION_SECRET = ""
HTTP_PORT = 8045

# =============================================================================
# Yjs Layout Sync
# =============================================================================

LAYOUT_STORE_PATH = Path(__file__).parent / ".yjs_store"


class PersistentWebsocketServer(WebsocketServer):
    """WebsocketServer that persists room data to disk using FileYStore."""

    async def get_room(self, name: str):
        from pycrdt.websocket.yroom import YRoom
        from functools import partial

        if name not in self.rooms.keys():
            safe_name = name.lstrip("/").replace("/", "_")
            store_path = LAYOUT_STORE_PATH / f"{safe_name}.ystore"
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
yjs_task: asyncio.Task | None = None

# =============================================================================
# Frontend Build
# =============================================================================

import subprocess

def _ensure_frontend_built() -> None:
    frontend_dir = Path(__file__).parent / "frontend"
    dist_dir = frontend_dir / "dist"
    dist_index = dist_dir / "index.html"

    if not dist_index.exists():
        print("Building frontend...")
        subprocess.run(["npm", "install"], cwd=frontend_dir, check=True)
        subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
        return

    src_dir = frontend_dir / "src"
    subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
    # if src_dir.exists():
    #     src_files = list(src_dir.rglob("*"))
    #     if src_files:
    #         src_mtime = max(f.stat().st_mtime for f in src_files if f.is_file())
    #         dist_mtime = dist_index.stat().st_mtime
    #         if src_mtime > dist_mtime:
    #             print("Frontend sources changed, rebuilding...")

def ensure_frontend_built() -> None:
    try:
        _ensure_frontend_built()
    except Exception as e:
        print(f"Failed to build frontend, wiping dist directory and retrying...")
        import shutil
        shutil.rmtree(Path(__file__).parent / "frontend" / "dist", ignore_errors=True)
        os.makedirs("frontend/dist/assets", exist_ok=True)
        _ensure_frontend_built()
        print("Frontend built successfully")

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
# PTY Utilities
# =============================================================================


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
# Tmux Integration
# =============================================================================


TMUX_SOCKET_NAME = "terminal-mux"


def _run_tmux(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a tmux command with our socket name."""
    cmd = ["tmux", "-L", TMUX_SOCKET_NAME] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def tmux_session_exists(session_name: str) -> bool:
    """Check if a tmux session exists."""
    result = _run_tmux("has-session", "-t", session_name)
    return result.returncode == 0


def tmux_create_session(session_name: str, width: int, height: int) -> None:
    """Create a new tmux session (detached)."""
    result = _run_tmux(
        "new-session",
        "-d",
        "-s", session_name,
        "-x", str(width),
        "-y", str(height),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create tmux session: {result.stderr}")
    _run_tmux("set-option", "-t", session_name, "aggressive-resize", "on")
    _run_tmux("set-option", "-g", "window-size", "largest")


def tmux_kill_session(session_name: str) -> None:
    """Kill a tmux session."""
    result = _run_tmux("kill-session", "-t", session_name)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to kill tmux session: {result.stderr}")


def tmux_rename_session(old_name: str, new_name: str) -> None:
    """Rename a tmux session."""
    result = _run_tmux("rename-session", "-t", old_name, new_name)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to rename tmux session: {result.stderr}")


def tmux_resize_window(session_name: str, width: int, height: int) -> None:
    """Resize the active window of a tmux session."""
    _run_tmux("resize-window", "-t", session_name, "-x", str(width), "-y", str(height))


@dataclass
class TmuxSessionInfo:
    name: str
    created_at: float
    width: int
    height: int
    pid: int


def tmux_list_sessions() -> list[TmuxSessionInfo]:
    """List all tmux sessions with metadata."""
    result = _run_tmux(
        "list-sessions",
        "-F", "#{session_name}\t#{session_created}\t#{window_width}\t#{window_height}\t#{pane_pid}",
    )
    if result.returncode != 0:
        return []

    sessions = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 5:
            sessions.append(TmuxSessionInfo(
                name=parts[0],
                created_at=float(parts[1]),
                width=int(parts[2]),
                height=int(parts[3]),
                pid=int(parts[4]),
            ))
    return sessions


def tmux_get_session(session_name: str) -> TmuxSessionInfo | None:
    """Get info for a specific tmux session."""
    for s in tmux_list_sessions():
        if s.name == session_name:
            return s
    return None


def spawn_tmux_attach(session_name: str, width: int, height: int) -> tuple[int, int]:
    """
    Spawn a PTY running 'tmux attach-session -t <name>'.
    Creates the session first if it doesn't exist.
    Returns (master_fd, pid).
    """
    if not tmux_session_exists(session_name):
        tmux_create_session(session_name, width, height)

    master_fd, slave_fd = pty.openpty()
    set_pty_size(master_fd, width, height)

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
        os.execvp("tmux", ["tmux", "-L", TMUX_SOCKET_NAME, "attach-session", "-t", session_name])
        raise RuntimeError("execvp failed")

    os.close(slave_fd)
    return (master_fd, pid)


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


@dataclass
class ClientConnection:
    """State for a single WebSocket client's tmux attachment."""
    session_name: str
    pty_fd: int
    pid: int
    width: int
    height: int


class TerminalServer:
    def __init__(self, socket_path: str, http_port: int) -> None:
        self._socket_path = socket_path
        self._http_port = http_port
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._next_client_id: int = 0
        self._ws_clients: dict[int, WebSocket] = {}
        self._client_dimensions: dict[int, tuple[int, int]] = {}
        self._client_connections: dict[int, ClientConnection] = {}
        self._pty_tasks: dict[int, asyncio.Task[None]] = {}
        self._session_name_to_id: dict[str, int] = {}
        self._session_id_to_name: dict[int, str] = {}
        self._next_session_id: int = 0
        self.last_session_name: str | None = None
        self._session_client_sizes: dict[str, dict[int, tuple[int, int]]] = {}
        self._session_effective_size: dict[str, tuple[int, int]] = {}

    def _get_or_create_session_id(self, session_name: str) -> int:
        """Get existing session ID or create a new one for this session name."""
        if session_name in self._session_name_to_id:
            return self._session_name_to_id[session_name]
        session_id = self._next_session_id
        self._next_session_id += 1
        self._session_name_to_id[session_name] = session_id
        self._session_id_to_name[session_id] = session_name
        return session_id

    def _sync_session_ids_from_tmux(self) -> None:
        """Sync our ID mappings with actual tmux sessions."""
        tmux_sessions = tmux_list_sessions()
        current_names = {s.name for s in tmux_sessions}
        stale_names = [n for n in self._session_name_to_id if n not in current_names]
        for name in stale_names:
            sid = self._session_name_to_id.pop(name, None)
            if sid is not None:
                self._session_id_to_name.pop(sid, None)
        for s in tmux_sessions:
            self._get_or_create_session_id(s.name)

    def _get_session_name_by_id(self, session_id: int) -> str | None:
        """Get session name by ID."""
        return self._session_id_to_name.get(session_id)

    def _register_client_size(self, session_name: str, client_id: int, width: int, height: int) -> None:
        """Register a client's size for a session."""
        if session_name not in self._session_client_sizes:
            self._session_client_sizes[session_name] = {}
        self._session_client_sizes[session_name][client_id] = (width, height)

    def _unregister_client_size(self, session_name: str, client_id: int) -> None:
        """Remove a client's size registration."""
        if session_name in self._session_client_sizes:
            self._session_client_sizes[session_name].pop(client_id, None)
            if not self._session_client_sizes[session_name]:
                del self._session_client_sizes[session_name]
                self._session_effective_size.pop(session_name, None)

    def _compute_smallest_size(self, session_name: str) -> tuple[int, int] | None:
        """Compute the smallest dimensions across all clients for a session."""
        sizes = self._session_client_sizes.get(session_name, {})
        if not sizes:
            return None
        min_width = min(w for w, h in sizes.values())
        min_height = min(h for w, h in sizes.values())
        return (min_width, min_height)

    async def _update_session_size(self, session_name: str) -> tuple[int, int] | None:
        """Recompute effective size and resize tmux if changed. Returns effective size."""
        new_size = self._compute_smallest_size(session_name)
        if new_size is None:
            return None

        old_size = self._session_effective_size.get(session_name)
        if old_size != new_size:
            self._session_effective_size[session_name] = new_size
            width, height = new_size
            for cid, conn in self._client_connections.items():
                if conn.session_name == session_name:
                    set_pty_size(conn.pty_fd, width, height)

        return new_size

    async def start(self) -> None:
        socket_dir = os.path.dirname(self._socket_path)
        if not os.path.exists(socket_dir):
            os.makedirs(socket_dir, mode=0o700)

        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, self._signal_stop)
        loop.add_signal_handler(signal.SIGCHLD, self._reap_children)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        atexit.register(self._atexit_cleanup)

        self._sync_session_ids_from_tmux()

        import uvicorn

        config = uvicorn.Config(app, host="0.0.0.0", port=self._http_port, log_level="info")
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())

        await self._shutdown_event.wait()

    async def stop(self) -> None:
        for task in self._pty_tasks.values():
            task.cancel()
        self._pty_tasks.clear()

        for client_id, conn in list(self._client_connections.items()):
            try:
                os.kill(conn.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                close_pty(conn.pty_fd)
            except OSError:
                pass
        self._client_connections.clear()

        for ws in self._ws_clients.values():
            try:
                await ws.close()
            except Exception:
                pass
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

    async def _pty_forward_loop(self, client_id: int) -> None:
        """Forward PTY output to WebSocket for a specific client."""
        conn = self._client_connections.get(client_id)
        if conn is None:
            raise RuntimeError(f"No connection for client {client_id}")

        ws = self._ws_clients.get(client_id)
        if ws is None:
            raise RuntimeError(f"No WebSocket for client {client_id}")

        session_id = self._get_or_create_session_id(conn.session_name)

        while True:
            try:
                data = await read_pty(conn.pty_fd, 4096)
            except OSError:
                break
            if not data:
                break

            output_msg = encode_output(data)
            try:
                await ws.send_bytes(output_msg.encode())
            except Exception:
                break

        exit_msg = encode_shell_exited(session_id, 0)
        try:
            await ws.send_bytes(exit_msg.encode())
        except Exception:
            pass

    def _cleanup_client(self, client_id: int) -> None:
        """Clean up client state."""
        conn = self._client_connections.pop(client_id, None)
        if conn is not None:
            self._unregister_client_size(conn.session_name, client_id)
            try:
                os.kill(conn.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                close_pty(conn.pty_fd)
            except OSError:
                pass

        task = self._pty_tasks.pop(client_id, None)
        if task is not None:
            task.cancel()

        self._ws_clients.pop(client_id, None)
        self._client_dimensions.pop(client_id, None)

    async def handle_websocket_client(self, websocket: WebSocket, email: str) -> None:
        if not LOCAL_MODE and email != ALLOWED_EMAIL:
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
            self._cleanup_client(client_id)

    def _count_attached_clients(self, session_name: str) -> int:
        """Count how many clients are attached to a session."""
        count = 0
        for conn in self._client_connections.values():
            if conn.session_name == session_name:
                count += 1
        return count

    async def _attach_to_session(
        self,
        client_id: int,
        session_name: str,
        width: int,
        height: int,
        websocket: WebSocket,
    ) -> None:
        """Attach a client to a tmux session (creates if needed)."""
        self._register_client_size(session_name, client_id, width, height)
        effective_size = await self._update_session_size(session_name)
        eff_width, eff_height = effective_size if effective_size else (width, height)

        pty_fd, pid = spawn_tmux_attach(session_name, eff_width, eff_height)

        conn = ClientConnection(
            session_name=session_name,
            pty_fd=pty_fd,
            pid=pid,
            width=width,
            height=height,
        )
        self._client_connections[client_id] = conn

        task = asyncio.create_task(self._pty_forward_loop(client_id))
        self._pty_tasks[client_id] = task

        self.last_session_name = session_name
        session_id = self._get_or_create_session_id(session_name)

        tmux_info = tmux_get_session(session_name)
        created_at = tmux_info.created_at if tmux_info else time.time()
        tmux_pid = tmux_info.pid if tmux_info else pid

        attached_count = self._count_attached_clients(session_name)

        info_msg = encode_session_info(
            session_id=session_id,
            name=session_name,
            pane_id=0,
            pid=tmux_pid,
            width=eff_width,
            height=eff_height,
            created_at=created_at,
            attached_count=attached_count,
        )
        await websocket.send_bytes(info_msg.encode())

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
            self._sync_session_ids_from_tmux()
            for tmux_sess in tmux_list_sessions():
                session_id = self._get_or_create_session_id(tmux_sess.name)
                attached_count = self._count_attached_clients(tmux_sess.name)
                info_msg = encode_session_info(
                    session_id=session_id,
                    name=tmux_sess.name,
                    pane_id=0,
                    pid=tmux_sess.pid,
                    width=tmux_sess.width,
                    height=tmux_sess.height,
                    created_at=tmux_sess.created_at,
                    attached_count=attached_count,
                )
                await websocket.send_bytes(info_msg.encode())

        elif message.msg_type == MessageType.NEW_SESSION:
            name = decode_new_session(message.payload)
            if not name:
                existing = tmux_list_sessions()
                if not existing:
                    name = "main"
                else:
                    existing_names = {s.name for s in existing}
                    i = 1
                    while f"session-{i}" in existing_names:
                        i += 1
                    name = f"session-{i}"

            dimensions = self._client_dimensions.get(client_id)
            if dimensions is None:
                raise RuntimeError("Client must send IDENTIFY before NEW_SESSION")
            width, height = dimensions

            await self._attach_to_session(client_id, name, width, height, websocket)

        elif message.msg_type == MessageType.ATTACH:
            session_id = decode_attach(message.payload)
            self._sync_session_ids_from_tmux()
            session_name = self._get_session_name_by_id(session_id)
            if session_name is None:
                raise RuntimeError(f"Session ID {session_id} not found")

            if not tmux_session_exists(session_name):
                raise RuntimeError(f"Tmux session '{session_name}' no longer exists")

            dimensions = self._client_dimensions.get(client_id)
            if dimensions is None:
                raise RuntimeError("Client must send IDENTIFY before ATTACH")
            width, height = dimensions

            await self._attach_to_session(client_id, session_name, width, height, websocket)

        elif message.msg_type == MessageType.INPUT:
            conn = self._client_connections.get(client_id)
            if conn is None:
                raise RuntimeError("Client not attached to any session")
            data = decode_input(message.payload)
            write_pty(conn.pty_fd, data)

        elif message.msg_type == MessageType.RESIZE:
            conn = self._client_connections.get(client_id)
            if conn is None:
                raise RuntimeError("Client not attached to any session")
            width, height = decode_resize(message.payload)
            conn.width = width
            conn.height = height

            self._register_client_size(conn.session_name, client_id, width, height)
            effective_size = await self._update_session_size(conn.session_name)
            eff_width, eff_height = effective_size if effective_size else (width, height)

            session_id = self._get_or_create_session_id(conn.session_name)
            tmux_info = tmux_get_session(conn.session_name)
            created_at = tmux_info.created_at if tmux_info else time.time()
            tmux_pid = tmux_info.pid if tmux_info else conn.pid
            attached_count = self._count_attached_clients(conn.session_name)

            info_msg = encode_session_info(
                session_id=session_id,
                name=conn.session_name,
                pane_id=0,
                pid=tmux_pid,
                width=eff_width,
                height=eff_height,
                created_at=created_at,
                attached_count=attached_count,
            )
            await websocket.send_bytes(info_msg.encode())

        elif message.msg_type == MessageType.DETACH:
            self._cleanup_client(client_id)
            self._ws_clients[client_id] = websocket

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




@asynccontextmanager
async def lifespan(app: FastAPI):
    global yjs_server, yjs_asgi_server
    LAYOUT_STORE_PATH.mkdir(parents=True, exist_ok=True)
    yjs_server = PersistentWebsocketServer()
    yjs_asgi_server = ASGIServer(yjs_server)

    from anyio import create_task_group
    async with create_task_group() as tg:
        await tg.start(yjs_server.start)
        yield
        await yjs_server.stop()
        tg.cancel_scope.cancel()

    yjs_asgi_server = None
    yjs_server = None


app = FastAPI(title="Terminal", lifespan=lifespan)
os.makedirs("frontend/dist/assets", exist_ok=True)

app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="assets")

oauth = OAuth()


def setup_auth():
    global session_signer
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
    session_signer = TimestampSigner(SESSION_SECRET)
    if not LOCAL_MODE:
        oauth.register(
            name="google",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email"},
        )


session_signer: TimestampSigner | None = None

terminal_server: TerminalServer | None = None


def get_email_from_cookie(cookie_header: str | None) -> str | None:
    if cookie_header is None:
        return None
    if session_signer is None:
        raise RuntimeError("session_signer not initialized - call setup_auth() first")
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


class AuthASGIMiddleware:
    """Wraps an ASGI app to require valid session cookie for WebSocket connections."""

    def __init__(self, asgi_app_getter):
        self.asgi_app_getter = asgi_app_getter

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket" and not LOCAL_MODE:
            headers = dict(scope.get("headers", []))
            cookie_header = headers.get(b"cookie", b"").decode("utf-8")
            email = get_email_from_cookie(cookie_header)
            if email != ALLOWED_EMAIL:
                await send({"type": "websocket.close", "code": 4001})
                return
        asgi_app = self.asgi_app_getter()
        if asgi_app is None:
            await send({"type": "websocket.close", "code": 1011})
            return
        await asgi_app(scope, receive, send)


yjs_asgi_server: ASGIServer | None = None

app.mount("/yjs", AuthASGIMiddleware(lambda: yjs_asgi_server))


@app.get("/api/last-session")
async def api_last_session(request: Request):
    if not LOCAL_MODE and request.session.get("user") != ALLOWED_EMAIL:
        return {"session_id": None}
    if terminal_server is None:
        return {"session_id": None}
    if terminal_server.last_session_name is None:
        return {"session_id": None}
    session_id = terminal_server._get_or_create_session_id(terminal_server.last_session_name)
    return {"session_id": session_id}


@app.get("/api/sessions")
async def api_sessions(request: Request):
    if not LOCAL_MODE and request.session.get("user") != ALLOWED_EMAIL:
        return {"sessions": []}
    if terminal_server is None:
        return {"sessions": []}

    terminal_server._sync_session_ids_from_tmux()
    result = []
    for tmux_sess in tmux_list_sessions():
        session_id = terminal_server._get_or_create_session_id(tmux_sess.name)
        attached_count = terminal_server._count_attached_clients(tmux_sess.name)
        result.append({
            "id": session_id,
            "name": tmux_sess.name,
            "width": tmux_sess.width,
            "height": tmux_sess.height,
            "pid": tmux_sess.pid,
            "created_at": tmux_sess.created_at,
            "attached_count": attached_count,
        })
    return {"sessions": result}


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: int, request: Request):
    if not LOCAL_MODE and request.session.get("user") != ALLOWED_EMAIL:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if terminal_server is None:
        return JSONResponse({"error": "Terminal server not initialized"}, status_code=500)

    terminal_server._sync_session_ids_from_tmux()
    session_name = terminal_server._get_session_name_by_id(session_id)
    if session_name is None:
        return JSONResponse({"error": f"Session {session_id} not found"}, status_code=404)

    try:
        tmux_kill_session(session_name)
        terminal_server._session_name_to_id.pop(session_name, None)
        terminal_server._session_id_to_name.pop(session_id, None)
        return {"success": True}
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.patch("/api/sessions/{session_id}")
async def api_rename_session(session_id: int, request: Request):
    if not LOCAL_MODE and request.session.get("user") != ALLOWED_EMAIL:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if terminal_server is None:
        return JSONResponse({"error": "Terminal server not initialized"}, status_code=500)

    terminal_server._sync_session_ids_from_tmux()
    session_name = terminal_server._get_session_name_by_id(session_id)
    if session_name is None:
        return JSONResponse({"error": f"Session {session_id} not found"}, status_code=404)

    try:
        body = await request.json()
        new_name = body.get("name")
        if not new_name:
            return JSONResponse({"error": "Missing 'name' field"}, status_code=400)

        if new_name in terminal_server._session_name_to_id:
            return JSONResponse({"error": f"Session '{new_name}' already exists"}, status_code=409)

        tmux_rename_session(session_name, new_name)

        terminal_server._session_name_to_id.pop(session_name, None)
        terminal_server._session_name_to_id[new_name] = session_id
        terminal_server._session_id_to_name[session_id] = new_name

        for conn in terminal_server._client_connections.values():
            if conn.session_name == session_name:
                conn.session_name = new_name

        tmux_info = tmux_get_session(new_name)
        if tmux_info is None:
            raise RuntimeError("Session not found after rename")

        attached_count = terminal_server._count_attached_clients(new_name)
        return {
            "id": session_id,
            "name": new_name,
            "width": tmux_info.width,
            "height": tmux_info.height,
            "pid": tmux_info.pid,
            "created_at": tmux_info.created_at,
            "attached_count": attached_count,
        }
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/login")
async def login(request: Request):
    if LOCAL_MODE:
        return RedirectResponse(url="/")
    redirect_uri = request.url_for("callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/callback")
async def callback(request: Request):
    if LOCAL_MODE:
        return RedirectResponse(url="/")
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
    if LOCAL_MODE:
        email = ALLOWED_EMAIL
    else:
        cookie_header = websocket.headers.get("cookie")
        email = get_email_from_cookie(cookie_header)
        if email is None:
            await websocket.close(4001)
            return
    await terminal_server.handle_websocket_client(websocket, email)


@app.get("/", response_class=HTMLResponse)
async def terminal_ui(request: Request):
    if not LOCAL_MODE and request.session.get("user") != ALLOWED_EMAIL:
        return RedirectResponse(url="/login")
    html = open("frontend/dist/index.html").read()
    return html


# =============================================================================
# Main
# =============================================================================



async def run_server(socket_path: str, http_port: int) -> None:
    ensure_frontend_built()
    setup_auth()
    global terminal_server
    terminal_server = TerminalServer(socket_path=socket_path, http_port=http_port)
    await terminal_server.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Terminal Mux server")
    parser.add_argument("--local", action="store_true", help="Run in local mode (skip OAuth)")
    args = parser.parse_args()

    LOCAL_MODE = args.local
    load_config()

    if LOCAL_MODE:
        print("Running in LOCAL MODE - OAuth disabled")

    socket_path = get_socket_path()
    pid_file = get_pid_file_path()
    # daemonize(pid_file)
    asyncio.run(run_server(socket_path, HTTP_PORT))
