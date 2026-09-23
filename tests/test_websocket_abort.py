import socket
import threading
import time
import unittest
from unittest.mock import Mock, call

import websocket

from intriniorealtime._websocket import (
    abort_websocket_app,
    run_forever_with_connect_timeout,
    should_abort_handshake,
)


class FakeWebSocket:
    def __init__(self, raw, connected=False):
        self.sock = raw
        self.connected = connected

    def close(self, **kwargs):
        if not self.connected:
            return
        self.shutdown()

    def shutdown(self):
        if self.sock:
            self.sock.close()
            self.sock = None
            self.connected = False


class FakeApp:
    def __init__(self, ws):
        self.sock = ws
        self.keep_running = True

    def close(self, **kwargs):
        self.keep_running = False
        if self.sock:
            self.sock.close()
            self.sock = None


class AbortWebsocketAppTests(unittest.TestCase):
    def test_shutdowns_raw_socket_when_websocket_is_not_connected(self):
        raw = Mock()
        ws = FakeWebSocket(raw, connected=False)
        app = FakeApp(ws)

        abort_websocket_app(app)

        self.assertFalse(app.keep_running)
        raw.shutdown.assert_called_with(socket.SHUT_RDWR)
        raw.close.assert_called()
        self.assertEqual(raw.mock_calls[0], call.shutdown(socket.SHUT_RDWR))
        self.assertEqual(raw.mock_calls[1], call.close())
        self.assertIsNone(app.sock)
        self.assertIsNone(ws.sock)
        self.assertFalse(ws.connected)

    def test_noop_on_none(self):
        abort_websocket_app(None)

    def test_sets_keep_running_false_without_sock(self):
        app = FakeApp(None)
        abort_websocket_app(app)
        self.assertFalse(app.keep_running)


class ConnectTimeoutTests(unittest.TestCase):
    def test_connect_timeout_is_restored_as_soon_as_socket_opens(self):
        original_timeout = websocket.getdefaulttimeout()
        websocket.setdefaulttimeout(17)
        observed = []
        sock = Mock()
        app = Mock()
        app.sock = sock

        def on_open(ws):
            observed.append(websocket.getdefaulttimeout())

        def run_forever(**kwargs):
            self.assertEqual(websocket.getdefaulttimeout(), 3)
            app.on_open(app)

        app.on_open = on_open
        app.run_forever.side_effect = run_forever
        try:
            run_forever_with_connect_timeout(app, 3)
            self.assertEqual(observed, [17])
            self.assertEqual(websocket.getdefaulttimeout(), 17)
            sock.settimeout.assert_called_once_with(None)
        finally:
            websocket.setdefaulttimeout(original_timeout)


class ShouldAbortHandshakeTests(unittest.TestCase):
    def test_true_on_timeout_for_current_generation(self):
        handshake = threading.Event()
        stop = threading.Event()
        self.assertTrue(should_abort_handshake(handshake, stop, 1, 1, 0.01))

    def test_false_when_handshake_completes(self):
        handshake = threading.Event()
        handshake.set()
        stop = threading.Event()
        self.assertFalse(should_abort_handshake(handshake, stop, 1, 1, 0.01))

    def test_false_for_stale_generation(self):
        handshake = threading.Event()
        stop = threading.Event()
        self.assertFalse(should_abort_handshake(handshake, stop, 1, 2, 0.01))

    def test_true_when_stopped_and_handshake_timed_out(self):
        handshake = threading.Event()
        stop = threading.Event()
        stop.set()
        self.assertTrue(should_abort_handshake(handshake, stop, 1, 1, 0.01))

    def test_callable_current_generation_is_read_after_wait(self):
        handshake = threading.Event()
        stop = threading.Event()
        state = {"g": 1}

        def bump():
            time.sleep(0.02)
            state["g"] = 2

        threading.Thread(target=bump, daemon=True).start()
        self.assertFalse(should_abort_handshake(handshake, stop, 1, lambda: state["g"], 0.05))


if __name__ == "__main__":
    unittest.main()
