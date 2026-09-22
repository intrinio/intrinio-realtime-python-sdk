import socket
import threading
from typing import Optional


def abort_websocket_app(app) -> None:
    if app is None:
        return
    try:
        app.keep_running = False
    except Exception:
        pass

    ws = getattr(app, "sock", None)
    raw = getattr(ws, "sock", None) if ws is not None else None

    if raw is not None:
        try:
            raw.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            raw.close()
        except Exception:
            pass

    try:
        if ws is not None:
            ws.sock = None
            ws.connected = False
    except Exception:
        pass

    try:
        app.sock = None
    except Exception:
        pass

    try:
        close = getattr(app, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def should_abort_handshake(
    handshake_event: threading.Event,
    stop_flag: Optional[threading.Event],
    generation: int,
    current_generation,
    timeout_seconds: float,
) -> bool:
    if handshake_event.wait(timeout_seconds):
        return False
    current = current_generation() if callable(current_generation) else current_generation
    if generation != current:
        return False
    return True


def clear_socket_timeout(app) -> None:
    sock = getattr(app, "sock", None)
    if sock is None:
        return
    try:
        sock.settimeout(None)
    except Exception:
        pass
