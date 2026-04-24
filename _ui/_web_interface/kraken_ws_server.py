"""
KrakenSDR WebSocket broadcast server — zero external dependencies.

Implements the WebSocket protocol (RFC 6455) directly on top of Python's
asyncio TCP server, so it works with whatever Python 3.x is installed on the
KrakenSDR device without requiring Quart, hypercorn, websockets, or any other
third-party package.

The server runs in its own background daemon thread with a dedicated asyncio
event loop, completely independent of the dash_devices / Quart server on
port 8080.  Clients can connect and receive data immediately at application
startup — no browser needs to be open.

Endpoint (default)
------------------
    ws://<host>:8082/ws/kraken

Every message is a JSON object whose first key is 'type':

    { "type": "doa",      "timestamp": <epoch_ms>, ... }
    { "type": "spectrum", "timestamp": <epoch_ms>, ... }
    { "type": "settings", "timestamp": <epoch_ms>, ... }

On connect the client immediately receives the last-known settings snapshot.

Thread-safety
-------------
Signal-processor and fetch_dsp_data timer threads call broadcast_from_thread()
which is a non-blocking fire-and-forget that schedules the async send onto the
WS server's own event loop via asyncio.run_coroutine_threadsafe().
"""

import asyncio
import base64
import hashlib
import json
import logging
import struct
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── shared state ──────────────────────────────────────────────────────────────

# One asyncio.Queue per connected client.
_ws_clients: set = set()

# The event loop running inside the WS server thread.
_event_loop: Optional[asyncio.AbstractEventLoop] = None

# Last settings payload as a serialised JSON string.  Written synchronously by
# cache_settings() so it is available even before the server starts.
_last_settings: Optional[str] = None

# Optional callback invoked with a parsed dict whenever a client sends a
# JSON command frame.  Register via register_command_handler().
_command_handler: Optional[Callable[[dict], None]] = None

# RFC 6455 magic GUID used to compute Sec-WebSocket-Accept
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ── WebSocket protocol helpers ────────────────────────────────────────────────

async def _do_handshake(reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter) -> bool:
    """Read the HTTP upgrade request and respond with 101."""
    headers: dict = {}
    try:
        await reader.readline()  # request line — discard
        while True:
            raw = await reader.readline()
            line = raw.decode(errors="replace").strip()
            if not line:
                break
            if ": " in line:
                k, v = line.split(": ", 1)
                headers[k.lower()] = v
    except Exception:
        return False

    ws_key = headers.get("sec-websocket-key", "")
    if not ws_key:
        return False

    accept = base64.b64encode(
        hashlib.sha1((ws_key + _WS_GUID).encode()).digest()
    ).decode()

    writer.write(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        + f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
    )
    await writer.drain()
    return True


async def _send_text(writer: asyncio.StreamWriter, text: str) -> None:
    """Send a single WebSocket text frame (opcode 0x1, FIN set)."""
    payload = text.encode("utf-8")
    length = len(payload)
    if length <= 125:
        header = bytes([0x81, length])
    elif length <= 65535:
        header = bytes([0x81, 126]) + struct.pack(">H", length)
    else:
        header = bytes([0x81, 127]) + struct.pack(">Q", length)
    writer.write(header + payload)
    await writer.drain()


async def _receive_and_dispatch(reader: asyncio.StreamReader) -> None:
    """Read frames sent by the client, unmask them, and dispatch commands.

    Client→server frames are always masked (RFC 6455 §5.3).  Text frames
    (opcode 0x1) are decoded as UTF-8 JSON and forwarded to the registered
    command handler.  All other frame types are consumed silently so TCP
    back-pressure never stalls the connection.  A close frame (opcode 0x8)
    terminates the loop.
    """
    try:
        while True:
            header = await reader.readexactly(2)
            opcode = header[0] & 0x0F
            masked_len = header[1]
            is_masked = bool(masked_len & 0x80)
            length = masked_len & 0x7F

            if length == 126:
                length = struct.unpack(">H", await reader.readexactly(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", await reader.readexactly(8))[0]

            mask_key = b""
            if is_masked:
                mask_key = await reader.readexactly(4)

            payload = b""
            if length:
                payload = await reader.readexactly(length)

            # Unmask payload (XOR each byte with cycling mask key)
            if is_masked and mask_key:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

            if opcode == 0x8:  # close frame
                break
            elif opcode == 0x1 and _command_handler is not None:  # text frame
                try:
                    data = json.loads(payload.decode("utf-8"))
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, _command_handler, data)
                except Exception as exc:
                    logger.warning("WS command error: %s", exc)
    except Exception:
        pass


# ── client handler ────────────────────────────────────────────────────────────

async def _handle_client(reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter) -> None:
    if not await _do_handshake(reader, writer):
        writer.close()
        return

    q: asyncio.Queue = asyncio.Queue(maxsize=20)
    _ws_clients.add(q)
    logger.info("WS client connected (%d total)", len(_ws_clients))

    async def _send_loop() -> None:
        if _last_settings is not None:
            await _send_text(writer, _last_settings)
        while True:
            message = await q.get()
            await _send_text(writer, message)

    send_task = asyncio.ensure_future(_send_loop())
    recv_task = asyncio.ensure_future(_receive_and_dispatch(reader))

    try:
        done, pending = await asyncio.wait(
            {send_task, recv_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        _ws_clients.discard(q)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        logger.info("WS client disconnected (%d total)", len(_ws_clients))


# ── public API ────────────────────────────────────────────────────────────────

def register_command_handler(fn: Callable[[dict], None]) -> None:
    """Register a callable that receives parsed JSON dicts from WS clients.

    Called once at startup (from app.py or headless.py) to wire inbound
    commands to the WebInterface.  The callable is invoked in a thread-pool
    executor so it may safely perform blocking I/O.
    """
    global _command_handler
    _command_handler = fn


def cache_settings(payload: dict) -> None:
    """Synchronously cache the settings payload.

    Called by save_configuration() before broadcast_from_thread() so the
    snapshot is always current — even at startup before the event loop exists.
    """
    global _last_settings
    _last_settings = json.dumps(payload)


async def broadcast_to_ws(payload: dict) -> None:
    """Put a JSON message on every connected client's queue (non-blocking)."""
    message = json.dumps(payload)
    slow_clients: set = set()
    for q in list(_ws_clients):
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            slow_clients.add(q)
        except Exception:
            slow_clients.add(q)
    _ws_clients.difference_update(slow_clients)


def broadcast_from_thread(payload: dict) -> None:
    """Thread-safe, non-blocking broadcast from any worker thread."""
    loop = _event_loop
    if loop is None or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(broadcast_to_ws(payload), loop)
    except RuntimeError:
        pass


def start_server(host: str = "0.0.0.0", port: int = 8082) -> None:
    """Start the WebSocket server in a background daemon thread.

    Uses only Python stdlib — no Quart, hypercorn, or websockets package
    required.  Returns immediately; the server runs until the process exits.
    """
    def _run() -> None:
        global _event_loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _event_loop = loop

        async def _serve() -> None:
            server = await asyncio.start_server(_handle_client, host, port)
            logger.info(
                "KrakenSDR WebSocket server listening on ws://%s:%d/ws/kraken",
                host, port,
            )
            async with server:
                await server.serve_forever()

        try:
            loop.run_until_complete(_serve())
        except Exception:
            logger.exception("WebSocket server stopped unexpectedly")

    t = threading.Thread(target=_run, daemon=True, name="kraken-ws-server")
    t.start()


def register_ws_route(_quart_app) -> None:
    """No-op — kept so any stale call sites don't raise AttributeError."""
    pass