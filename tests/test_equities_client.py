import logging
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import websocket

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

    def test_on_connect_does_not_restore_ready_after_disconnect(self):
        client = _make_client()
        client.ws = Mock()
        client._stop_event.set()

        client.on_connect()

        self.assertFalse(client.ready)
        client.ws.send.assert_not_called()

    def test_watchdog_aborts_app_when_handshake_times_out(self):
        client = _make_client()
        client.token = "tok"
        receiver = EquitiesQuoteReceiver(client)
        aborted = []

        def fake_run_forever(app, *args, **kwargs):
            time.sleep(0.12)
            receiver.enabled = False
            client._stop_event.set()

        with patch("intriniorealtime.equities_client.close_websocket_app", side_effect=lambda app: aborted.append(app)), \
             patch("intriniorealtime.equities_client.CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("websocket.setdefaulttimeout"), \
             patch("websocket.WebSocketApp.run_forever", fake_run_forever):
            receiver.run()

        self.assertTrue(aborted)

    def test_run_does_not_change_websocket_client_global_timeout(self):
        client = _make_client()
        client.token = "tok"
        receiver = EquitiesQuoteReceiver(client)

        def fake_run_forever(app, *args, **kwargs):
            app.on_open(app)
            receiver.enabled = False
            client._stop_event.set()

        with patch("websocket.setdefaulttimeout") as set_timeout, \
             patch("websocket.WebSocketApp.run_forever", fake_run_forever):
            receiver.run()

        set_timeout.assert_not_called()

    def test_late_open_after_disconnect_is_aborted_before_becoming_ready(self):
        client = _make_client()
        client.token = "tok"
        receiver = EquitiesQuoteReceiver(client)
        aborted = []

        def fake_run_forever(app, *args, **kwargs):
            receiver.enabled = False
            client._stop_event.set()
            app.on_open(app)

        with patch("websocket.WebSocketApp.run_forever", fake_run_forever), \
             patch("intriniorealtime.equities_client.close_websocket_app", side_effect=aborted.append):
            receiver.run()

        self.assertFalse(client.ready)
        self.assertTrue(aborted)

    def test_late_open_after_timeout_is_aborted_before_becoming_ready(self):
        client = _make_client()
        client.token = "tok"
        receiver = EquitiesQuoteReceiver(client)
        aborted = []

        def fake_run_forever(app, *args, **kwargs):
            time.sleep(0.08)
            app.on_open(app)
            receiver.enabled = False

        with patch("websocket.WebSocketApp.run_forever", fake_run_forever), \
             patch("intriniorealtime.equities_client.CONNECT_TIMEOUT_SECONDS", 0.03), \
             patch("intriniorealtime.equities_client.should_abort_handshake", return_value=False), \
             patch("intriniorealtime.equities_client.close_websocket_app", side_effect=aborted.append):
            receiver.run()

        self.assertFalse(client.ready)
        self.assertTrue(aborted)

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
             patch("intriniorealtime.equities_client.close_websocket_app"):
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

        with patch("intriniorealtime.equities_client.close_websocket_app"):
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

    def test_quote_handler_stops_before_dispatching_rest_of_batch(self):
        messages = queue.Queue()
        callbacks = []
        fake_client = SimpleNamespace(
            logger=Mock(),
            quotes=messages,
            on_quote=None,
            on_trade=None,
        )
        handler = EquitiesQuoteHandler(fake_client, bypass_parsing=True)

        def on_trade(item, backlog):
            callbacks.append(item)
            handler.stop()

        fake_client.on_trade = on_trade
        datum = bytearray(49)
        datum[0] = 2
        datum[1] = 0
        datum[2] = 24
        datum[25] = 0
        datum[26] = 24
        messages.put(bytes(datum))

        handler.start()
        handler.join(timeout=1)

        self.assertFalse(handler.is_alive())
        self.assertEqual(len(callbacks), 1)

    def test_bypass_parsing_returns_complete_raw_record(self):
        for message_type, callback_name in ((0, "on_trade"), (1, "on_quote")):
            with self.subTest(message_type=message_type):
                callback = Mock()
                fake_client = SimpleNamespace(
                    logger=Mock(),
                    on_quote=None,
                    on_trade=None,
                )
                setattr(fake_client, callback_name, callback)
                handler = EquitiesQuoteHandler(fake_client, bypass_parsing=True)
                record = bytearray(range(24))
                record[0] = message_type
                record[1] = len(record)

                next_index = handler.parse_message(bytes(record), 0, 7)

                self.assertEqual(next_index, len(record))
                callback.assert_called_once_with(bytes(record), 7)

    def test_quote_handler_ignores_text_and_malformed_binary_messages(self):
        client = _make_client()
        client.on_trade = Mock()
        handler = EquitiesQuoteHandler(client, client.bypass_parsing)
        client.quote_handler = handler
        handler.start()
        client.quotes.put("server text message")
        malformed = bytearray(48)
        malformed[0] = 2
        malformed[2] = 255
        client.quotes.put(bytes(malformed))
        undersized = bytearray(25)
        undersized[0] = 1
        undersized[2] = 1
        client.quotes.put(bytes(undersized))

        time.sleep(0.05)

        self.assertTrue(handler.is_alive())
        client.on_trade.assert_not_called()
        client.disconnect()

    def test_connect_replaces_stopped_quote_handler_and_queue(self):
        client = _make_client()
        old_handler = client.quote_handler
        old_queue = client.quotes
        old_handler.stop()
        client.refresh_token = Mock()
        receiver = Mock()

        with patch.object(EquitiesQuoteHandler, "start") as start, \
             patch("intriniorealtime.equities_client.EquitiesQuoteReceiver", return_value=receiver):
            client.connect()

        self.assertIsNot(client.quote_handler, old_handler)
        self.assertIsNot(client.quotes, old_queue)
        start.assert_called_once()
        receiver.start.assert_called_once()

    def test_disconnect_and_reconnect_from_callback_replaces_current_handler(self):
        callback_finished = threading.Event()
        client_holder = {}

        def on_trade(item, backlog):
            client = client_holder["client"]
            client.disconnect()
            client.connect()
            callback_finished.set()

        client = IntrinioRealtimeEquitiesClient(
            {
                "api_key": "test-key",
                "provider": "IEX",
                "bypass_parsing": True,
            },
            on_trade,
            lambda *args: None,
        )
        client_holder["client"] = client
        original_handler = client.quote_handler
        client.refresh_token = Mock()
        client.refresh_websocket = Mock()
        datum = bytearray(25)
        datum[0] = 1
        datum[1] = 0
        datum[2] = 24

        client.quotes.put(bytes(datum))

        self.assertTrue(callback_finished.wait(timeout=1))
        original_handler.join(timeout=1)
        self.assertFalse(original_handler.is_alive())
        self.assertIsNot(client.quote_handler, original_handler)
        client.disconnect()

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

    def test_concurrent_connects_start_only_one_receiver(self):
        client = _make_client()
        client.refresh_quote_handler = Mock()
        client.refresh_token = Mock()
        start_entered = threading.Event()
        release_start = threading.Event()
        receivers = []

        class Receiver:
            enabled = True

            def __init__(self, owner):
                receivers.append(self)

            def start(self):
                start_entered.set()
                release_start.wait(timeout=1)

            def is_alive(self):
                return True

        with patch("intriniorealtime.equities_client.EquitiesQuoteReceiver", Receiver):
            first = threading.Thread(target=client.connect)
            second = threading.Thread(target=client.connect)
            first.start()
            self.assertTrue(start_entered.wait(timeout=1))
            second.start()
            time.sleep(0.05)
            self.assertEqual(len(receivers), 1)
            release_start.set()
            first.join(timeout=1)
            second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(len(receivers), 1)

    def test_connect_raises_while_disconnect_is_in_progress(self):
        client = _make_client()
        client.refresh_quote_handler = Mock()
        client.refresh_token = Mock()
        old_join_entered = threading.Event()
        release_disconnect = threading.Event()

        class OldReceiver:
            enabled = True
            alive = True
            join_calls = 0

            def is_alive(self):
                return self.alive

            def join(self, timeout=None):
                self.join_calls += 1
                if self.join_calls == 1:
                    old_join_entered.set()
                    release_disconnect.wait(timeout=2)
                else:
                    self.alive = False

        old_receiver = OldReceiver()
        new_receiver = Mock()
        new_receiver.enabled = True
        new_receiver.is_alive.return_value = True
        client.quote_receiver = old_receiver
        client.ws = Mock()

        with patch("intriniorealtime.equities_client.EquitiesQuoteReceiver", return_value=new_receiver), \
             patch("intriniorealtime.equities_client.close_websocket_app"):
            disconnect_thread = threading.Thread(target=client.disconnect)
            disconnect_thread.start()
            self.assertTrue(old_join_entered.wait(timeout=1))

            with self.assertRaisesRegex(RuntimeError, "Cannot connect while client is disconnecting"):
                client.connect()
            release_disconnect.set()
            disconnect_thread.join(timeout=1)

            client.connect()

        self.assertFalse(disconnect_thread.is_alive())
        self.assertIs(client.quote_receiver, new_receiver)

    def test_disconnect_clears_websocket_published_late_by_current_receiver(self):
        client = _make_client()
        late_websocket = Mock()

        class Receiver:
            enabled = True

            def join(self, timeout=None):
                client.ws = late_websocket

            def is_alive(self):
                return False

        receiver = Receiver()
        client.quote_receiver = receiver
        client.ws = None

        with patch("intriniorealtime.equities_client.close_websocket_app") as abort:
            client.disconnect()

        self.assertIsNone(client.quote_receiver)
        self.assertIsNone(client.ws)
        abort.assert_called_with(late_websocket)

    def test_concurrent_joins_serialize_channel_refreshes(self):
        client = _make_client()
        client.ready = True
        client.ws = Mock()
        first_send_entered = threading.Event()
        release_first_send = threading.Event()

        def send(_message, _opcode):
            if client.ws.send.call_count == 1:
                first_send_entered.set()
                release_first_send.wait(timeout=1)

        client.ws.send.side_effect = send
        first = threading.Thread(target=client.join, args=("AAPL",))
        second = threading.Thread(target=client.join, args=("MSFT",))

        first.start()
        self.assertTrue(first_send_entered.wait(timeout=1))
        second.start()
        time.sleep(0.05)
        self.assertEqual(client.ws.send.call_count, 1)
        release_first_send.set()
        first.join(timeout=1)
        second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(client.ws.send.call_count, 2)
        self.assertEqual(client.joined_channels, {"AAPL", "MSFT"})

    def test_disconnect_aborts_socket_before_waiting_for_blocked_join(self):
        client = _make_client()
        client.ready = True
        websocket_app = Mock()
        client.ws = websocket_app
        send_entered = threading.Event()
        release_send = threading.Event()

        def send(_message, _opcode):
            send_entered.set()
            release_send.wait(timeout=1)

        websocket_app.send.side_effect = send
        joiner = threading.Thread(target=client.join, args=("AAPL",))
        disconnecter = threading.Thread(target=client.disconnect)

        with patch(
            "intriniorealtime.equities_client.close_websocket_app",
            side_effect=lambda app: release_send.set(),
        ) as abort:
            joiner.start()
            self.assertTrue(send_entered.wait(timeout=1))
            disconnecter.start()
            disconnecter.join(timeout=1)
            joiner.join(timeout=1)

        self.assertFalse(disconnecter.is_alive())
        self.assertFalse(joiner.is_alive())
        abort.assert_any_call(websocket_app)

    def test_disconnect_aborts_socket_during_blocked_initial_channel_replay(self):
        client = _make_client()
        client.channels = {"AAPL"}
        client.ws = Mock()
        send_entered = threading.Event()
        release_send = threading.Event()

        def send(*args, **kwargs):
            send_entered.set()
            release_send.wait(timeout=1)

        client.ws.send.side_effect = send
        opener = threading.Thread(target=client.on_connect)
        stopper = threading.Thread(target=client.disconnect)

        with patch(
            "intriniorealtime.equities_client.close_websocket_app",
            side_effect=lambda app: release_send.set(),
        ) as abort:
            opener.start()
            self.assertTrue(send_entered.wait(timeout=1))
            stopper.start()
            stopper.join(timeout=1)
            opener.join(timeout=1)

        self.assertFalse(stopper.is_alive())
        self.assertFalse(opener.is_alive())
        abort.assert_called()
        self.assertFalse(client.ready)

    def test_partial_channel_refresh_records_successful_sends(self):
        client = _make_client()
        client.ready = True
        client.channels = {"AAPL", "MSFT"}
        client.join_binary_message = Mock(side_effect=lambda channel: channel)
        client.ws = Mock()
        client.ws.send.side_effect = [None, RuntimeError("send failed")]

        with self.assertRaises(RuntimeError):
            client.refresh_channels()

        successful_channel = client.ws.send.call_args_list[0].args[0]
        failed_channel = client.ws.send.call_args_list[1].args[0]
        self.assertEqual(client.joined_channels, {successful_channel})

        client.ws.send.reset_mock(side_effect=True)
        client.refresh_channels()

        client.ws.send.assert_called_once_with(
            failed_channel,
            websocket.ABNF.OPCODE_BINARY,
        )
        self.assertEqual(client.joined_channels, {"AAPL", "MSFT"})

        client.channels = set()
        client.leave_binary_message = Mock(side_effect=lambda channel: channel)
        client.ws.send.reset_mock(side_effect=True)
        client.ws.send.side_effect = [None, RuntimeError("send failed")]

        with self.assertRaises(RuntimeError):
            client.refresh_channels()

        successful_leave = client.ws.send.call_args_list[0].args[0]
        failed_leave = client.ws.send.call_args_list[1].args[0]
        self.assertNotIn(successful_leave, client.joined_channels)
        self.assertEqual(client.joined_channels, {failed_leave})

        client.ws.send.reset_mock(side_effect=True)
        client.refresh_channels()

        client.ws.send.assert_called_once_with(
            failed_leave,
            websocket.ABNF.OPCODE_BINARY,
        )
        self.assertEqual(client.joined_channels, set())

    def test_connect_queued_before_disconnect_does_not_restart(self):
        client = _make_client()
        auth_entered = threading.Event()
        auth_calls = []

        def refresh_token():
            auth_calls.append(1)
            auth_entered.set()
            client._stop_event.wait(timeout=1)

        client.refresh_quote_handler = Mock()
        client.refresh_token = Mock(side_effect=refresh_token)
        client.refresh_websocket = Mock()
        first = threading.Thread(target=client.connect)
        second = threading.Thread(target=client.connect)

        first.start()
        self.assertTrue(auth_entered.wait(timeout=1))
        second.start()
        time.sleep(0.05)
        client.disconnect()
        first.join(timeout=1)
        second.join(timeout=1)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(auth_calls, [1])
        client.refresh_websocket.assert_not_called()

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

    def test_receiver_relies_on_websocket_client_fragment_aggregation(self):
        client = _make_client()
        client.token = "tok"
        receiver = EquitiesQuoteReceiver(client)
        continuation_callbacks = []

        def fake_run_forever(app, *args, **kwargs):
            continuation_callbacks.append(app.on_cont_message)
            receiver.enabled = False
            client._stop_event.set()

        with patch("websocket.setdefaulttimeout"), \
             patch("intriniorealtime.equities_client.CONNECT_TIMEOUT_SECONDS", 0.05), \
             patch("websocket.WebSocketApp.run_forever", fake_run_forever), \
             patch("intriniorealtime.equities_client.close_websocket_app"):
            receiver.run()

        self.assertEqual(continuation_callbacks, [None])

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
        with patch("intriniorealtime.equities_client.close_websocket_app") as abort:
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

        with patch("intriniorealtime.equities_client.close_websocket_app") as abort, \
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
             patch("intriniorealtime.equities_client.close_websocket_app"):
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
             patch("intriniorealtime.equities_client.close_websocket_app"):
            receiver.run()

        self.assertEqual(run_count["n"], 2)
        self.assertEqual(client.refresh_token.call_count, 1)
        client.do_backoff.assert_not_called()


if __name__ == "__main__":
    unittest.main()
