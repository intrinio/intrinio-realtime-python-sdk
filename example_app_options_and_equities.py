import threading
import signal
import time
import sys
import logging
import datetime
from threading import Timer, Thread, Event, Lock


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

from intriniorealtime.equities_client import IntrinioRealtimeEquitiesClient
from intriniorealtime.equities_replay_client import IntrinioReplayEquitiesClient
from intriniorealtime.equities_client import EquitiesQuote
from intriniorealtime.equities_client import EquitiesTrade

equities_trade_count = 0
equities_ask_count = 0
equities_bid_count = 0
equities_backlog_count = 0

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


def on_equities_quote(quote, backlog):
    global equities_ask_count
    global equities_bid_count
    global equities_backlog_count
    equities_backlog_count = backlog
    if isinstance(quote, EquitiesQuote) and 'type' in quote.__dict__:
        if quote.type == "ask":
            equities_ask_count += 1
        else:
            equities_bid_count += 1

def on_equities_trade(trade, backlog):
    global equities_trade_count
    global equities_backlog_count
    equities_backlog_count = backlog
    equities_trade_count += 1


def on_options_quote(quote: OptionsQuote):
    global options_quote_count
    global options_quote_count_lock
    with options_quote_count_lock:
        options_quote_count += 1


def on_options_trade(trade: OptionsTrade):
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
        log(f"on_unusual_activity - Unknown activity_type {ua.activity_type}")


class Summarize(threading.Thread):
    def __init__(self, stop_flag: threading.Event, intrinio_options_client: IntrinioRealtimeOptionsClient, intrinio_equities_client: IntrinioRealtimeEquitiesClient):
        threading.Thread.__init__(self, group=None, args=(), kwargs={}, daemon=True)
        self.__stop_flag: threading.Event = stop_flag
        self.__options_client = intrinio_options_client
        self.__equities_client = intrinio_equities_client

    def run(self):
        global equities_trade_count
        global equities_bid_count
        global equities_ask_count
        global equities_backlog_count

        while not self.__stop_flag.wait(30):
            log("Equities Client Stats - trades: " + str(equities_trade_count) + "; asks: " + str(equities_ask_count) + "; bids: " + str(equities_bid_count) + "; backlog: " + str(equities_backlog_count))

            (dataMsgs, txtMsgs, queueDepth) = self.__options_client.get_stats()
            log("Options Client Stats - Data Messages: {0}, Text Messages: {1}, Queue Depth: {2}, Trades: {3}, Quotes: {4}, Refreshes: {5}, Blocks: {6}, Sweeps: {7}, Large Trades: {8}, Unusual Sweeps: {9}".format(
                dataMsgs,
                txtMsgs,
                queueDepth,
                options_trade_count,
                options_quote_count,
                options_refresh_count,
                options_ua_block_count,
                options_ua_sweep_count,
                options_ua_large_trade_count,
                options_ua_unusual_sweep_count
            ))

# Your config object MUST include the 'api_key' and 'provider', at a minimum
options_config: Config = Config(
    api_key="API_KEY_HERE",
    provider=Providers.OPRA, # or Providers.OPTIONS_EDGE
    num_threads=8,
    # symbols=[], # this is a static list of symbols (options contracts or option chains) that will automatically be subscribed to when the client starts
    log_level=LogLevel.INFO,
    delayed=False) #set delayed parameter to true if you have realtime access but want the data delayed 15 minutes anyway

equities_config = {
    'api_key': 'API_KEY_HERE',
    'provider': 'EQUITIES_EDGE' # 'REALTIME' (IEX), or 'IEX', or 'DELAYED_SIP', or 'NASDAQ_BASIC', or 'CBOE_ONE', or 'EQUITIES_EDGE'
    # ,'delayed': True # Add this if you have realtime (nondelayed) access and want to force delayed mode. If you only have delayed mode access, this is redundant.
    # ,'replay_date': datetime.date.today() - datetime.timedelta(days=1)  # needed for ReplayClient. The date to replay.
    # ,'with_simulated_delay': False  # needed for ReplayClient. This plays back the events at the same rate they happened in market.
    # ,'delete_file_when_done': True  # needed for ReplayClient
    # ,'write_to_csv': False  # needed for ReplayClient
    # ,'csv_file_path': 'data.csv'  # needed for ReplayClient
    # ,'bypass_parsing': True # if you want to handle parsing yourself, set this to True. Otherwise, leave it alone.
    # ,'debug': True
    ,'max_queue_size': 250000
}

# Register only the callbacks that you want.
# Take special care when registering the 'on_quote' handler as it will increase throughput by ~10x
intrinio_realtime_options_client: IntrinioRealtimeOptionsClient = IntrinioRealtimeOptionsClient(options_config, on_trade=on_options_trade, on_quote=on_options_quote, on_refresh=on_refresh, on_unusual_activity=on_unusual_activity)

intrinio_realtime_equities_client = IntrinioRealtimeEquitiesClient(equities_config, on_equities_trade, on_equities_quote)

stop_event = Event()


def on_kill_process(sig, frame):
    log("Sample Application - Stopping")
    stop_event.set()
    intrinio_realtime_options_client.stop()
    intrinio_realtime_equities_client.disconnect()
    sys.exit(0)


signal.signal(signal.SIGINT, on_kill_process)

summarize_thread = Summarize(stop_event, intrinio_realtime_options_client, intrinio_realtime_equities_client)
summarize_thread.start()

intrinio_realtime_options_client.start()

#use this to join the channels already declared in your config
#intrinio_realtime_options_client.join()

# Use this to subscribe to the entire universe of symbols (option contracts).
intrinio_realtime_options_client.join_firehose()

# Use this to subscribe, dynamically, to an option chain (all option contracts for a given underlying contract).
# intrinio_realtime_options_client.join("AAPL")

# Use this to subscribe, dynamically, to a specific option contract.
# intrinio_realtime_options_client.join("AAP___230616P00250000")

# Use this to subscribe, dynamically, a list of specific option contracts or option chains.
# intrinio_realtime_options_client.join("GOOG__220408C02870000", "MSFT__220408C00315000", "AAPL__220414C00180000", "TSLA", "GE")

intrinio_realtime_equities_client.connect()

#use this to join the channels already declared in your config
#intrinio_realtime_equities_client.join(['AAPL','GE','MSFT'])

# Use this to subscribe to the entire universe of symbols.
intrinio_realtime_equities_client.join(['lobby'])

time.sleep(60 * 60 * 24)
# sigint, or ctrl+c, during the thread wait will also perform the same below code.
on_kill_process(None, None)
