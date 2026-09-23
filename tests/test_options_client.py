import logging
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

from intriniorealtime.options_client import (
    Config,
    IntrinioRealtimeOptionsClient,
    Providers,
    _CONNECT_TIMEOUT_SECONDS,
    _WebSocket,
)


def _make_ws(stop_flag, get_token=None, get_url=None, channels=None):
    return _WebSocket(
        "ws://example/old",
        threading.Lock(),
        [],
        lambda: channels if channels is not None else set(),
        get_token or (lambda: "tok"),
        get_url or (lambda token: "ws://example/" + token),
        True,
        False,
        False,
        False,
        queue.Queue(),
        stop_flag,
    )


def _make_client():
    config = Config(
        api_key="test-key",
        provider=Providers.MANUAL,
        num_threads=1,
        manual_ip_address="127.0.0.1",
    )
    return IntrinioRealtimeOptionsClient(config, on_trade=lambda trade: None)


class OptionsReconnectTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_stale_watchdog_does_not_abort_later_attempt(self):
        stop = threading.Event()
        second_opened = threading.Event()
        aborts_after_second_open = []
        run_count = {"n": 0}

        ws = _make_ws(stop)

        def track_abort(app):
            if app is ws and second_opened.is_set():
                aborts_after_second_open.append(1)

        def fake_run_forever(app, *args, **kwargs):
            run_count["n"] += 1
            if run_count["n"] == 1:
                return
            app.on_open(app)
            second_opened.set()
            time.sleep(0.2)
            stop.set()
        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("intriniorealtime.options_client._SELF_HEAL_BACKOFFS", [0]), \
             patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.options_client.abort_websocket_app", side_effect=track_abort), \
             patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever", fake_run_forever):
            ws.start()

        self.assertGreaterEqual(run_count["n"], 2)
        self.assertEqual(aborts_after_second_open, [])

    def test_reconnect_refreshes_token_without_extra_argument(self):
        stop = threading.Event()

        def real_get_token():
            return "tok2"

        get_token = Mock(side_effect=lambda *args, **kwargs: real_get_token(*args, **kwargs))
        get_url = Mock(return_value="ws://example/new")
        run_count = {"n": 0}

        def fake_run_forever(app, *args, **kwargs):
            run_count["n"] += 1
            if run_count["n"] >= 2:
                stop.set()

        ws = _make_ws(stop, get_token=get_token, get_url=get_url)
        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("intriniorealtime.options_client._SELF_HEAL_BACKOFFS", [0]), \
             patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.options_client.abort_websocket_app"), \
             patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever", fake_run_forever):
            ws.start()

        get_token.assert_called_with()
        get_url.assert_called_with("tok2")
        self.assertEqual(ws.url, "ws://example/new")

    def test_reconnect_does_not_run_forever_with_stale_url_when_token_refresh_fails(self):
        stop = threading.Event()
        token_calls = {"n": 0}

        def get_token():
            token_calls["n"] += 1
            if token_calls["n"] < 2:
                raise RuntimeError("token failed")
            return "tok2"

        urls = []
        run_count = {"n": 0}

        def fake_run_forever(app, *args, **kwargs):
            run_count["n"] += 1
            urls.append(app.url)
            if run_count["n"] >= 2:
                stop.set()

        ws = _make_ws(stop, get_token=get_token, get_url=lambda token: "ws://example/" + token)
        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("intriniorealtime.options_client._SELF_HEAL_BACKOFFS", [0]), \
             patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.options_client.abort_websocket_app"), \
             patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever", fake_run_forever):
            ws.start()

        self.assertEqual(run_count["n"], 2)
        self.assertEqual(urls[0], "ws://example/old")
        self.assertEqual(urls[1], "ws://example/tok2")
        self.assertEqual(token_calls["n"], 2)

    def test_start_after_stop_clears_stop_event_and_connects_again(self):
        client = _make_client()
        start_calls = []

        def fake_ws_start(self):
            start_calls.append(1)
            self.isReady = True
            client._stop_event.wait(5)

        with patch("intriniorealtime.options_client.requests.get") as get, \
             patch("intriniorealtime.options_client.time.sleep"), \
             patch.object(_WebSocket, "start", fake_ws_start):
            get.return_value.status_code = 200
            get.return_value.text = "tok"

            client.start()
            deadline = time.time() + 2
            while not start_calls and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(start_calls, [1])

            client.stop()
            self.assertTrue(client._stop_event.is_set())

            client.start()
            deadline = time.time() + 2
            while len(start_calls) < 2 and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(start_calls), 2)

            client.stop()

    def test_websocket_start_is_noop_while_stopped_and_runs_after_clear(self):
        stop = threading.Event()
        stop.set()
        ws = _make_ws(stop)
        with patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever") as run_forever, \
             patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("websocket.setdefaulttimeout"):
            ws.start()
        run_forever.assert_not_called()

        stop.clear()
        calls = []

        def fake_run_forever(app, *args, **kwargs):
            calls.append(1)
            stop.set()

        with patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever", fake_run_forever), \
             patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.options_client.abort_websocket_app"):
            ws.start()
        self.assertEqual(calls, [1])

    def test_clears_continuation_state_on_each_connect_attempt(self):
        stop = threading.Event()
        ws = _make_ws(stop)
        ws._WebSocket__currently_continuing = True
        ws._WebSocket__continuation_queue.put(b"partial-from-prior-session")
        seen = []
        run_count = {"n": 0}

        def fake_run_forever(app, *args, **kwargs):
            run_count["n"] += 1
            seen.append((
                app._WebSocket__currently_continuing,
                app._WebSocket__continuation_queue.empty(),
            ))
            if run_count["n"] == 1:
                app._WebSocket__currently_continuing = True
                app._WebSocket__continuation_queue.put(b"leftover-mid-fragment")
                return
            stop.set()

        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("intriniorealtime.options_client._SELF_HEAL_BACKOFFS", [0]), \
             patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.options_client.abort_websocket_app"), \
             patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever", fake_run_forever):
            ws.start()

        self.assertEqual(run_count["n"], 2)
        self.assertEqual(seen, [(False, True), (False, True)])

    def test_stop_preserves_channels_and_clears_started(self):
        client = _make_client()
        client._IntrinioRealtimeOptionsClient__channels = {"AAPL"}
        client._IntrinioRealtimeOptionsClient__is_started = True
        ws = Mock()
        ws.isReady = True
        client._IntrinioRealtimeOptionsClient__webSocket = ws
        client._IntrinioRealtimeOptionsClient__socket_thread = None

        with patch("intriniorealtime.options_client.time.sleep"):
            client.stop()

        self.assertEqual(client._IntrinioRealtimeOptionsClient__channels, {"AAPL"})
        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_started)
        ws.send_binary.assert_called()
        leave = ws.send_binary.call_args[0][0]
        self.assertEqual(leave[0], 76)

    def test_join_after_stop_does_not_wait_for_ready(self):
        client = _make_client()
        client._IntrinioRealtimeOptionsClient__is_started = True
        client._IntrinioRealtimeOptionsClient__webSocket = None
        with patch("intriniorealtime.options_client.time.sleep"):
            client.stop()

        with patch.object(client, "_IntrinioRealtimeOptionsClient__all_ready") as ready:
            client.join("MSFT")
        ready.assert_not_called()
        self.assertIn("MSFT", client._IntrinioRealtimeOptionsClient__channels)

    def test_leave_drops_channel_if_send_raises(self):
        client = _make_client()
        client._IntrinioRealtimeOptionsClient__channels = {"AAPL"}
        ws = Mock()
        ws.isReady = True
        ws.send_binary.side_effect = RuntimeError("connection closed")
        client._IntrinioRealtimeOptionsClient__webSocket = ws

        with self.assertRaises(RuntimeError):
            client.leave("AAPL")

        self.assertNotIn("AAPL", client._IntrinioRealtimeOptionsClient__channels)

    def test_stop_joins_socket_thread_with_timeout(self):
        client = _make_client()
        thread = Mock()
        thread.ident = 1
        client._IntrinioRealtimeOptionsClient__socket_thread = thread
        client._IntrinioRealtimeOptionsClient__webSocket = None
        with patch("intriniorealtime.options_client.time.sleep"):
            client.stop()
        thread.join.assert_called_with(timeout=_CONNECT_TIMEOUT_SECONDS + 1)

    def test_open_then_drop_twice_backs_off_on_second_reconnect(self):
        stop = threading.Event()
        waits = []
        original_wait = threading.Event.wait

        def tracking_wait(self, timeout=None):
            waits.append(timeout)
            return original_wait(self, timeout)

        run_count = {"n": 0}

        def fake_run_forever(app, *args, **kwargs):
            run_count["n"] += 1
            app.on_open(app)
            if run_count["n"] >= 3:
                stop.set()

        ws = _make_ws(stop)
        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("intriniorealtime.options_client._STABLE_SESSION_SECONDS", 10), \
             patch("intriniorealtime.options_client._SELF_HEAL_BACKOFFS", [0.01]), \
             patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.options_client.abort_websocket_app"), \
             patch("threading.Event.wait", tracking_wait), \
             patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever", fake_run_forever):
            ws.start()

        self.assertEqual(run_count["n"], 3)
        self.assertIn(0.01, waits)

    def test_stable_session_drop_skips_first_backoff(self):
        stop = threading.Event()
        waits = []
        original_wait = threading.Event.wait

        def tracking_wait(self, timeout=None):
            waits.append(timeout)
            return original_wait(self, timeout)

        run_count = {"n": 0}

        def fake_run_forever(app, *args, **kwargs):
            run_count["n"] += 1
            if run_count["n"] == 1:
                app.on_open(app)
                time.sleep(0.06)
                return
            stop.set()

        ws = _make_ws(stop)
        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("intriniorealtime.options_client._STABLE_SESSION_SECONDS", 0.05), \
             patch("intriniorealtime.options_client._SELF_HEAL_BACKOFFS", [10]), \
             patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.options_client.abort_websocket_app"), \
             patch("threading.Event.wait", tracking_wait), \
             patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever", fake_run_forever):
            ws.start()

        self.assertEqual(run_count["n"], 2)
        self.assertNotIn(10, waits)

    def test_failed_handshake_still_backs_off_before_reconnect(self):
        stop = threading.Event()
        waits = []
        original_wait = threading.Event.wait

        def tracking_wait(self, timeout=None):
            waits.append(timeout)
            return original_wait(self, timeout)

        run_count = {"n": 0}

        def fake_run_forever(app, *args, **kwargs):
            run_count["n"] += 1
            if run_count["n"] == 1:
                return
            stop.set()

        ws = _make_ws(stop)
        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("intriniorealtime.options_client._SELF_HEAL_BACKOFFS", [0.01]), \
             patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.options_client.abort_websocket_app"), \
             patch("threading.Event.wait", tracking_wait), \
             patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever", fake_run_forever):
            ws.start()

        self.assertEqual(run_count["n"], 2)
        self.assertIn(0.01, waits)

    def test_stop_sets_flag_before_sleep(self):
        client = _make_client()
        saw_stop = {}

        def fake_sleep(seconds):
            saw_stop["set"] = client._stop_event.is_set()

        with patch("intriniorealtime.options_client.time.sleep", side_effect=fake_sleep):
            client.stop()
        self.assertTrue(saw_stop["set"])

    def test_start_does_not_spawn_thread_when_stopped_during_auth(self):
        client = _make_client()

        def get_token():
            client._stop_event.set()
            return None

        with patch.object(client, "_IntrinioRealtimeOptionsClient__get_token", side_effect=get_token), \
             patch.object(threading.Thread, "start") as thread_start:
            client.start()

        thread_start.assert_not_called()
        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_started)

    def test_start_retries_until_token_then_connects(self):
        client = _make_client()
        tokens = {"n": 0}
        start_calls = []

        def get_token():
            tokens["n"] += 1
            if tokens["n"] < 3:
                return None
            return "tok"

        def fake_ws_start(self):
            start_calls.append(1)

        with patch.object(client, "_IntrinioRealtimeOptionsClient__get_token", side_effect=get_token), \
             patch("intriniorealtime.options_client._SELF_HEAL_BACKOFFS", [0]), \
             patch.object(_WebSocket, "start", fake_ws_start):
            client.start()

        self.assertEqual(tokens["n"], 3)
        self.assertEqual(start_calls, [1])
        self.assertTrue(client._IntrinioRealtimeOptionsClient__is_started)

    def test_stop_during_socket_thread_start_does_not_publish_started_state(self):
        client = _make_client()

        def stop_before_thread_runs():
            client.stop()

        with patch.object(client, "_IntrinioRealtimeOptionsClient__get_token", return_value="tok"), \
             patch.object(threading.Thread, "start", side_effect=stop_before_thread_runs), \
             patch("intriniorealtime.options_client.time.sleep"):
            client.start()

        self.assertTrue(client._stop_event.is_set())
        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_started)

    def test_start_replaces_data_queue_from_previous_session(self):
        client = _make_client()
        old_queue = client._IntrinioRealtimeOptionsClient__data
        old_queue.put(b"stale")

        with patch.object(client, "_IntrinioRealtimeOptionsClient__get_token", return_value="tok"), \
             patch.object(_WebSocket, "start"):
            client.start()

        new_queue = client._IntrinioRealtimeOptionsClient__data
        self.assertIsNot(new_queue, old_queue)
        self.assertTrue(new_queue.empty())
        self.assertEqual(old_queue.get_nowait(), b"stale")

    def test_start_on_healthy_session_is_noop(self):
        client = _make_client()
        thread = Mock()
        thread.is_alive.return_value = True
        client._IntrinioRealtimeOptionsClient__socket_thread = thread
        client._IntrinioRealtimeOptionsClient__is_started = True
        client._stop_event.clear()

        with patch.object(client, "_IntrinioRealtimeOptionsClient__get_token") as get_token:
            client.start()

        get_token.assert_not_called()

    def test_start_waits_for_previous_socket_thread_then_starts(self):
        client = _make_client()
        leftover = Mock()
        leftover.ident = 1
        leftover.is_alive.side_effect = [True, True, False]
        leftover.join = Mock()
        client._IntrinioRealtimeOptionsClient__socket_thread = leftover
        client._IntrinioRealtimeOptionsClient__is_started = False
        client._stop_event.set()
        start_calls = []

        def fake_ws_start(self):
            start_calls.append(1)

        with patch("intriniorealtime.options_client.requests.get") as get, \
             patch.object(_WebSocket, "start", fake_ws_start):
            get.return_value.status_code = 200
            get.return_value.text = "tok"
            client.start()

        leftover.join.assert_called()
        self.assertEqual(start_calls, [1])

    def test_stop_completes_teardown_if_leave_raises(self):
        client = _make_client()
        client._IntrinioRealtimeOptionsClient__channels = {"AAPL"}
        client._IntrinioRealtimeOptionsClient__is_started = True
        ws = Mock()
        ws.isReady = True
        ws.sock = Mock()
        ws.send_binary.side_effect = RuntimeError("connection closed")
        client._IntrinioRealtimeOptionsClient__webSocket = ws
        thread = Mock()
        thread.ident = 1
        client._IntrinioRealtimeOptionsClient__socket_thread = thread

        with patch("intriniorealtime.options_client.time.sleep"):
            client.stop()

        ws.sock.settimeout.assert_called_with(1)
        ws.stop.assert_called()
        thread.join.assert_called()
        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_started)

    def test_start_uses_new_stop_event_so_old_workers_stay_stopped(self):
        client = _make_client()
        old_event = client._stop_event
        old_event.set()
        old_worker = Mock()
        old_worker.ident = 1
        client._IntrinioRealtimeOptionsClient__worker_threads = [old_worker]
        client._IntrinioRealtimeOptionsClient__is_started = False
        start_calls = []

        def fake_ws_start(self):
            start_calls.append(1)

        with patch("intriniorealtime.options_client.requests.get") as get, \
             patch.object(_WebSocket, "start", fake_ws_start):
            get.return_value.status_code = 200
            get.return_value.text = "tok"
            client.start()

        self.assertIsNot(client._stop_event, old_event)
        self.assertTrue(old_event.is_set())
        self.assertFalse(client._stop_event.is_set())
        self.assertNotIn(old_worker, client._IntrinioRealtimeOptionsClient__worker_threads)
        self.assertEqual(start_calls, [1])

    def test_start_aborts_leftover_websocket_while_joining(self):
        client = _make_client()
        leftover = Mock()
        leftover.ident = 1
        leftover.is_alive.side_effect = [True, True, False]
        leftover.join = Mock()
        ws = Mock()
        client._IntrinioRealtimeOptionsClient__socket_thread = leftover
        client._IntrinioRealtimeOptionsClient__webSocket = ws
        client._IntrinioRealtimeOptionsClient__is_started = False
        client._stop_event.set()
        start_calls = []

        def fake_ws_start(self):
            start_calls.append(1)

        with patch("intriniorealtime.options_client.abort_websocket_app") as abort, \
             patch("intriniorealtime.options_client.requests.get") as get, \
             patch.object(_WebSocket, "start", fake_ws_start):
            get.return_value.status_code = 200
            get.return_value.text = "tok"
            client.start()

        abort.assert_called()
        leftover.join.assert_called()
        self.assertEqual(start_calls, [1])

    def test_start_returns_if_stop_during_leftover_join(self):
        client = _make_client()
        leftover = Mock()
        leftover.ident = 1
        leftover.is_alive.return_value = True

        def join(timeout=None):
            client._stop_generation += 1
            client._stop_event.set()

        leftover.join.side_effect = join
        client._IntrinioRealtimeOptionsClient__socket_thread = leftover
        client._IntrinioRealtimeOptionsClient__is_started = False
        client._stop_event.set()

        with patch("intriniorealtime.options_client.abort_websocket_app"), \
             patch.object(client, "_IntrinioRealtimeOptionsClient__get_token") as get_token, \
             patch.object(threading.Thread, "start") as thread_start:
            client.start()

        get_token.assert_not_called()
        thread_start.assert_not_called()
        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_started)
