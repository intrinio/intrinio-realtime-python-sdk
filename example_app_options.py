import threading
import signal
import time
import sys
import logging
from threading import Event, Lock

from intriniorealtime.options_client import IntrinioRealtimeOptionsClient
from intriniorealtime.options_client import OptionsQuote
from intriniorealtime.options_client import OptionsTrade
from intriniorealtime.options_client import OptionsRefresh
from intriniorealtime.options_client import OptionsUnusualActivity
from intriniorealtime.options_client import OptionsUnusualActivityType
from intriniorealtime.options_client import OptionsUnusualActivitySentiment
from intriniorealtime.options_client import log
from intriniorealtime.options_client import Config
from intriniorealtime.options_client import Providers
from intriniorealtime.options_client import LogLevel

options_trade_count = 0
options_trade_count_lock = Lock()
options_quote_count = 0
options_quote_count_lock = Lock()
options_refresh_count = 0
options_refresh_count_lock = Lock()
options_ua_block_count = 0
options_ua_block_count_lock = Lock()
options_ua_sweep_count = 0
options_ua_sweep_count_lock = Lock()
options_ua_large_trade_count = 0
options_ua_large_trade_count_lock = Lock()
options_ua_unusual_sweep_count = 0
options_ua_unusual_sweep_count_lock = Lock()


def on_quote(quote: OptionsQuote):
    global options_quote_count
    global options_quote_count_lock
    with options_quote_count_lock:
        options_quote_count += 1


def on_trade(trade: OptionsTrade):
    global options_trade_count
    global options_trade_count_lock
    with options_trade_count_lock:
        options_trade_count += 1


def on_refresh(refresh: OptionsRefresh):
    global options_refresh_count
    global options_refresh_count_lock
    with options_refresh_count_lock:
        options_refresh_count += 1


def on_unusual_activity(ua: OptionsUnusualActivity):
    global options_ua_block_count
    global options_ua_block_count_lock
    global options_ua_sweep_count
    global options_ua_sweep_count_lock
    global options_ua_large_trade_count
    global options_ua_large_trade_count_lock
    global options_ua_unusual_sweep_count
    global options_ua_unusual_sweep_count_lock
    if ua.activity_type == OptionsUnusualActivityType.BLOCK:
        with options_ua_block_count_lock:
            options_ua_block_count += 1
    elif ua.activity_type == OptionsUnusualActivityType.SWEEP:
        with options_ua_sweep_count_lock:
            options_ua_sweep_count += 1
    elif ua.activity_type == OptionsUnusualActivityType.LARGE:
        with options_ua_large_trade_count_lock:
            options_ua_large_trade_count += 1
    elif ua.activity_type == OptionsUnusualActivityType.UNUSUAL_SWEEP:
        with options_ua_unusual_sweep_count_lock:
            options_ua_unusual_sweep_count += 1
    else:
        log("on_unusual_activity - Unknown activity_type {0}", ua.activity_type)


class Summarize(threading.Thread):
    def __init__(self, stop_flag: threading.Event, intrinio_client: IntrinioRealtimeOptionsClient):
        threading.Thread.__init__(self, group=None, args=(), kwargs={}, daemon=True)
        self.__stop_flag: threading.Event = stop_flag
        self.__client = intrinio_client

    def run(self):
        while not self.__stop_flag.is_set():
            time.sleep(30.0)
            (dataMsgs, txtMsgs, queueDepth) = self.__client.get_stats()
            log("Client Stats - Data Messages: {0}, Text Messages: {1}, Queue Depth: {2}".format(dataMsgs, txtMsgs, queueDepth))
            log(
                "App Stats - Trades: {0}, Quotes: {1}, Refreshes: {2}, Blocks: {3}, Sweeps: {4}, Large Trades: {5}, Unusual Sweeps: {6}"
                .format(
                    options_trade_count,
                    options_quote_count,
                    options_refresh_count,
                    options_ua_block_count,
                    options_ua_sweep_count,
                    options_ua_large_trade_count,
                    options_ua_unusual_sweep_count))


# Your config object MUST include the 'api_key' and 'provider', at a minimum
config: Config = Config(
    api_key="API_KEY_HERE",
    provider=Providers.OPRA,
    num_threads=8,
    symbols=["AAPL", "BRKB__230217C00300000"], # this is a static list of symbols (options contracts or option chains) that will automatically be subscribed to when the client starts
    log_level=LogLevel.INFO,
    delayed=False) #set delayed parameter to true if you have realtime access but want the data delayed 15 minutes anyway

# Register only the callbacks that you want.
# Take special care when registering the 'on_quote' handler as it will increase throughput by ~10x
intrinioRealtimeOptionsClient: IntrinioRealtimeOptionsClient = IntrinioRealtimeOptionsClient(config, on_trade=on_trade, on_quote=on_quote, on_refresh=on_refresh, on_unusual_activity=on_unusual_activity)

stop_event = Event()


def on_kill_process(sig, frame):
    log("Sample Application - Stopping")
    stop_event.set()
    intrinioRealtimeOptionsClient.stop()
    sys.exit(0)


signal.signal(signal.SIGINT, on_kill_process)

summarize_thread = Summarize(stop_event, intrinioRealtimeOptionsClient)
summarize_thread.start()

intrinioRealtimeOptionsClient.start()

#use this to join the channels already declared in your config
intrinioRealtimeOptionsClient.join()

# Use this to subscribe to the entire universe of symbols (option contracts). This requires special permission.
# intrinioRealtimeOptionsClient.join_firehose()

# Use this to subscribe, dynamically, to an option chain (all option contracts for a given underlying contract).
# intrinioRealtimeOptionsClient.join("AAPL")

# Use this to subscribe, dynamically, to a specific option contract.
# intrinioRealtimeOptionsClient.join("AAP___230616P00250000")

# Use this to subscribe, dynamically, a list of specific option contracts or option chains.
# intrinioRealtimeOptionsClient.join("GOOG__220408C02870000", "MSFT__220408C00315000", "AAPL__220414C00180000", "TSLA", "GE")

time.sleep(60 * 60 * 8)
# sigint, or ctrl+c, during the thread wait will also perform the same below code.
on_kill_process(None, None)
