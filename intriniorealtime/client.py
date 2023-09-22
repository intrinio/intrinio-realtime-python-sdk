import time
import requests
import threading
import websocket
import logging
import queue
import struct
import sys
import wsaccel

SELF_HEAL_BACKOFFS = [10, 30, 60, 300, 600]
REALTIME = "REALTIME"
DELAYED_SIP = "DELAYED_SIP"
NASDAQ_BASIC = "NASDAQ_BASIC"
MANUAL = "MANUAL"
PROVIDERS = [REALTIME, MANUAL, DELAYED_SIP, NASDAQ_BASIC]
NO_SUBPROVIDER = "NO_SUBPROVIDER"
CTA_A = "CTA_A"
CTA_B = "CTA_B"
UTP = "UTP"
OTC = "OTC"
NASDAQ_BASIC = "NASDAQ_BASIC"
IEX = "IEX"
SUB_PROVIDERS = [NO_SUBPROVIDER, CTA_A, CTA_B, UTP, OTC, NASDAQ_BASIC, IEX]
MAX_QUEUE_SIZE = 10000
DEBUGGING = not (sys.gettrace() is None)
HEADER_MESSAGE_FORMAT_KEY = "UseNewEquitiesFormat"
HEADER_MESSAGE_FORMAT_VALUE = "v2"
HEADER_CLIENT_INFORMATION_KEY = "Client-Information"
HEADER_CLIENT_INFORMATION_VALUE = "IntrinioPythonSDKv5.1.0"


class Quote:
    def __init__(self, symbol, type, price, size, timestamp, subprovider, market_center, condition):
        self.symbol = symbol
        self.type = type
        self.price = price
        self.size = size
        self.timestamp = timestamp
        self.subprovider = subprovider
        self.market_center = market_center
        self.condition = condition

    def __str__(self):
        return self.symbol + ", " + self.type + ", price: " + str(self.price) + ", size: " + str(self.size) + ", timestamp: " + str(self.timestamp) + ", subprovider: " + str(self.subprovider) + ", market_center: " + str(self.market_center) + ", condition: " + str(self.condition)


class Trade:
    def __init__(self, symbol, price, size, total_volume, timestamp, subprovider, market_center, condition):
        self.symbol = symbol
        self.price = price
        self.size = size
        self.total_volume = total_volume
        self.timestamp = timestamp
        self.subprovider = subprovider
        self.market_center = market_center
        self.condition = condition

    def __str__(self):
        return self.symbol + ", trade, price: " + str(self.price) + ", size: " + str(self.size) + ", timestamp: " + str(self.timestamp) + ", subprovider: " + str(self.subprovider) + ", market_center: " + str(self.market_center) + ", condition: " + str(self.condition)


