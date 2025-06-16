# intrinio realtime python sdk
SDK for working with Intrinio's realtime OPRA, IEX, delayed SIP, CBOE One, or NASDAQ Basic prices feeds.  Get a comprehensive view with increased market volume and enjoy minimized exchange and per user fees.

[Intrinio](https://intrinio.com/) provides real-time stock and option prices via a two-way WebSocket connection. To get started, [subscribe to a real-time equity feed](https://intrinio.com/real-time-multi-exchange), or [subscribe to a real-time options feed](https://intrinio.com/financial-market-data/options-data) and follow the instructions below.

## Requirements

- Python 3.10
- NOTE: You need https://pypi.org/project/websocket-client/, not https://pypi.org/project/websocket/.

## Docker
Add your API key to the example_app_equities.py or example_app_options.py file, comment the correct line (16 or 17) in Dockerfile, then
```
docker compose build
docker compose run client
```

## Features

### Equities

* Receive streaming, real-time pricing (trades, NBBO bid, ask)
* Subscribe to updates from individual securities, individual contracts, or
* Subscribe to updates for all securities (Lobby/Firehose mode)
* Replay a specific day (at actual pace or as fast as it loads) while the servers are down, either for testing or fetching missed data.

### Options

* Receive streaming, real-time option price updates:
  * every trade
  * conflated bid and ask
  * open interest, open, close, high, low
  * unusual activity(block trades, sweeps, whale trades, unusual sweeps)
* Subscribe to updates from individual options contracts (or option chains)
* Subscribe to updates for the entire universe of option contracts (~1.5M option contracts)

## Installation
```
pip install intriniorealtime
```

## Handling Quotes and the Queue

There are thousands of securities (and millions of options contracts), each with their own feed of activity.  We highly encourage you to make your on-event handlers as short as possible and follow a queue pattern so your app can handle the volume of activity.  
Note that quotes (ask and bid updates) comprise 99% of the volume of the entire feed. Be cautious when deciding to receive quote updates.

## Example Equities Usage

```python
import threading
import signal
import time
import sys
import datetime
from threading import Timer,Thread,Event,Lock

from intriniorealtime.equities_client import IntrinioRealtimeEquitiesClient
from intriniorealtime.equities_replay_client import IntrinioReplayEquitiesClient
from intriniorealtime.equities_client import EquitiesQuote
from intriniorealtime.equities_client import EquitiesTrade

trade_count = 0
ask_count = 0
bid_count = 0
backlog_count = 0

def on_quote(quote, backlog):
        global ask_count
        global bid_count
        global backlog_count
        backlog_count = backlog
        if isinstance(quote, EquitiesQuote) and 'type' in quote.__dict__:
            if quote.type == "ask": ask_count += 1
            else: bid_count += 1

def on_trade(trade, backlog): 
        global trade_count
        global backlog_count
        backlog_count = backlog
        trade_count += 1

class Summarize(threading.Thread):
    def __init__(self, stop_flag):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.stop_flag = stop_flag

    def run(self):
        global trade_count
        global bid_count
        global ask_count
        global backlog_count
        while not self.stop_flag.wait(5):
            print("trades: " + str(trade_count) + "; asks: " + str(ask_count) + "; bids: " + str(bid_count) + "; backlog: " + str(backlog_count))


configuration = {
    'api_key': 'API_KEY_HERE',
    'provider': 'IEX' # 'REALTIME' (IEX), or 'IEX', or 'DELAYED_SIP', or 'NASDAQ_BASIC', or 'CBOE_ONE'
    # ,'delayed': True # Add this if you have realtime (nondelayed) access and want to force delayed mode. If you only have delayed mode access, this is redundant.
    # ,'replay_date': datetime.date.today() - datetime.timedelta(days=1)  # needed for ReplayClient. The date to replay.
    # ,'with_simulated_delay': False  # needed for ReplayClient. This plays back the events at the same rate they happened in market.
    # ,'delete_file_when_done': True  # needed for ReplayClient
    # ,'write_to_csv': False  # needed for ReplayClient
    # ,'csv_file_path': 'data.csv'  # needed for ReplayClient
    # ,'bypass_parsing': True # if you want to handle parsing yourself, set this to True. Otherwise, leave it alone.
    # ,'debug': True
    # ,'max_queue_size': 250000
}


client = IntrinioRealtimeEquitiesClient(configuration, on_trade, on_quote)
# client = IntrinioReplayClient(options, on_trade, on_quote)
stop_event = Event()


def on_kill_process(sig, frame):
    print("Stopping")
    stop_event.set()
    client.disconnect()
    sys.exit(0)


signal.signal(signal.SIGINT, on_kill_process)


client.join(['AAPL','GE','MSFT'])
# client.join(['lobby'])
client.connect()

summarize_thread = Summarize(stop_event)
summarize_thread.start()

time.sleep(120)

# sigint, or ctrl+c, during the thread wait will also perform the same below code.
print("Stopping")
stop_event.set()
client.disconnect()
sys.exit(0)
```

## Equities Data Format

### Quote Message

```python
{ 'symbol': 'AAPL',
  'type': 'ask',
  'price': '102.3',
  'size': 100,
  'timestamp': 1636395583000000000,
  'subprovider': 'UTP',
  'market_center': '',
  'condition': '' }
```

*   **symbol** - the ticker of the security
*   **type** - the quote type
  *    **`ask`** - represents the top-of-book ask price
  *    **`bid`** - represents the top-of-book bid price
*   **price** - the price in USD
*   **size** - the size of the `last` trade, or total volume of orders at the top-of-book `bid` or `ask` price
*   **timestamp** - a Unix timestamp (nanoseconds since unix epoch)
*   **subprovider** - Denotes the detailed source within grouped sources.
  *    **`NO_SUBPROVIDER`** - No subtype specified.
  *    **`CTA_A`** - CTA_A in the DELAYED_SIP provider.
  *    **`CTA_B`** - CTA_B in the DELAYED_SIP provider.
  *    **`UTP`** - UTP in the DELAYED_SIP provider.
  *    **`OTC`** - OTC in the DELAYED_SIP provider.
  *    **`NASDAQ_BASIC`** - NASDAQ Basic in the NASDAQ_BASIC provider.
  *    **`IEX`** - From the IEX exchange in the REALTIME provider.
* **market_center** - Provides the market center
* **condition** - Provides the condition

### Trade Message

```python
{ 'symbol': 'AAPL',
  'total_volume': '106812',
  'price': '102.3',
  'size': 100,
  'timestamp': 1636395583000000000,
  'subprovider': 'IEX',
  'market_center': '',
  'condition': '' }
```

*   **symbol** - the ticker of the security
*   **total_volume** - the total volume of trades for the security so far today.
*   **price** - the price in USD
*   **size** - the size of the `last` trade, or total volume of orders at the top-of-book `bid` or `ask` price
*   **timestamp** - a Unix timestamp (nanoseconds since unix epoch)
*   **subprovider** - Denotes the detailed source within grouped sources.
  *    **`NO_SUBPROVIDER`** - No subtype specified.
  *    **`CTA_A`** - CTA_A in the DELAYED_SIP provider.
  *    **`CTA_B`** - CTA_B in the DELAYED_SIP provider.
  *    **`UTP`** - UTP in the DELAYED_SIP provider.
  *    **`OTC`** - OTC in the DELAYED_SIP provider.
  *    **`NASDAQ_BASIC`** - NASDAQ Basic in the NASDAQ_BASIC provider.
  *    **`IEX`** - From the IEX exchange in the REALTIME provider.
* **market_center** - Provides the market center
* **condition** - Provides the condition


## Example Options Usage
```python
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
        threading.Thread.__init__(self, args=(), kwargs=None, daemon=True)
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

time.sleep(60 * 60)
# sigint, or ctrl+c, during the thread wait will also perform the same below code.
on_kill_process(None, None)

```

## Options Data Format
### Trade Message

```python
class Trade:
    def __init__(self, contract: str, exchange: Exchange, price: float, size: int, timestamp: float, total_volume: int, qualifiers: tuple, ask_price_at_execution: float, bid_price_at_execution: float, underlying_price_at_execution: float):
        self.contract: str = contract
        self.exchange: Exchange = exchange
        self.price: float = price
        self.size: int = size
        self.timestamp: float = timestamp
        self.total_volume: int = total_volume
        self.qualifiers: tuple = qualifiers
        self.ask_price_at_execution = ask_price_at_execution
        self.bid_price_at_execution = bid_price_at_execution
        self.underlying_price_at_execution = underlying_price_at_execution
```

* **contract** - Identifier for the options contract.  This includes the ticker symbol, put/call, expiry, and strike price.
* **exchange** - Exchange(IntEnum): the specific exchange through which the trade occurred
* **price** - the price in USD
* **size** - the size of the last trade in hundreds (each contract is for 100 shares).
* **total_volume** - The number of contracts traded so far today.
* **timestamp** - a Unix timestamp (with microsecond precision)
* **qualifiers** - a tuple containing 4 ints: each item represents one trade qualifier. see list of possible [Trade Qualifiers](#trade-qualifiers), below. 
* **ask_price_at_execution** - the contract ask price in USD at the time of execution.
* **bid_price_at_execution** - the contract bid price in USD at the time of execution.
* **underlying_price_at_execution** - the contract's underlying security price in USD at the time of execution.

### Trade Qualifiers

### Option Trade Qualifiers

| Value | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | 
|-------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------| 
| 0     | Transaction is a regular trade                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | 
| 1     | Out-of-sequence cancellation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 2     | Transaction is being reported late and is out-of-sequence                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | 
| 3     | In-sequence cancellation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 4     | Transaction is being reported late, but is in correct sequence.                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 5     | Cancel the first trade of the day                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | 
| 6     | Late report of the opening trade and is out -of-sequence. Send an open price.                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 7     | Transaction was the only  one reported this day for the particular option contract and is now to be cancelled.                                                                                                                                                                                                                                                                                                                                                                                                             |
| 8     | Late report of an opening trade and is in correct sequence. Process as regular trade.                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| 9     | Transaction was executed electronically. Process as regular trade.                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| 10    | Re-opening of a contract which was halted earlier. Process as regular trade.                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 11    | Transaction is a contract for which the terms have been adjusted to reflect stock dividend, stock split or similar event. Process as regular trade.                                                                                                                                                                                                                                                                                                                                                                        |
| 12    | Transaction represents a trade in two options of same class (a buy and a sell in the same class). Process as regular trade.                                                                                                                                                                                                                                                                                                                                                                                                |
| 13    | Transaction represents a trade in two options of same class (a buy and a sell in a put and a call.). Process as regular trade.                                                                                                                                                                                                                                                                                                                                                                                             |
| 14    | Transaction is the execution of a sale at a price agreed upon by the floor personnel involved, where a condition of the trade is that it reported following a non -stopped trade of the same series at the same price.                                                                                                                                                                                                                                                                                                     |
| 15    | Cancel stopped transaction.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 16    | Transaction represents the option portion of buy/write (buy stock, sell call options). Process as regular trade.                                                                                                                                                                                                                                                                                                                                                                                                           |
| 17    | Transaction represents the buying of a call and selling of a put for same underlying stock or index. Process as regular trade.                                                                                                                                                                                                                                                                                                                                                                                             |
| 18    | Transaction was the execution of an order which was “stopped” at a price that did not constitute a Trade-Through  on another market at the time of the stop.  Process like a normal transaction.                                                                                                                                                                                                                                                                                                                           |
| 19    | Transaction was the execution of an order identified as an Intermarket Sweep Order. Updates open, high, low, and last.                                                                                                                                                                                                                                                                                                                                                                                                     |
| 20    | Transaction reflects the execution of a “benchmark trade”. A “benchmark trade” is a trade resulting from the matching of “benchmark orders”. A “benchmark order” is an order for which the price is not based, directly or indirectly, on the quoted price of th e option at the time of the order’s execution and for which the material terms were not reasonably determinable at the time a commitment to trade the order was made. Updates open, high, and low, but not last unless the trade is the first of the day. |
| 24    | Transaction is trade through exempt, treat like a regular trade.                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 27    | “a” (Single leg auction non ISO)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 28    | “b” (Single leg auction ISO)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 29    | “c” (Single leg cross Non ISO)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 30    | “d” (Single leg cross ISO)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 31    | “e” (Single leg floor trade)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 32    | “f” (Multi leg auto electronic trade)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| 33    | “g” (Multi leg auction trade)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 34    | “h” (Multi leg Cross trade)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 35    | “i” (Multi leg floor trade)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 36    | “j” (Multi leg auto electronic trade against single leg)                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 37    | “k” (Stock options Auction)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 38    | “l” (Multi leg auction trade against single leg)                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 39    | “m” (Multi leg floor trade against single leg)                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 40    | “n” (Stock options auto electronic trade)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| 41    | “o” (Stock options cross trade)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 42    | “p” (Stock options floor trade)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 43    | “q” (Stock options auto electronic trade against single leg)                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 44    | “r” (Stock options auction against single leg)                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| 45    | “s” (Stock options floor trade against single leg)                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| 46    | “t” (Multi leg floor trade of proprietary products)                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| 47    | “u” (Multilateral Compression Trade of  Proprietary Data Products)Transaction represents an execution in a proprietary product done as part of a multilateral compression. Trades are  executed outside of regular trading hours at prices derived from end of day markets. Trades do not update Open,  High, Low, and Closing Prices, but will update total volume.                                                                                                                                                       |
| 48    | “v” (Extended Hours Trade )Transaction represents a trade that was executed outside of regular market hours. Trades do not update Open,  High, Low, and Closing Prices but will update total volume.                                                                                                                                                                                                                                                                                                                       |


### Quote Message

```python
class Quote:
    def __init__(self, contract: str, ask_price: float, ask_size: int, bid_price: float, bid_size: int, timestamp: float):
        self.contract: str = contract
        self.ask_price: float = ask_price
        self.bid_price: float = bid_price
        self.ask_size: int = ask_size
        self.bid_size: int = bid_size
        self.timestamp: float = timestamp
```

* **contract** - Identifier for the options contract.  This includes the ticker symbol, put/call, expiry, and strike price.
* **ask_price** - the ask price in USD
* **ask_size** - the size of the last ask in hundreds (each contract is for 100 shares).
* **bid_price** - the bid price in USD
* **bid_size** - the size of the last bid in hundreds (each contract is for 100 shares).
* **timestamp** - a Unix timestamp (with microsecond precision)


### Refresh Message

```python
class Refresh:
    def __init__(self, contract: str, open_interest: int, open_price: float, close_price: float, high_price: float, low_price: float):
        self.contract: str = contract
        self.open_interest: int = open_interest
        self.open_price: float = open_price
        self.close_price: float = close_price
        self.high_price: float = high_price
        self.low_price: float = low_price
```

* **contract** - Identifier for the options contract.  This includes the ticker symbol, put/call, expiry, and strike price.
* **openInterest** - the total quantity of opened contracts as reported at the start of the trading day
* **open_price** - the open price in USD
* **close_price** - the close price in USD
* **high_price** - the daily high price in USD
* **low_price** - the daily low price in USD

### Unusual Activity Message
```python
class UnusualActivity:
    def __init__(self,
                 contract: str,
                 activity_type: UnusualActivityType,
                 sentiment: UnusualActivitySentiment,
                 total_value: float,
                 total_size: int,
                 average_price: float,
                 ask_price_at_execution: float,
                 bid_price_at_execution: float,
                 underlying_price_at_execution: float,
                 timestamp: float):
        self.contract: str = contract
        self.activity_type: UnusualActivityType = activity_type
        self.sentiment: UnusualActivitySentiment = sentiment
        self.total_value: float = total_value
        self.total_size: int = total_size
        self.average_price: float = average_price
        self.ask_price_at_execution: float = ask_price_at_execution
        self.bid_price_at_execution: float = bid_price_at_execution
        self.underlying_price_at_execution: float = underlying_price_at_execution
        self.timestamp: float = timestamp
```

* **contract** - Identifier for the options contract.  This includes the ticker symbol, put/call, expiry, and strike price.
* **activity_type** - The type of unusual activity that was detected
  * **`Block`** - represents an 'block' trade
  * **`Sweep`** - represents an intermarket sweep
  * **`Large`** - represents a trade of at least $100,000
  * **`UnusualSweep`** - represents an unusually large sweep near market open
* **sentiment** - The sentiment of the unusual activity event
  *    **`Neutral`** - Reflects a minimal expected price change
  *    **`Bullish`** - Reflects an expected positive (upward) change in price
  *    **`Bearish`** - Reflects an expected negative (downward) change in price
* **total_value** - The total value of the trade in USD. 'Sweeps' and 'blocks' can be comprised of multiple trades. This is the value of the entire event.
* **total_size** - The total size of the trade in number of contracts. 'Sweeps' and 'blocks' can be comprised of multiple trades. This is the total number of contracts exchanged during the event.
* **average_price** - The average price at which the trade was executed. 'Sweeps' and 'blocks' can be comprised of multiple trades. This is the average trade price for the entire event.
* **ask_price_at_execution** - The 'ask' price of the underlying at execution of the trade event.
* **bid_price_at_execution** - The 'bid' price of the underlying at execution of the trade event.
* **underlying_price_at_execution** - The last trade price of the underlying at execution of the trade event.
* **Timestamp** - a Unix timestamp (with microsecond precision).

## API Keys
You will receive your Intrinio API Key after [creating an account](https://intrinio.com/signup). You will need a subscription to a [realtime data feed](https://intrinio.com/real-time-multi-exchange) as well.

## Documentation

### Methods

`client = IntrinioRealtimeEquitiesClient(configuration)` - Creates an Intrinio Realtime client
* **Parameter** `configuration.api_key`: Your Intrinio API Key
* **Parameter** `configuration.provider`: The real-time data provider to use ("REALTIME" or "DELAYED_SIP" or "NASDAQ_BASIC")
* **Parameter** `configuration.on_quote(quote, backlog)`: A function that handles received quotes. `backlog` is an integer representing the approximate size of the queue of unhandled quote/trade events.
* **Parameter** `configuration.on_trade(quote, backlog)`: A function that handles received trades. `backlog` is an integer representing the approximate size of the queue of unhandled quote/trade events.
* **Parameter** `configuration.logger`: (optional) A Python Logger instance to use for logging

`client : IntrinioRealtimeOptionsClient = IntrinioRealtimeOptionsClient(config : Config, on_trade : Callable[[Trade], None], on_quote : Callable[[Quote], None] = None, on_refresh : Callable[[Refresh], None] = None, on_unusual_activity : Callable[[UnusualActivity],None] = None)` - Creates an Intrinio Real-Time client.
* **Parameter** `config`: The configuration to be used by the client.
* **Parameter** `on_trade`: The Callable accepting trades. If no `on_trade` callback is provided, you will not receive trade updates from the server.
* **Parameter** `on_quote`: The Callable accepting quotes. If no `on_quote` callback is provided, you will not receive quote (ask, bid) updates from the server.
* **Parameter** `on_refresh`: The Callable accepting refresh messages. If no `on_refresh` callback is provided, you will not receive open interest, high, low, open, or close data from the server. Note: open interest data is only updated at the beginning of every trading day. If this callback is provided you will recieve an update immediately, as well as every 15 minutes (approx).
* **Parameter** `on_unusual_activity`: The Callable accepting unusual activity events. If no `on_unusual_activity` callback is provided, you will not receive unusual activity updates from the server.

#### Equities:
```python
def on_quote(quote, backlog):
    print("QUOTE: " , quote, "BACKLOG LENGTH: ", backlog)
def on_trade(trade, backlog):
    print("TRADE: " , trade, "BACKLOG LENGTH: ", backlog)
    
configuration = {
    'api_key': '',
    'provider': 'IEX',  # REALTIME (IEX) or IEX or CBOE_ONE or DELAYED_SIP or NASDAQ_BASIC
    #'delayed': True, # Add this if you have realtime (nondelayed) access and want to force delayed mode. If you only have delayed mode access, this is redundant.
    'on_quote': on_quote,
    'on_trade': on_trade
}

client = IntrinioRealtimeEquitiesClient(configuration)
```

#### Options:
```python
class Config:
    def __init__(self, apiKey : str, provider : Providers, numThreads : int = 4, logLevel : LogLevel = LogLevel.INFO, manualIpAddress : str = None, symbols : set[str] = None):
        self.apiKey : str = apiKey
        self.provider : Providers = provider # Providers.OPRA or Providers.MANUAL
        self.numThreads : int = numThreads # At least 4 threads are recommended for 'FIREHOSE' connections
        self.manualIpAddress : str = manualIpAddress
        self.symbols : list[str] = symbols # Static list of symbols to use
        self.logLevel : LogLevel = logLevel
```

---------

`client.join(channels)` - Joins the given channels. This can be called at any time. The client will automatically register joined channels and establish the proper subscriptions with the WebSocket connection.
* **Parameter** `channels` - A single channel or list of channels. You can also use the special symbol, "lobby" to join the firehose channel and recieved updates for all ticker symbols (you must have a valid "firehose" subscription).
```python
client.join(["AAPL", "MSFT", "GE"])
client.join("GOOG")
client.join("lobby")
```
---------

Equities - `client.connect()` - Retrieves an auth token, opens the WebSocket connection, starts the self-healing and heartbeat intervals, joins requested channels.
Options - `client.start()`

---------

Equities - `client.disconnect()` - Closes the WebSocket, stops the self-healing and heartbeat intervals. You _must_ call this to dispose of the client.
Options - `client.stop()`

---------

`client.on_quote(quote, backlog)` - Changes the quote handler function
```python
def on_quote(quote, backlog):
    print("QUOTE: " , quote, "BACKLOG LENGTH: ", backlog)
    
client.on_quote = on_quote
```

---------

`client.leave(channels)` - Leaves the given channels.
* **Parameter** `channels` - A single channel or list of channels
```python
client.leave(["AAPL", "MSFT", "GE"])
client.leave("GOOG")
```

---------

`client.leave_all()` - Leaves all channels.

---------
## Example Equities Replay Client Usage
```python
def on_quote(quote, backlog):
    print("QUOTE: " , quote, "BACKLOG LENGTH: ", backlog)
def on_trade(trade, backlog):
    print("TRADE: " , trade, "BACKLOG LENGTH: ", backlog)
    
options = {
    'api_key': '',
    'provider': 'IEX',  # REALTIME (IEX) or IEX or CBOE_ONE or DELAYED_SIP or NASDAQ_BASIC
    'replay_date': datetime.date.today(),
    'with_simulated_delay': False,  # This plays back the events at the same rate they happened in market.
    'delete_file_when_done': True,
    'write_to_csv': False,  # needed for ReplayClient
    'csv_file_path': 'data.csv'  # needed for ReplayClient
}

client = IntrinioReplayClient(options, on_trade, on_quote)
```

### Minimum Hardware Requirements - Trades only
Equities Client:
* Non-lobby mode: 1 hardware core and 1 thread in your configuration for roughly every 100 symbols, up to the lobby mode settings. Absolute minimum 2 cores and threads.
* Lobby mode: 4 hardware cores and 4 threads in your configuration
* 5 Mbps connection
* 0.5 ms latency

Options Client:
* Non-lobby mode: 1 hardware core and 1 thread in your configuration for roughly every 250 contracts, up to the lobby mode settings.  3 cores and 3 configured threads for each chain, up to the lobby mode settings. Absolute minimum 3 cores and threads.
* Lobby mode: 6 hardware cores and 6 threads in your configuration
* 25 Mbps connection
* 0.5 ms latency

### Minimum Hardware Requirements - Trades and Quotes
Equities Client:
* Non-lobby mode: 1 hardware core and 1 thread in your configuration for roughly every 25 symbols, up to the lobby mode settings. Absolute minimum 4 cores and threads.
* Lobby mode: 8 hardware cores and 8 threads in your configuration
* 25 Mbps connection
* 0.5 ms latency

Options Client:
* Non-lobby mode: 1 hardware core and 1 thread in your configuration for roughly every 100 contracts, up to the lobby mode settings.  4 cores and 4 configured threads for each chain, up to the lobby mode settings. Absolute minimum 4 cores and threads.
* Lobby mode: 12 hardware cores and 12 threads in your configuration
* 100 Mbps connection
* 0.5 ms latency