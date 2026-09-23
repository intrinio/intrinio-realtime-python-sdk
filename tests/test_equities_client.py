import logging
import time
import unittest
from unittest.mock import Mock, patch

from intriniorealtime.equities_client import (
    CONNECT_TIMEOUT_SECONDS,
    EquitiesQuoteHandler,
    EquitiesQuoteReceiver,
    IntrinioRealtimeEquitiesClient,
)


def _make_client():
    with patch.object(EquitiesQuoteHandler, "start"):
        return IntrinioRealtimeEquitiesClient(
            {"api_key": "test-key", "provider": "IEX"},
            lambda *args: None,
            lambda *args: None,
        )


class EquitiesReconnectTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_on_connect_rejoins_channels_after_reconnect(self):
        client = _make_client()
        client.channels = {"AAPL"}
        client.joined_channels = {"AAPL"}
        client.ready = False
        client.ws = Mock()

        client.on_connect()

        self.assertTrue(client.ready)
        self.assertEqual(client.joined_channels, {"AAPL"})
        client.ws.send.assert_called()

    def test_on_connect_does_not_skip_join_when_joined_channels_are_stale(self):
        client = _make_client()
        client.channels = {"AAPL", "MSFT"}
        client.joined_channels = {"AAPL", "MSFT"}
        client.ready = True
        client.ws = Mock()

        client.refresh_channels()
        client.ws.send.assert_not_called()

        client.on_connect()
        self.assertEqual(client.ws.send.call_count, 2)

    def test_watchdog_aborts_app_when_handshake_times_out(self):
        client = _make_client()
        client.token = "tok"
        receiver = EquitiesQuoteReceiver(client)
        aborted = []

        def fake_run_forever(app, *args, **kwargs):
            time.sleep(0.12)
            receiver.enabled = False
            client._stop_event.set()

        with patch("intriniorealtime.equities_client.abort_websocket_app", side_effect=lambda app: aborted.append(app)), \
             patch("intriniorealtime.equities_client.CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("websocket.setdefaulttimeout"), \
             patch("websocket.WebSocketApp.run_forever", fake_run_forever):
            receiver.run()

        self.assertTrue(aborted)

    def test_run_sets_connect_timeout_and_clears_it_on_open(self):
        client = _make_client()
        client.token = "tok"
        receiver = EquitiesQuoteReceiver(client)
        sock = Mock()

        def fake_run_forever(app, *args, **kwargs):
            app.sock = sock
            app.on_open(app)
            receiver.enabled = False
            client._stop_event.set()

        with patch("websocket.setdefaulttimeout") as set_timeout, \
             patch("websocket.WebSocketApp.run_forever", fake_run_forever):
            receiver.run()

        self.assertIn(((CONNECT_TIMEOUT_SECONDS,), {}), set_timeout.call_args_list)
        set_timeout.assert_called_with(None)
        sock.settimeout.assert_called_with(None)

    def test_refresh_token_failure_backs_off_and_skips_socket_until_auth_succeeds(self):
        client = _make_client()
        client.token = "stale"
        client.do_backoff = Mock()
        attempts = {"n": 0}

        def refresh():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("Auth failed")

        client.refresh_token = Mock(side_effect=refresh)
        receiver = EquitiesQuoteReceiver(client)
        run_forever_calls = []

        def fake_run_forever(app, *args, **kwargs):
            run_forever_calls.append(1)
            if len(run_forever_calls) >= 2:
                receiver.enabled = False
                client._stop_event.set()

        with patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.equities_client.CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("websocket.WebSocketApp.run_forever", fake_run_forever), \
             patch("intriniorealtime.equities_client.abort_websocket_app"):
            receiver.run()

        self.assertEqual(len(run_forever_calls), 2)
        self.assertEqual(client.refresh_token.call_count, 3)
        self.assertGreaterEqual(client.do_backoff.call_count, 3)

    def test_disconnect_then_connect_starts_a_new_receiver(self):
        client = _make_client()
        client.refresh_token = Mock()
        old = Mock()
        old.is_alive.return_value = True
        old.enabled = True

        def join_and_exit(timeout=None):
            old.is_alive.return_value = False

        old.join.side_effect = join_and_exit
        client.quote_receiver = old
        client.ws = Mock()

        with patch("intriniorealtime.equities_client.abort_websocket_app"):
            client.disconnect()

        self.assertFalse(old.enabled)
        old.join.assert_called()
        self.assertIsNone(client.quote_receiver)
        self.assertTrue(client._stop_event.is_set())

        new_receiver = Mock()
        with patch("intriniorealtime.equities_client.EquitiesQuoteReceiver", return_value=new_receiver) as ctor:
            client.connect()

        ctor.assert_called_once()
        new_receiver.start.assert_called_once()
        self.assertIs(client.quote_receiver, new_receiver)
        self.assertFalse(client._stop_event.is_set())

    def test_refresh_websocket_replaces_disabled_live_receiver(self):
        client = _make_client()
        old = Mock()
        old.is_alive.return_value = True
        old.enabled = False

        def join_and_exit(timeout=None):
            old.is_alive.return_value = False

        old.join.side_effect = join_and_exit
        client.quote_receiver = old
        new_receiver = Mock()

        with patch("intriniorealtime.equities_client.EquitiesQuoteReceiver", return_value=new_receiver):
            client.refresh_websocket()

        old.join.assert_called()
        new_receiver.start.assert_called_once()
        self.assertIs(client.quote_receiver, new_receiver)

    def test_connect_on_healthy_session_does_not_close_socket(self):
        client = _make_client()
        client.ready = True
        client.refresh_token = Mock()
        receiver = Mock()
        receiver.is_alive.return_value = True
        receiver.enabled = True
        client.quote_receiver = receiver
        ws = Mock()
        client.ws = ws

        client.connect()

        ws.close.assert_not_called()
        client.refresh_token.assert_not_called()
        self.assertIs(client.quote_receiver, receiver)
        self.assertTrue(client.ready)
        self.assertTrue(receiver.enabled)

    def test_connect_while_receiver_is_starting_does_not_close_socket(self):
        client = _make_client()
        client.ready = False
        client.refresh_token = Mock()
        receiver = Mock()
        receiver.is_alive.return_value = True
        receiver.enabled = True
        client.quote_receiver = receiver
        ws = Mock()
        client.ws = ws

        client.connect()

        ws.close.assert_not_called()
        client.refresh_token.assert_not_called()
        self.assertIs(client.quote_receiver, receiver)

    def test_connect_resets_backoff_on_new_attempt(self):
        client = _make_client()
        client.last_self_heal_backoff = 4
        client.refresh_token = Mock()
        new_receiver = Mock()
        with patch("intriniorealtime.equities_client.EquitiesQuoteReceiver", return_value=new_receiver):
            client.connect()
        self.assertEqual(client.last_self_heal_backoff, -1)
        new_receiver.start.assert_called_once()

    def test_connect_healthy_noop_does_not_reset_backoff(self):
        client = _make_client()
        client.last_self_heal_backoff = 4
        receiver = Mock()
        receiver.is_alive.return_value = True
        receiver.enabled = True
        client.quote_receiver = receiver
        client.connect()
        self.assertEqual(client.last_self_heal_backoff, 4)

    def test_receiver_clears_continuation_queue_on_each_connect_attempt(self):
        client = _make_client()
        client.token = "tok"
        client.do_backoff = Mock()
        client.refresh_token = Mock()
        receiver = EquitiesQuoteReceiver(client)
        receiver.continuation_queue.put(b"partial-from-prior-session")
        seen_empty = []
        run_count = {"n": 0}

        def fake_run_forever(app, *args, **kwargs):
            run_count["n"] += 1
            seen_empty.append(receiver.continuation_queue.empty())
            if run_count["n"] == 1:
                receiver.continuation_queue.put(b"leftover-mid-fragment")
                return
            receiver.enabled = False
            client._stop_event.set()

        with patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.equities_client.CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("websocket.WebSocketApp.run_forever", fake_run_forever), \
             patch("intriniorealtime.equities_client.abort_websocket_app"):
            receiver.run()

        self.assertEqual(run_count["n"], 2)
        self.assertEqual(seen_empty, [True, True])

    def test_fragmented_message_is_reassembled_and_delivered(self):
        client = _make_client()
        receiver = EquitiesQuoteReceiver(client)
        ws = Mock()
        receiver.on_message = Mock()

        receiver.on_cont_message(ws, b"abc", 0)
        receiver.on_cont_message(ws, b"def", 1)

        receiver.on_message.assert_called_once_with(ws, b"abcdef")

    def test_refresh_token_passes_timeout(self):
        client = _make_client()
        with patch("intriniorealtime.equities_client.requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.text = "tok"
            client.refresh_token()
        self.assertEqual(get.call_args.kwargs["timeout"], CONNECT_TIMEOUT_SECONDS)

    def test_connect_does_not_start_receiver_if_stopped_during_auth(self):
        client = _make_client()

        def refresh():
            client._stop_event.set()

        client.refresh_token = Mock(side_effect=refresh)
        with patch("intriniorealtime.equities_client.EquitiesQuoteReceiver") as ctor:
            client.connect()
        ctor.assert_not_called()

    def test_disconnect_keeps_receiver_if_join_times_out(self):
        client = _make_client()
        old = Mock()
        old.is_alive.return_value = True
        old.enabled = True
        old.join = Mock()
        client.quote_receiver = old
        client.ws = Mock()

        ws = client.ws
        with patch("intriniorealtime.equities_client.abort_websocket_app") as abort:
            client.disconnect()

        self.assertFalse(old.enabled)
        old.join.assert_called()
        self.assertIs(client.quote_receiver, old)
        self.assertIs(client.ws, ws)
        self.assertGreaterEqual(abort.call_count, 2)

    def test_refresh_websocket_does_not_replace_receiver_still_alive_after_join(self):
        client = _make_client()
        old = Mock()
        old.is_alive.return_value = True
        old.enabled = False
        old.join = Mock()
        client.quote_receiver = old
        client.ws = Mock()

        with patch("intriniorealtime.equities_client.abort_websocket_app") as abort, \
             patch("intriniorealtime.equities_client.EquitiesQuoteReceiver") as ctor:
            with self.assertRaises(RuntimeError):
                client.refresh_websocket()

        ctor.assert_not_called()
        self.assertIs(client.quote_receiver, old)
        self.assertGreaterEqual(abort.call_count, 2)

    def test_open_then_immediate_drop_backs_off(self):
        client = _make_client()
        client.token = "tok"
        client.do_backoff = Mock()
        client.refresh_token = Mock()
        receiver = EquitiesQuoteReceiver(client)
        run_count = {"n": 0}

        def fake_run_forever(app, *args, **kwargs):
            run_count["n"] += 1
            if run_count["n"] == 1:
                app.on_open(app)
                return
            receiver.enabled = False
            client._stop_event.set()

        with patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.equities_client.CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("intriniorealtime.equities_client.STABLE_SESSION_SECONDS", 10), \
             patch("websocket.WebSocketApp.run_forever", fake_run_forever), \
             patch("intriniorealtime.equities_client.abort_websocket_app"):
            receiver.run()

        self.assertEqual(run_count["n"], 2)
        client.do_backoff.assert_called()

    def test_stable_session_drop_skips_first_backoff(self):
        client = _make_client()
        client.token = "tok"
        client.do_backoff = Mock()
        client.refresh_token = Mock()
        receiver = EquitiesQuoteReceiver(client)
        run_count = {"n": 0}

        def fake_run_forever(app, *args, **kwargs):
            run_count["n"] += 1
            if run_count["n"] == 1:
                app.on_open(app)
                time.sleep(0.06)
                return
            receiver.enabled = False
            client._stop_event.set()

        with patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.equities_client.CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("intriniorealtime.equities_client.STABLE_SESSION_SECONDS", 0.05), \
             patch("websocket.WebSocketApp.run_forever", fake_run_forever), \
             patch("intriniorealtime.equities_client.abort_websocket_app"):
            receiver.run()

        self.assertEqual(run_count["n"], 2)
        self.assertEqual(client.refresh_token.call_count, 1)
        client.do_backoff.assert_not_called()


if __name__ == "__main__":
    unittest.main()