class IntrinioRealtimeClient:
    def __init__(self, options, on_trade, on_quote):
        if options is None:
            raise ValueError("Options parameter is required")

        self.options = options
        self.api_key = options.get('api_key')
        self.username = options.get('username')
        self.password = options.get('password')
        self.provider = options.get('provider')
        self.ipaddress = options.get('ipaddress')
        self.tradesonly = options.get('tradesonly')

        if 'channels' in options:
            self.channels = set(options['channels'])
        else:
            self.channels = set()

        if 'logger' in options:
            self.logger = options['logger']
        else:
            log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            log_handler = logging.StreamHandler()
            log_handler.setFormatter(log_formatter)
            self.logger = logging.getLogger('intrinio_realtime')
            if 'debug' in options and options['debug'] == True:
                self.logger.setLevel(logging.DEBUG)
            else:
                self.logger.setLevel(logging.INFO)
            self.logger.addHandler(log_handler)

        if 'max_queue_size' in options:
            self.quotes = queue.Queue(maxsize=options['max_queue_size'])
        else:
            self.quotes = queue.Queue(maxsize=MAX_QUEUE_SIZE)

        if self.api_key:
            if not self.valid_api_key(self.api_key):
                raise ValueError("API Key was formatted invalidly")
        else:
            if not self.username and not self.password:
                raise ValueError("API key or username and password are required")

            if not self.username:
                raise ValueError("Parameter 'username' must be specified")

            if not self.password:
                raise ValueError("Parameter 'password' must be specified")

        if not callable(on_quote):
            self.on_quote = None
            raise ValueError("Parameter 'on_quote' must be a function")
        else:
            self.on_quote = on_quote

        if not callable(on_trade):
            self.on_trade = None
            raise ValueError("Parameter 'on_trade' must be a function")
        else:
            self.on_trade = on_trade

        if self.provider not in PROVIDERS:
            raise ValueError(f"Parameter 'provider' is invalid, use one of {PROVIDERS}")

        self.ready = False
        self.token = None
        self.ws = None
        self.quote_receiver = None
        self.quote_handler = QuoteHandler(self)
        self.joined_channels = set()
        self.last_queue_warning_time = 0
        self.last_self_heal_backoff = -1
        self.quote_handler.start()

    def auth_url(self):
        auth_url = ""

        if self.provider == REALTIME:
            auth_url = "https://realtime-mx.intrinio.com/auth"
        elif self.provider == DELAYED_SIP:
            auth_url = "https://realtime-delayed-sip.intrinio.com/auth"
        elif self.provider == NASDAQ_BASIC:
            auth_url = "https://realtime-nasdaq-basic.intrinio.com/auth"
        elif self.provider == MANUAL:
            auth_url = "http://" + self.ipaddress + "/auth"

        if self.api_key:
            auth_url = self.api_auth_url(auth_url)

        return auth_url

    def api_auth_url(self, auth_url):
        if "?" in auth_url:
            auth_url = auth_url + "&"
        else:
            auth_url = auth_url + "?"

        return auth_url + "api_key=" + self.api_key

    def websocket_url(self):
        if self.provider == REALTIME:
            return "wss://realtime-mx.intrinio.com/socket/websocket?vsn=1.0.0&token=" + self.token
        elif self.provider == DELAYED_SIP:
            return "wss://realtime-delayed-sip.intrinio.com/socket/websocket?vsn=1.0.0&token=" + self.token
        elif self.provider == NASDAQ_BASIC:
            return "wss://realtime-nasdaq-basic.intrinio.com/socket/websocket?vsn=1.0.0&token=" + self.token
        elif self.provider == MANUAL:
            return "ws://" + self.ipaddress + "/socket/websocket?vsn=1.0.0&token=" + self.token

    def do_backoff(self):
        self.last_self_heal_backoff += 1
        i = min(self.last_self_heal_backoff, len(SELF_HEAL_BACKOFFS) - 1)
        backoff = SELF_HEAL_BACKOFFS[i]
        time.sleep(backoff)

    def connect(self):
        connected = False
        while not connected:
            try:
                self.logger.info("Connecting...")
                self.ready = False
                self.joined_channels = set()

                if self.ws:
                    self.ws.close()
                    time.sleep(3)

                self.refresh_token()
                self.refresh_websocket()
                connected = True
            except Exception as e:
                self.logger.error(f"Cannot connect: {repr(e)}")
                self.do_backoff()

    def disconnect(self):
        self.ready = False
        self.joined_channels = set()

        if self.ws:
            self.ws.close()
            time.sleep(1)

    def refresh_token(self):
        headers = {HEADER_CLIENT_INFORMATION_KEY: HEADER_CLIENT_INFORMATION_VALUE}
        if self.api_key:
            response = requests.get(self.auth_url(), headers=headers)
        else:
            response = requests.get(self.auth_url(), auth=(self.username, self.password), headers=headers)

        if response.status_code != 200:
            raise RuntimeError("Auth failed")

        self.token = response.text
        self.logger.info("Authentication successful!")

    def refresh_websocket(self):
        self.quote_receiver = QuoteReceiver(self)
        self.quote_receiver.start()

    def on_connect(self):
        self.ready = True
        self.last_self_heal_backoff = -1
        self.refresh_channels()

    def on_queue_full(self):
        if time.time() - self.last_queue_warning_time > 1:
            self.logger.error("Quote queue is full! Dropped some new quotes")
            self.last_queue_warning_time = time.time()

    def join(self, channels):
        if isinstance(channels, str):
            channels = [channels]

        self.channels = self.channels | set(channels)
        self.refresh_channels()

    def leave(self, channels):
        if isinstance(channels, str):
            channels = [channels]

        self.channels = self.channels - set(channels)
        self.refresh_channels()

    def leave_all(self):
        self.channels = set()
        self.refresh_channels()

    def refresh_channels(self):
        if self.ready != True:
            return

        # Join new channels
        new_channels = self.channels - self.joined_channels
        self.logger.debug(f"New channels: {new_channels}")
        for channel in new_channels:
            msg = self.join_binary_message(channel)
            self.ws.send(msg, websocket.ABNF.OPCODE_BINARY)
            self.logger.info(f"Joined channel {channel}")

        # Leave old channels
        old_channels = self.joined_channels - self.channels
        self.logger.debug(f"Old channels: {old_channels}")
        for channel in old_channels:
            msg = self.leave_binary_message(channel)
            self.ws.send(msg, websocket.ABNF.OPCODE_BINARY)
            self.logger.info(f"Left channel {channel}")

        self.joined_channels = self.channels.copy()
        self.logger.debug(f"Current channels: {self.joined_channels}")

    def join_binary_message(self, channel):
        if channel == "lobby":
            message = bytearray([74, 1 if self.tradesonly else 0])
            channel_bytes = bytes("$FIREHOSE", 'ascii')
            message.extend(channel_bytes)
            return message
        else:
            message = bytearray([74, 1 if self.tradesonly else 0])
            channel_bytes = bytes(channel, 'ascii')
            message.extend(channel_bytes)
            return message

    def leave_binary_message(self, channel):
        if channel == "lobby":
            message = bytearray([76])
            channel_bytes = bytes("$FIREHOSE", 'ascii')
            message.extend(channel_bytes)
            return message
        else:
            message = bytearray([76])
            channel_bytes = bytes(channel, 'ascii')
            message.extend(channel_bytes)
            return message

    def valid_api_key(self, api_key):
        if not isinstance(api_key, str):
            return False

        if api_key == "":
            return False

        return True


