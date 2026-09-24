import logging
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

import websocket

from intriniorealtime.options_client import (
    Config,
    IntrinioRealtimeOptionsClient,
    Providers,
    _CONNECT_TIMEOUT_SECONDS,
    _WebSocket,
    _thread_fn,
)


def _make_ws(stop_flag, get_token=None, get_url=None, channels=None, data_queue=None,
             channels_lock=None, stats=None, stats_lock=None, sent_channels=None):
    return _WebSocket(
        "ws://example/old",
        threading.Lock(),
        channels_lock or threading.RLock(),
        sent_channels if sent_channels is not None else set(),
        stats if stats is not None else {"data": 0, "text": 0},
        stats_lock or threading.Lock(),
        [],
        lambda: channels if channels is not None else set(),
        get_token or (lambda: "tok"),
        get_url or (lambda token: "ws://example/" + token),
        True,
        False,
        False,
        False,
        data_queue if data_queue is not None else queue.Queue(),
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

    def test_config_rejects_non_positive_or_non_integer_worker_counts(self):
        for num_threads in (0, -1, True, 1.5, "2"):
            with self.subTest(num_threads=num_threads):
                with self.assertRaises(ValueError):
                    Config(
                        api_key="test-key",
                        provider=Providers.MANUAL,
                        num_threads=num_threads,
                        manual_ip_address="127.0.0.1",
                    )

        config = Config(
            api_key="test-key",
            provider=Providers.MANUAL,
            num_threads=1,
            manual_ip_address="127.0.0.1",
        )
        config.num_threads = 0
        with self.assertRaises(ValueError):
            IntrinioRealtimeOptionsClient(config, on_trade=lambda trade: None)

    def test_config_symbol_set_initializes_channels(self):
        config = Config(
            api_key="test-key",
            provider=Providers.MANUAL,
            num_threads=1,
            manual_ip_address="127.0.0.1",
            symbols={"AAPL__220101C00140000", "MSFT"},
        )

        client = IntrinioRealtimeOptionsClient(config, on_trade=lambda trade: None)

        self.assertEqual(
            client._IntrinioRealtimeOptionsClient__channels,
            {"AAPL_220101C140.00", "MSFT"},
        )

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
             patch("intriniorealtime.options_client.close_websocket_app", side_effect=track_abort), \
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
             patch("intriniorealtime.options_client.close_websocket_app"), \
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
             patch("intriniorealtime.options_client.close_websocket_app"), \
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
             patch("intriniorealtime.options_client.close_websocket_app"):
            ws.start()
        self.assertEqual(calls, [1])

    def test_late_open_after_stop_is_aborted_before_becoming_ready(self):
        stop = threading.Event()
        stop.set()
        ws = _make_ws(stop)

        with patch("intriniorealtime.options_client.close_websocket_app") as abort:
            ws.on_open(ws)

        self.assertFalse(ws.isReady)
        abort.assert_called_once_with(ws)

    def test_late_open_after_timeout_is_aborted_before_becoming_ready(self):
        stop = threading.Event()
        ws = _make_ws(stop)

        def fake_run_forever(app, *args, **kwargs):
            time.sleep(0.08)
            app.on_open(app)
            stop.set()

        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0.03), \
             patch("intriniorealtime.options_client.should_abort_handshake", return_value=False), \
             patch("intriniorealtime.options_client.close_websocket_app") as abort, \
             patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever", fake_run_forever):
            ws.start()

        self.assertFalse(ws.isReady)
        self.assertTrue(abort.called)

    def test_relies_on_websocket_client_fragment_aggregation(self):
        stop = threading.Event()
        data_queue = queue.Queue()
        ws = _make_ws(stop, data_queue=data_queue)

        self.assertIsNone(ws.on_cont_message)
        ws.on_data(ws, b"complete-message", websocket.ABNF.OPCODE_BINARY, True)
        self.assertEqual(data_queue.get_nowait(), b"complete-message")

    def test_open_does_not_publish_ready_before_channel_replay_lock(self):
        class GateLock:
            def __init__(self):
                self.entered = threading.Event()
                self.release = threading.Event()

            def __enter__(self):
                self.entered.set()
                self.release.wait(timeout=1)
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        stop = threading.Event()
        channels_lock = GateLock()
        ws = _make_ws(stop, channels_lock=channels_lock)
        opener = threading.Thread(target=ws.on_open, args=(ws,))

        opener.start()
        self.assertTrue(channels_lock.entered.wait(timeout=1))
        self.assertFalse(ws.isReady)
        channels_lock.release.set()
        opener.join(timeout=1)

        self.assertFalse(opener.is_alive())
        self.assertTrue(ws.isReady)

    def test_open_rechecks_stop_before_publishing_ready(self):
        class GateLock:
            def __init__(self):
                self.entered = threading.Event()
                self.release = threading.Event()

            def __enter__(self):
                self.entered.set()
                self.release.wait(timeout=1)
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        stop = threading.Event()
        channels_lock = GateLock()
        ws = _make_ws(stop, channels_lock=channels_lock)
        opener = threading.Thread(target=ws.on_open, args=(ws,))

        opener.start()
        self.assertTrue(channels_lock.entered.wait(timeout=1))
        stop.set()
        channels_lock.release.set()
        opener.join(timeout=1)

        self.assertFalse(opener.is_alive())
        self.assertFalse(ws.isReady)

    def test_open_aborts_connection_when_channel_replay_fails(self):
        stop = threading.Event()
        sent_channels = set()
        ws = _make_ws(
            stop,
            channels={"AAPL"},
            sent_channels=sent_channels,
        )
        ws.send_binary = Mock(side_effect=RuntimeError("send failed"))

        with patch("intriniorealtime.options_client.close_websocket_app") as abort:
            with self.assertRaises(RuntimeError):
                ws.on_open(ws)

        self.assertFalse(ws.isReady)
        self.assertEqual(sent_channels, set())
        abort.assert_called_once_with(ws)

    def test_get_channels_returns_snapshot(self):
        client = _make_client()
        client._IntrinioRealtimeOptionsClient__channels = {"AAPL"}

        snapshot = client._IntrinioRealtimeOptionsClient__get_channels()
        snapshot.add("MSFT")

        self.assertEqual(client._IntrinioRealtimeOptionsClient__channels, {"AAPL"})

    def test_stats_are_isolated_between_clients(self):
        first = _make_client()
        second = _make_client()
        first_data = first._IntrinioRealtimeOptionsClient__data
        first_stats = first._IntrinioRealtimeOptionsClient__stats
        first_stats_lock = first._IntrinioRealtimeOptionsClient__stats_lock
        ws = _make_ws(
            threading.Event(),
            data_queue=first_data,
            stats=first_stats,
            stats_lock=first_stats_lock,
        )

        ws.on_data(ws, b"message", websocket.ABNF.OPCODE_BINARY, True)

        self.assertEqual(first.get_stats(), (1, 0, 1))
        self.assertEqual(second.get_stats(), (0, 0, 0))

    def test_worker_stops_before_dispatching_rest_of_batch(self):
        data = queue.Queue()
        stop = threading.Event()
        callbacks = []

        def on_quote(quote):
            callbacks.append(quote)
            stop.set()

        datum = bytearray(105)
        datum[0] = 2
        datum[1 + 22] = 1
        datum[1 + 52 + 22] = 1
        data.put(bytes(datum))

        worker = threading.Thread(
            target=_thread_fn,
            args=(0, data, None, on_quote, None, None, stop),
        )
        worker.start()
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(callbacks), 1)

    def test_truncated_message_does_not_terminate_worker(self):
        data = queue.Queue()
        stop = threading.Event()
        worker = threading.Thread(
            target=_thread_fn,
            args=(0, data, None, None, None, None, stop),
        )
        worker.start()

        data.put(b"\x01")
        time.sleep(0.05)

        self.assertTrue(worker.is_alive())
        stop.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_stop_preserves_channels_and_clears_started(self):
        client = _make_client()
        client._IntrinioRealtimeOptionsClient__channels = {"AAPL"}
        client._IntrinioRealtimeOptionsClient__sent_channels = {"AAPL"}
        client._IntrinioRealtimeOptionsClient__is_started = True
        ws = Mock()
        ws.isReady = True
        client._IntrinioRealtimeOptionsClient__webSocket = ws
        client._IntrinioRealtimeOptionsClient__socket_thread = None

        client.stop()

        self.assertEqual(client._IntrinioRealtimeOptionsClient__channels, {"AAPL"})
        self.assertEqual(client._IntrinioRealtimeOptionsClient__sent_channels, set())
        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_started)
        ws.send_binary.assert_not_called()
        ws.stop.assert_called()

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

    def test_join_does_not_wait_while_connection_is_unavailable(self):
        client = _make_client()
        client._IntrinioRealtimeOptionsClient__is_started = True
        client._IntrinioRealtimeOptionsClient__webSocket = None

        with patch.object(client, "_IntrinioRealtimeOptionsClient__all_ready") as ready:
            client.join("MSFT")
            client.join_firehose()

        ready.assert_not_called()
        self.assertIn("MSFT", client._IntrinioRealtimeOptionsClient__channels)
        self.assertIn("$FIREHOSE", client._IntrinioRealtimeOptionsClient__channels)

    def test_reconnect_token_refresh_invalidates_cached_token(self):
        client = _make_client()
        client._IntrinioRealtimeOptionsClient__token = ("cached", time.time())

        with patch("intriniorealtime.options_client.requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.text = "fresh"
            token = client._IntrinioRealtimeOptionsClient__refresh_token()

        self.assertEqual(token, "fresh")
        get.assert_called_once()

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

    def test_join_retries_send_after_previous_send_raises(self):
        client = _make_client()
        ws = Mock()
        ws.isReady = True
        ws.send_binary.side_effect = RuntimeError("connection closed")
        client._IntrinioRealtimeOptionsClient__webSocket = ws

        with self.assertRaises(RuntimeError):
            client.join("AAPL")

        ws.send_binary.side_effect = None
        client.join("AAPL")

        self.assertIn("AAPL", client._IntrinioRealtimeOptionsClient__channels)
        self.assertEqual(ws.send_binary.call_count, 2)

    def test_stop_joins_socket_thread_with_timeout(self):
        client = _make_client()
        thread = Mock()
        thread.ident = 1
        thread.is_alive.return_value = False
        client._IntrinioRealtimeOptionsClient__socket_thread = thread
        client._IntrinioRealtimeOptionsClient__webSocket = None
        with patch("intriniorealtime.options_client.time.sleep"):
            client.stop()
        timeout = thread.join.call_args.kwargs["timeout"]
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, _CONNECT_TIMEOUT_SECONDS + 1)

    def test_stop_from_worker_callback_does_not_join_current_thread(self):
        client = _make_client()
        current = threading.current_thread()
        client._IntrinioRealtimeOptionsClient__worker_threads = [current]
        client._IntrinioRealtimeOptionsClient__socket_thread = None
        client._IntrinioRealtimeOptionsClient__webSocket = None

        with patch("intriniorealtime.options_client.time.sleep"):
            client.stop()

        self.assertTrue(client._stop_event.is_set())
        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_started)

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
             patch("intriniorealtime.options_client.close_websocket_app"), \
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
             patch("intriniorealtime.options_client.close_websocket_app"), \
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
             patch("intriniorealtime.options_client.close_websocket_app"), \
             patch("threading.Event.wait", tracking_wait), \
             patch("intriniorealtime.options_client.websocket.WebSocketApp.run_forever", fake_run_forever):
            ws.start()

        self.assertEqual(run_count["n"], 2)
        self.assertIn(0.01, waits)

    def test_stop_sets_flag_before_socket_abort(self):
        client = _make_client()
        saw_stop = {}
        ws = Mock()
        ws.isReady = True
        client._IntrinioRealtimeOptionsClient__webSocket = ws

        def stop():
            saw_stop["set"] = client._stop_event.is_set()

        ws.stop.side_effect = stop
        client.stop()
        self.assertTrue(saw_stop["set"])

    def test_stop_aborts_socket_before_waiting_for_blocked_join(self):
        client = _make_client()
        ws = Mock()
        ws.isReady = True
        send_entered = threading.Event()
        release_send = threading.Event()

        def send_binary(_message):
            send_entered.set()
            release_send.wait(timeout=1)

        ws.send_binary.side_effect = send_binary
        ws.stop.side_effect = release_send.set
        client._IntrinioRealtimeOptionsClient__webSocket = ws
        joiner = threading.Thread(target=client.join, args=("AAPL",))
        stopper = threading.Thread(target=client.stop)

        joiner.start()
        self.assertTrue(send_entered.wait(timeout=1))
        stopper.start()
        stopper.join(timeout=1)
        joiner.join(timeout=1)

        self.assertFalse(stopper.is_alive())
        self.assertFalse(joiner.is_alive())
        ws.stop.assert_called()

    def test_start_raises_if_stopped_during_auth(self):
        client = _make_client()

        def get_token():
            client._stop_event.set()
            return None

        with patch.object(client, "_IntrinioRealtimeOptionsClient__get_token", side_effect=get_token), \
             patch.object(threading.Thread, "start") as thread_start:
            with self.assertRaisesRegex(RuntimeError, "Cannot start while client is stopping"):
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

    def test_stop_during_socket_thread_start_raises_lifecycle_error(self):
        client = _make_client()

        def stop_before_thread_runs():
            client.stop()

        with patch.object(client, "_IntrinioRealtimeOptionsClient__get_token", return_value="tok"), \
             patch.object(threading.Thread, "start", side_effect=stop_before_thread_runs), \
             patch("intriniorealtime.options_client.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "Cannot start while client is stopping"):
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
        leftover.is_alive.side_effect = [True, False]
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

    def test_start_fails_when_previous_socket_thread_does_not_exit(self):
        client = _make_client()
        leftover = Mock()
        leftover.ident = 1
        leftover.is_alive.return_value = True
        client._IntrinioRealtimeOptionsClient__socket_thread = leftover
        client._IntrinioRealtimeOptionsClient__is_started = False
        client._stop_event.set()

        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0), \
             patch("intriniorealtime.options_client.close_websocket_app"):
            with self.assertRaisesRegex(RuntimeError, "Previous socket thread"):
                client.start()

        leftover.join.assert_called_once_with(timeout=1)
        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_starting)

    def test_start_raises_clear_error_while_stop_is_in_progress(self):
        client = _make_client()
        client._IntrinioRealtimeOptionsClient__is_stopping = True

        with self.assertRaisesRegex(RuntimeError, "Cannot start while client is stopping"):
            client.start()

    def test_concurrent_start_waits_and_reports_active_start_failure(self):
        client = _make_client()
        token_entered = threading.Event()
        release_token = threading.Event()
        errors = []

        def fail_token():
            token_entered.set()
            release_token.wait(timeout=1)
            raise RuntimeError("auth failed")

        def start_client():
            try:
                client.start()
            except Exception as error:
                errors.append(str(error))

        with patch.object(client, "_IntrinioRealtimeOptionsClient__get_token", side_effect=fail_token):
            first = threading.Thread(target=start_client)
            second = threading.Thread(target=start_client)
            first.start()
            self.assertTrue(token_entered.wait(timeout=1))
            second.start()
            second.join(timeout=0.05)
            self.assertTrue(second.is_alive())

            release_token.set()
            first.join(timeout=1)
            second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(errors), 2)
        self.assertTrue(any(error == "auth failed" for error in errors))
        self.assertTrue(any(error.startswith("Concurrent start failed: auth failed") for error in errors))

    def test_stop_completes_teardown_with_active_socket(self):
        client = _make_client()
        client._IntrinioRealtimeOptionsClient__channels = {"AAPL"}
        client._IntrinioRealtimeOptionsClient__is_started = True
        ws = Mock()
        ws.isReady = True
        client._IntrinioRealtimeOptionsClient__webSocket = ws
        thread = Mock()
        thread.ident = 1
        thread.is_alive.return_value = False
        client._IntrinioRealtimeOptionsClient__socket_thread = thread

        client.stop()

        ws.stop.assert_called()
        thread.join.assert_called()
        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_started)

    def test_start_uses_new_stop_event_so_old_workers_stay_stopped(self):
        client = _make_client()
        old_event = client._stop_event
        old_event.set()
        old_worker = Mock()
        old_worker.ident = 1
        old_worker.is_alive.return_value = False
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

    def test_stop_reports_workers_that_do_not_exit(self):
        client = _make_client()
        worker = Mock()
        worker.ident = 1
        worker.is_alive.return_value = True
        client._IntrinioRealtimeOptionsClient__worker_threads = [worker]
        client._IntrinioRealtimeOptionsClient__socket_thread = None

        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "stop incomplete"):
                client.stop()

        self.assertTrue(client._IntrinioRealtimeOptionsClient__is_started)
        with self.assertRaisesRegex(RuntimeError, "Previous worker threads"):
            client.start()

    def test_stop_rechecks_survivors_after_all_joins(self):
        client = _make_client()
        worker = Mock()
        worker.ident = 1
        worker.is_alive.side_effect = [True, False]
        client._IntrinioRealtimeOptionsClient__worker_threads = [worker]
        client._IntrinioRealtimeOptionsClient__socket_thread = None

        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0):
            client.stop()

        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_started)

    def test_concurrent_stop_waits_for_active_stop(self):
        client = _make_client()
        join_entered = threading.Event()
        release_join = threading.Event()
        worker_alive = {"value": True}
        worker = Mock()
        worker.ident = 1
        worker.is_alive.side_effect = lambda: worker_alive["value"]

        def join(timeout=None):
            join_entered.set()
            release_join.wait(timeout=1)
            worker_alive["value"] = False

        worker.join.side_effect = join
        client._IntrinioRealtimeOptionsClient__worker_threads = [worker]
        client._IntrinioRealtimeOptionsClient__socket_thread = None
        failures = []

        def stop_client():
            try:
                client.stop()
            except Exception as error:
                failures.append(error)

        first = threading.Thread(target=stop_client)
        second = threading.Thread(target=stop_client)
        first.start()
        self.assertTrue(join_entered.wait(timeout=1))
        second.start()
        second.join(timeout=0.05)
        self.assertTrue(second.is_alive())

        release_join.set()
        first.join(timeout=1)
        second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(failures, [])

    def test_stop_uses_one_deadline_for_all_thread_joins(self):
        client = _make_client()
        clock = {"now": 100.0}
        workers = []

        for _ in range(2):
            worker = Mock()
            worker.ident = 1
            worker.is_alive.return_value = False

            def join(timeout=None, clock=clock):
                clock["now"] += 0.6

            worker.join.side_effect = join
            workers.append(worker)

        client._IntrinioRealtimeOptionsClient__worker_threads = workers
        client._IntrinioRealtimeOptionsClient__socket_thread = None

        with patch("intriniorealtime.options_client._CONNECT_TIMEOUT_SECONDS", 0), \
             patch("intriniorealtime.options_client.time.monotonic", side_effect=lambda: clock["now"]):
            client.stop()

        first_timeout = workers[0].join.call_args.kwargs["timeout"]
        second_timeout = workers[1].join.call_args.kwargs["timeout"]
        self.assertEqual(first_timeout, 1.0)
        self.assertAlmostEqual(second_timeout, 0.4)

    def test_start_aborts_leftover_websocket_while_joining(self):
        client = _make_client()
        leftover = Mock()
        leftover.ident = 1
        leftover.is_alive.side_effect = [True, False]
        leftover.join = Mock()
        ws = Mock()
        client._IntrinioRealtimeOptionsClient__socket_thread = leftover
        client._IntrinioRealtimeOptionsClient__webSocket = ws
        client._IntrinioRealtimeOptionsClient__is_started = False
        client._stop_event.set()
        start_calls = []

        def fake_ws_start(self):
            start_calls.append(1)

        with patch("intriniorealtime.options_client.close_websocket_app") as abort, \
             patch("intriniorealtime.options_client.requests.get") as get, \
             patch.object(_WebSocket, "start", fake_ws_start):
            get.return_value.status_code = 200
            get.return_value.text = "tok"
            client.start()

        abort.assert_called()
        leftover.join.assert_called()
        self.assertEqual(start_calls, [1])

    def test_start_raises_if_stop_occurs_during_leftover_join(self):
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

        with patch("intriniorealtime.options_client.close_websocket_app"), \
             patch.object(client, "_IntrinioRealtimeOptionsClient__get_token") as get_token, \
             patch.object(threading.Thread, "start") as thread_start:
            with self.assertRaisesRegex(RuntimeError, "Cannot start while client is stopping"):
                client.start()

        get_token.assert_not_called()
        thread_start.assert_not_called()
        self.assertFalse(client._IntrinioRealtimeOptionsClient__is_started)
