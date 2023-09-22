# intrinio realtime python sdk
SDK for working with Intrinio's realtime Multi-Exchange prices feed.  Intrinio’s Multi-Exchange feed bridges the gap by merging real-time equity pricing from the IEX and MEMX exchanges. Get a comprehensive view with increased market volume and enjoy no exchange fees, no per-user requirements, no permissions or authorizations, and little to no paperwork.

[Intrinio](https://intrinio.com/) provides real-time stock prices via a two-way WebSocket connection. To get started, [subscribe to a real-time data feed](https://intrinio.com/real-time-multi-exchange) and follow the instructions below.

[Documentation for our legacy realtime client](https://github.com/intrinio/intrinio-realtime-python-sdk/tree/2.2.0)

## Requirements

- Python 3.10
- You need https://pypi.org/project/websocket-client/, not https://pypi.org/project/websocket/.

## Docker
Add your API key to the example_app.py file, then
```
docker compose build
docker compose run client
```

## Features

* Receive streaming, real-time price quotes (last trade, bid, ask)
* Subscribe to updates from individual securities
* Subscribe to updates for all securities
* Multiple sources of data - REALTIME or DELAYED_SIP or NASDAQ_BASIC

## Installation
```
pip install intriniorealtime
```

## Example Usage
```python
import threading
import time
from threading import Timer,Thread,Event
from intriniorealtime.client import IntrinioRealtimeClient

trade_count = 0
ask_count = 0
bid_count = 0
backlog_count = 0

def on_quote(quote, backlog):
        global ask_count
        global bid_count
        global backlog_count
        backlog_count = backlog
        if 'type' in quote.__dict__:
            if quote.type == "ask": ask_count += 1
            else: bid_count += 1

def on_trade(trade, backlog): 
        global trade_count
        global backlog_count
        backlog_count = backlog
        trade_count += 1

class Summarize(threading.Thread):
    def __init__(self, event):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.stopped = event

    def run(self):
        global trade_count
        global bid_count
        global ask_count
        global backlog_count
        while not self.stopped.wait(5):
            print("trades: " + str(trade_count) + "; asks: " + str(ask_count) + "; bids: " + str(bid_count) + "; backlog: " + str(backlog_count))

options = {
    'api_key': 'API_KEY_HERE',
    'provider': 'REALTIME' # REALTIME or DELAYED_SIP or NASDAQ_BASIC
}

client = IntrinioRealtimeClient(options, on_trade, on_quote)
client.join(['AAPL','GE','MSFT'])
#client.join(['lobby'])
client.connect()
stopFlag = Event()
summarize_thread = Summarize(stopFlag)
summarize_thread.start()
time.sleep(10)
client.disconnect()
# this will stop the summarize thread
stopFlag.set()
```

## Handling Quotes and the Queue

There are thousands of securities, each with their own feed of activity.  We highly encourage you to make your trade and quote handlers has short as possible and follow a queue pattern so your app can handle the volume of activity.

## Quote Data Format

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


## API Keys
You will receive your Intrinio API Key after [creating an account](https://intrinio.com/signup). You will need a subscription to a [realtime data feed](https://intrinio.com/real-time-multi-exchange) as well.

## Documentation

### Methods

`client = IntrinioRealtimeClient(options)` - Creates an Intrinio Realtime client
* **Parameter** `options.api_key`: Your Intrinio API Key
* **Parameter** `options.provider`: The real-time data provider to use ("REALTIME" or "DELAYED_SIP" or "NASDAQ_BASIC")
* **Parameter** `options.on_quote(quote, backlog)`: A function that handles received quotes. `backlog` is an integer representing the approximate size of the queue of unhandled quote/trade events.
* **Parameter** `options.on_trade(quote, backlog)`: A function that handles received trades. `backlog` is an integer representing the approximate size of the queue of unhandled quote/trade events.
* **Parameter** `options.logger`: (optional) A Python Logger instance to use for logging

```python
def on_quote(quote, backlog):
    print("QUOTE: " , quote, "BACKLOG LENGTH: ", backlog)
def on_trade(trade, backlog):
    print("TRADE: " , trade, "BACKLOG LENGTH: ", backlog)
    
options = {
    'api_key': '',
    'provider': 'REALTIME', # REALTIME or DELAYED_SIP or NASDAQ_BASIC
    'on_quote': on_quote,
    'on_trade': on_trade
}

client = IntrinioRealtimeClient(options)
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

`client.connect()` - Retrieves an auth token, opens the WebSocket connection, starts the self-healing and heartbeat intervals, joins requested channels.

---------

`client.keep_alive()` - Runs an infinite loop to keep the thread alive, so that the client continues to receive prices. You may call this function after `connect()` or use your own timing logic (for example: connect, listen for quotes for x minutes, disconnect).

---------

`client.disconnect()` - Closes the WebSocket, stops the self-healing and heartbeat intervals. You _must_ call this to dispose of the client.

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
## Example Replay Client Usage
```python
def on_quote(quote, backlog):
    print("QUOTE: " , quote, "BACKLOG LENGTH: ", backlog)
def on_trade(trade, backlog):
    print("TRADE: " , trade, "BACKLOG LENGTH: ", backlog)
    
options = {
    'api_key': '',
    'provider': 'REALTIME', # REALTIME or DELAYED_SIP or NASDAQ_BASIC
    'replay_date': datetime.date.today(),
    'with_simulated_delay': False,  # This plays back the events at the same rate they happened in market.
    'delete_file_when_done': True
}

client = IntrinioReplayClient(options, on_trade, on_quote)
```