class QuoteReceiver(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.client = client
        self.enabled = True

    def run(self):
        self.client.ws = websocket.WebSocketApp(
            self.client.websocket_url(),
            header={HEADER_MESSAGE_FORMAT_KEY: HEADER_MESSAGE_FORMAT_VALUE, HEADER_CLIENT_INFORMATION_KEY: HEADER_CLIENT_INFORMATION_VALUE},
            on_open=self.on_open,
            on_close=self.on_close,
            on_message=self.on_message,
            on_error=self.on_error
        )

        self.client.logger.debug("QuoteReceiver ready")
        self.client.ws.run_forever(skip_utf8_validation=True)  # skip_utf8_validation for more performance
        self.client.logger.debug("QuoteReceiver exiting")

    def on_open(self, ws):
        self.client.logger.info("Websocket opened!")
        self.client.on_connect()

    def on_close(self, ws, code, message):
        self.client.logger.info("Websocket closed!")

    def on_error(self, ws, error, *args):
        try:
            self.client.logger.error(f"Websocket ERROR: {error}")
            self.client.connect()
        except Exception as e:
            self.client.logger.error(f"Error in on_error handler: {repr(e)}; {repr(error)}")
            raise e

    def on_message(self, ws, message):
        try:
            if DEBUGGING:  # This is here for performance reasons so we don't use slow reflection on every message.
                if isinstance(message, str):
                    self.client.logger.debug(f"Received message (hex): {message.encode('utf-8').hex()}")
                else:
                    if isinstance(message, bytes):
                        self.client.logger.debug(f"Received message (hex): {message.hex()}")
            self.client.quotes.put_nowait(message)
        except queue.Full:
            self.client.on_queue_full()
        except Exception as e:
            hex_message = ""
            if isinstance(message, str):
                hex_message = message.encode('utf-8').hex()
            else:
                if isinstance(message, bytes):
                    hex_message = message.hex()
            self.client.logger.error(f"Websocket on_message ERROR. Message as hex: {hex_message}; error: {repr(e)}")
            raise e


class QuoteHandler(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.client = client

    def parse_quote(self, quote_bytes, start_index):
        buffer = memoryview(quote_bytes)
        symbol_length = quote_bytes[start_index + 2]
        condition_length = quote_bytes[start_index + 22 + symbol_length]
        symbol = quote_bytes[(start_index + 3):(start_index + 3 + symbol_length)].decode("ascii")
        quote_type = "ask" if quote_bytes[start_index] == 1 else "bid"
        price = struct.unpack_from('<f', buffer, start_index + 6 + symbol_length)[0]
        size = struct.unpack_from('<L', buffer, start_index + 10 + symbol_length)[0]
        timestamp = struct.unpack_from('<Q', buffer, start_index + 14 + symbol_length)[0]

        subprovider = None
        match quote_bytes[3 + symbol_length + start_index]:
            case 0:
                subprovider = NO_SUBPROVIDER
            case 1:
                subprovider = CTA_A
            case 2:
                subprovider = CTA_B
            case 3:
                subprovider = UTP
            case 4:
                subprovider = OTC
            case 5:
                subprovider = NASDAQ_BASIC
            case 6:
                subprovider = IEX
            case _:
                subprovider = IEX

        market_center = quote_bytes[(start_index + 4 + symbol_length):(start_index + 6 + symbol_length)].decode("utf-16")

        condition = ""
        if condition_length > 0:
            condition = quote_bytes[(start_index + 23 + symbol_length):(start_index + 23 + symbol_length + condition_length)].decode("ascii")

        return Quote(symbol, quote_type, price, size, timestamp, subprovider, market_center, condition)

    def parse_trade(self, trade_bytes, start_index):
        buffer = memoryview(trade_bytes)
        symbol_length = trade_bytes[start_index + 2]
        condition_length = trade_bytes[start_index + 26 + symbol_length]
        symbol = trade_bytes[(start_index + 3):(start_index + 3 + symbol_length)].decode("ascii")
        price = struct.unpack_from('<f', buffer, start_index + 6 + symbol_length)[0]
        size = struct.unpack_from('<L', buffer, start_index + 10 + symbol_length)[0]
        timestamp = struct.unpack_from('<Q', buffer, start_index + 14 + symbol_length)[0]
        total_volume = struct.unpack_from('<L', buffer, start_index + 22 + symbol_length)[0]

        subprovider = None
        match trade_bytes[3 + symbol_length + start_index]:
            case 0:
                subprovider = NO_SUBPROVIDER
            case 1:
                subprovider = CTA_A
            case 2:
                subprovider = CTA_B
            case 3:
                subprovider = UTP
            case 4:
                subprovider = OTC
            case 5:
                subprovider = NASDAQ_BASIC
            case 6:
                subprovider = IEX
            case _:
                subprovider = IEX

        market_center = trade_bytes[(start_index + 4 + symbol_length):(start_index + 6 + symbol_length)].decode("utf-16")

        condition = ""
        if condition_length > 0:
            condition = trade_bytes[(start_index + 27 + symbol_length):(start_index + 27 + symbol_length + condition_length)].decode("ascii")

        return Trade(symbol, price, size, total_volume, timestamp, subprovider, market_center, condition)

    def parse_message(self, bytes, start_index, backlog_len):
        message_type = bytes[start_index]
        message_length = bytes[start_index + 1]
        new_start_index = start_index + message_length
        item = None
        if message_type == 0:  # this is a trade
            item = self.parse_trade(bytes, start_index)
            if callable(self.client.on_trade):
                try:
                    self.client.on_trade(item, backlog_len)
                except Exception as e:
                    self.client.logger.error(repr(e))
        else:  # message_type is ask or bid (quote)
            item = self.parse_quote(bytes, start_index)
            if callable(self.client.on_quote):
                try:
                    self.client.on_quote(item, backlog_len)
                except Exception as e:
                    self.client.logger.error(repr(e))
        return new_start_index

    def run(self):
        self.client.logger.debug("QuoteHandler ready")
        while True:
            message = self.client.quotes.get()
            backlog_len = self.client.quotes.qsize()
            items_in_message = message[0]
            start_index = 1
            for i in range(0, items_in_message):
                start_index = self.parse_message(message, start_index, backlog_len)
