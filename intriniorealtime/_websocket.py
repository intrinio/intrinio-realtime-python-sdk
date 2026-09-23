import socket
import threading
from typing import Optional

import websocket


_websocket_timeout_lock = threading.Lock()


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


def run_forever_with_connect_timeout(app, timeout_seconds: float, **kwargs):
    """Run an app with a connect timeout without leaving a global timeout behind."""
    _websocket_timeout_lock.acquire()
    previous_timeout = websocket.getdefaulttimeout()
    original_on_open = app.on_open
    restored = False

    def restore_timeout() -> None:
        nonlocal restored
        if restored:
            return
        websocket.setdefaulttimeout(previous_timeout)
        restored = True
        _websocket_timeout_lock.release()

    def on_open(ws, *args):
        clear_socket_timeout(app)
        restore_timeout()
        if original_on_open:
            original_on_open(ws, *args)

    app.on_open = on_open
    websocket.setdefaulttimeout(timeout_seconds)
    try:
        return app.run_forever(**kwargs)
    finally:
        restore_timeout()
        app.on_open = original_on_open
