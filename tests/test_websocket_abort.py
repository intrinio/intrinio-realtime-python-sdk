import threading
import time
import unittest
from unittest.mock import Mock

from intriniorealtime._websocket import close_websocket_app, should_abort_handshake


class CloseWebsocketAppTests(unittest.TestCase):
    def test_uses_only_public_close_method(self):
        app = Mock(spec=["close"])

        close_websocket_app(app)

        app.close.assert_called_once_with()

    def test_noop_on_none(self):
        close_websocket_app(None)

    def test_swallows_close_error(self):
        app = Mock(spec=["close"])
        app.close.side_effect = RuntimeError("close failed")
        close_websocket_app(app)


class ShouldAbortHandshakeTests(unittest.TestCase):
    def test_true_on_timeout_for_current_generation(self):
        handshake = threading.Event()
        stop = threading.Event()
        self.assertTrue(should_abort_handshake(
            handshake, stop, 1, 1, time.monotonic() + 0.01
        ))

    def test_false_when_handshake_completes(self):
        handshake = threading.Event()
        handshake.set()
        stop = threading.Event()
        self.assertFalse(should_abort_handshake(
            handshake, stop, 1, 1, time.monotonic() + 0.01
        ))

    def test_false_for_stale_generation(self):
        handshake = threading.Event()
        stop = threading.Event()
        self.assertFalse(should_abort_handshake(
            handshake, stop, 1, 2, time.monotonic() + 0.01
        ))

    def test_false_immediately_when_stopped(self):
        handshake = threading.Event()
        stop = threading.Event()
        stop.set()
        start = time.monotonic()
        self.assertFalse(should_abort_handshake(
            handshake, stop, 1, 1, time.monotonic() + 1.0
        ))
        self.assertLess(time.monotonic() - start, 0.1)

    def test_callable_current_generation_is_read_after_wait(self):
        handshake = threading.Event()
        stop = threading.Event()
        state = {"g": 1}

        def bump():
            time.sleep(0.02)
            state["g"] = 2

        threading.Thread(target=bump, daemon=True).start()
        self.assertFalse(should_abort_handshake(
            handshake, stop, 1, lambda: state["g"], time.monotonic() + 0.05
        ))


if __name__ == "__main__":
    unittest.main()
