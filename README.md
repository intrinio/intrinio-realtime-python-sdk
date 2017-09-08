# Intrinio Python SDK for Real-Time Stock Prices

[Intrinio](https://intrinio.com/) provides real-time stock prices via a two-way WebSocket connection. To get started, [subscribe to a real-time data feed](https://intrinio.com/marketplace/data/prices/realtime) and follow the instructions below.

## Requirements

- Python 2 or 3

## Features

* Receive streaming, real-time price quotes (last trade, bid, ask)
* Subscribe to updates from individual securities
* Subscribe to updates for all securities (contact us for special access)

## Installation
```
pip install intriniorealtime
```

## Example Usage
```python
from intriniorealtime.client import IntrinioRealtimeClient

def on_quote(quote, backlog):
    print("QUOTE: " , quote, "BACKLOG LENGTH: ", backlog)
    
options = {
    'username': 'YOUR_INTRINIO_API_USERNAME',
    'password': 'YOUR_INTRINIO_API_PASSWORD',
    'provider': 'iex',
    'on_quote': on_quote
}

client = IntrinioRealtimeClient(options)
client.join(['AAPL','GE','MSFT'])
client.connect()
client.keep_alive()
```

## Handling Quotes and the Queue

When the Intrinio Realtime library receives quotes from the websocket connection, it places them in a queue. This queue has a default maximum size of 10,000 quotes. You can modify this value by specifying a `max_queue_size` option, as your environment memory contraints allow. Once a quote has been placed in the queue, it is passed to the `on_quote` handler in the order it was received. Your `on_quote` handler should process quotes quickly, so that the queue does not reach its maximum size (at which point, the system will log an error and ignore any incoming quotes until the queue has space). We recommend using the a thread class or pool for operations such as writing quotes to a database (or anything else involving time-consuming I/O). The `on_quote` handler also receives a `backlog` argument, which is an integer specifying the approximate length of the quote queue. Monitor this to make sure you are processing quotes quickly enough.

## Providers

Currently, Intrinio offers realtime data for this SDK from the following providers:

* IEX - [Homepage](https://iextrading.com/)

Each has distinct price channels and quote formats, but a very similar API.

## Quote Data Format

Each data provider has a different format for their quote data.

### IEX

```python
{ 'type': 'ask',
  'timestamp': 1493409509.3932788,
  'ticker': 'GE',
  'size': 13750,
  'price': 28.97 }
```

*   **type** - the quote type
  *    **`last`** - represents the last traded price
  *    **`bid`** - represents the top-of-book bid price
  *    **`ask`** - represents the top-of-book ask price
*   **timestamp** - a Unix timestamp (with microsecond precision)
*   **ticker** - the ticker of the security
*   **size** - the size of the `last` trade, or total volume of orders at the top-of-book `bid` or `ask` price
*   **price** - the price in USD

## Channels

### IEX

To receive price quotes from IEX, you need to instruct the client to "join" a channel. A channel can be
* A security ticker (`AAPL`, `MSFT`, `GE`, etc)
* The security lobby (`$lobby`) where all price quotes for all securities are posted
* The security last price lobby (`$lobby_last_price`) where only last price quotes for all securities are posted

Special access is required for both lobby channeles. [Contact us](mailto:sales@intrinio.com) for more information.

## API Keys
You will receive your Intrinio API Username and Password after [creating an account](https://intrinio.com/signup). You will need a subscription to a [realtime data feed](https://intrinio.com/marketplace/data/prices/realtime) as well.

## Documentation

### Methods

`client = IntrinioRealtimeClient(options)` - Creates an Intrinio Realtime client
* **Parameter** `options.username`: Your Intrinio API Username
* **Parameter** `options.password`: Your Intrinio API Password
* **Parameter** `options.provider`: The real-time data provider to use (for now "iex" only)
* **Parameter** `options.on_quote(quote, backlog)`: (optional) A function that handles received quotes. `backlog` is an integer representing the approximate size of the queue of unhandled quotes
* **Parameter** `options.channels`: (optional) An array of channels to join after connecting
* **Parameter** `options.logger`: (optional) A Python Logger instance to use for logging

```python
def on_quote(quote, backlog):
    print("QUOTE: " , quote, "BACKLOG LENGTH: ", backlog)
    
options = {
    'username': 'YOUR_INTRINIO_API_USERNAME',
    'password': 'YOUR_INTRINIO_API_PASSWORD',
    'provider': 'iex',
    'on_quote': on_quote
}

client = IntrinioRealtimeClient(options)
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

`client.join(channels)` - Joins the given channels. This can be called at any time. The client will automatically register joined channels and establish the proper subscriptions with the WebSocket connection.
* **Parameter** `channels` - A single channel or list of channels
```python
client.join(["AAPL", "MSFT", "GE"])
client.join("GOOG")
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
