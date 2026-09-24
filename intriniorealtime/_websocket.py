import threading
import time
from typing import Optional


def close_websocket_app(app) -> None:
    """Best-effort close using websocket-client's public API only."""
    if app is None:
        return
    try:
        app.close()
    except Exception:
        pass


def should_abort_handshake(
    handshake_event: threading.Event,
    stop_flag: Optional[threading.Event],
    generation: int,
    current_generation,
    deadline: float,
) -> bool:
    while True:
        if handshake_event.is_set():
            return False
        if stop_flag is not None and stop_flag.is_set():
            return False
        current = current_generation() if callable(current_generation) else current_generation
        if generation != current:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        handshake_event.wait(min(remaining, 0.1))
