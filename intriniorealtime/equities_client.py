import time
import requests
import threading
import websocket
import logging
import queue
import struct
import sys
import wsaccel
from enum import IntEnum, unique
from typing import Optional, Dict, Any

SELF_HEAL_BACKOFFS = [10, 30, 60, 300, 600]
_EMPTY_STRING = ""
_NAN = float("NAN")
REALTIME = "REALTIME"
DELAYED_SIP = "DELAYED_SIP"
NASDAQ_BASIC = "NASDAQ_BASIC"
MANUAL = "MANUAL"
NO_PROVIDER = "NO_PROVIDER"
NO_SUBPROVIDER = "NO_SUBPROVIDER"
CTA_A = "CTA_A"
CTA_B = "CTA_B"
UTP = "UTP"
OTC = "OTC"
IEX = "IEX"
CBOE_ONE = "CBOE_ONE"
PROVIDERS = [REALTIME, MANUAL, DELAYED_SIP, NASDAQ_BASIC, IEX, CBOE_ONE]
SUB_PROVIDERS = [NO_SUBPROVIDER, CTA_A, CTA_B, UTP, OTC, NASDAQ_BASIC, IEX, CBOE_ONE]
MAX_QUEUE_SIZE = 250000
DEBUGGING = not (sys.gettrace() is None)
HEADER_MESSAGE_FORMAT_KEY = "UseNewEquitiesFormat"
HEADER_MESSAGE_FORMAT_VALUE = "v2"
HEADER_CLIENT_INFORMATION_KEY = "Client-Information"
HEADER_CLIENT_INFORMATION_VALUE = "IntrinioPythonSDKv6.0.3"


class EquitiesQuote:
    def __init__(self, symbol, type, price, size, timestamp, subprovider, market_center, condition):
        self.symbol = symbol
        self.type = type
        self.price = price
        self.size = size
        self.timestamp = timestamp
        self.subprovider = subprovider
        self.market_center = market_center
        self.condition = condition

    @staticmethod
    def json_keys():
        return ["symbol","type","price","size","timestamp","subprovider","market_center","condition"]

    @classmethod
    def to_json_array(self):
        return f'["{self.symbol}","{self.type}",{self.price},{self.size},{self.timestamp},"{self.subprovider}","{self.market_center}","{self.condition}"]'

    @classmethod
    def to_json(self):
        return f'{{"symbol":"{self.symbol}","type":"{self.type}","price":{self.price},"size":{self.size},"timestamp":{self.timestamp},"subprovider":"{self.subprovider}","market_center":"{self.market_center}","condition":"{self.condition}"}}'

    @classmethod
    def __str__(self):
        return self.symbol + ", " + self.type + ", price: " + str(self.price) + ", size: " + str(self.size) + ", timestamp: " + str(self.timestamp) + ", subprovider: " + str(self.subprovider) + ", market_center: " + str(self.market_center) + ", condition: " + str(self.condition)


class EquitiesTrade:
    def __init__(self, symbol, price, size, total_volume, timestamp, subprovider, market_center, condition):
        self.symbol = symbol
        self.price = price
        self.size = size
        self.total_volume = total_volume
        self.timestamp = timestamp
        self.subprovider = subprovider
        self.market_center = market_center
        self.condition = condition
        
    def json_keys(self):
        return ["symbol","price","size","total_volume","timestamp","subprovider","market_center","condition"]
        
    def to_json_array(self):
        return f'["{self.symbol}",{self.price},{self.size},{self.total_volume},{self.timestamp},"{self.subprovider}","{self.market_center}","{self.condition}"]'
        
    def to_json(self):
        return f'{{"symbol":"{self.symbol}","price":{self.price},"size":{self.size},"total_volume":{self.total_volume},"timestamp":{self.timestamp},"subprovider":"{self.subprovider}","market_center":"{self.market_center}","condition":"{self.condition}"}}'

    def __str__(self):
        return self.symbol + ", trade, price: " + str(self.price) + ", size: " + str(self.size) + ", timestamp: " + str(self.timestamp) + ", subprovider: " + str(self.subprovider) + ", market_center: " + str(self.market_center) + ", condition: " + str(self.condition)

    def is_darkpool(self):
        if self.subprovider in [CTA_A, CTA_B, OTC, UTP, DELAYED_SIP]:
            return (not self.market_center) or self.market_center == 'D' or self.market_center == 'E' or self.market_center == '\0' or self.market_center.strip() == ''
        elif self.subprovider == NASDAQ_BASIC:
            return (not self.market_center) or self.market_center == 'L' or self.market_center == '2' or self.market_center == '\0' or self.market_center.strip() == ''
        else:
            return False


class IntrinioRealtimeEquitiesClient:
    def __init__(self, configuration: Dict[str, Any], on_trade: Optional[callable], on_quote: Optional[callable]):
        if configuration is None:
            raise ValueError("Options parameter is required")

        self.options = configuration
        self.api_key = configuration.get('api_key')
        self.username = configuration.get('username')
        self.password = configuration.get('password')
        self.provider = configuration.get('provider')
        self.ipaddress = configuration.get('ipaddress')
        self.tradesonly = configuration.get('tradesonly')
        self.bypass_parsing = configuration.get('bypass_parsing', False)
        self.delayed = configuration.get('delayed', False)

        if 'channels' in configuration:
            self.channels = set(configuration['channels'])
        else:
            self.channels = set()

        if 'logger' in configuration:
            self.logger = configuration['logger']
        else:
            log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            log_handler = logging.StreamHandler()
            log_handler.setFormatter(log_formatter)
            self.logger = logging.getLogger('intrinio_realtime')
            if 'debug' in configuration and configuration['debug'] == True:
                self.logger.setLevel(logging.DEBUG)
            else:
                self.logger.setLevel(logging.INFO)
            self.logger.addHandler(log_handler)

        if 'max_queue_size' in configuration:
            self.quotes = queue.Queue(maxsize=configuration['max_queue_size'])
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
        self.quote_handler = EquitiesQuoteHandler(self, self.bypass_parsing)
        self.joined_channels = set()
        self.last_queue_warning_time = 0
        self.last_self_heal_backoff = -1
        self.quote_handler.start()

    def auth_url(self) -> str:
        auth_url = ""

        if self.provider == REALTIME:
            auth_url = "https://realtime-mx.intrinio.com/auth"
        elif self.provider == IEX:
            auth_url = "https://realtime-mx.intrinio.com/auth"
        elif self.provider == DELAYED_SIP:
            auth_url = "https://realtime-delayed-sip.intrinio.com/auth"
        elif self.provider == NASDAQ_BASIC:
            auth_url = "https://realtime-nasdaq-basic.intrinio.com/auth"
        elif self.provider == CBOE_ONE:
            auth_url = "https://cboe-one.intrinio.com/auth"
        elif self.provider == MANUAL:
            auth_url = "http://" + self.ipaddress + "/auth"

        if self.api_key:
            auth_url = self.api_auth_url(auth_url)

        return auth_url

    def api_auth_url(self, auth_url: str) -> str:
        if "?" in auth_url:
            auth_url = auth_url + "&"
        else:
            auth_url = auth_url + "?"

        return auth_url + "api_key=" + self.api_key

    def websocket_url(self) -> str:
        delayed_part = "&delayed=true" if self.delayed else ""

        if self.provider == REALTIME:
            return "wss://realtime-mx.intrinio.com/socket/websocket?vsn=1.0.0&token=" + self.token + delayed_part
        elif self.provider == IEX:
            return "wss://realtime-mx.intrinio.com/socket/websocket?vsn=1.0.0&token=" + self.token + delayed_part
        elif self.provider == DELAYED_SIP:
            return "wss://realtime-delayed-sip.intrinio.com/socket/websocket?vsn=1.0.0&token=" + self.token + delayed_part
        elif self.provider == NASDAQ_BASIC:
            return "wss://realtime-nasdaq-basic.intrinio.com/socket/websocket?vsn=1.0.0&token=" + self.token + delayed_part
        elif self.provider == CBOE_ONE:
            return "wss://cboe-one.intrinio.com/socket/websocket?vsn=1.0.0&token=" + self.token + delayed_part
        elif self.provider == MANUAL:
            return "ws://" + self.ipaddress + "/socket/websocket?vsn=1.0.0&token=" + self.token + delayed_part
        else:
            return "wss://realtime-mx.intrinio.com/socket/websocket?vsn=1.0.0&token=" + self.token + delayed_part

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
        self.quote_receiver = EquitiesQuoteReceiver(self)
        self.quote_receiver.start()

    def on_connect(self):
        self.ready = True
        self.last_self_heal_backoff = -1
        self.refresh_channels()

    def on_queue_full(self):
        if time.time() - self.last_queue_warning_time > 1:
            self.logger.error("Quote queue is full! Dropped some new quotes")
            self.last_queue_warning_time = time.time()

    def join(self, channels: list[str]):
        if isinstance(channels, str):
            channels = [channels]

        self.channels = self.channels | set(channels)
        self.refresh_channels()

    def leave(self, channels: list[str]):
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

    def join_binary_message(self, channel: str):
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

    def leave_binary_message(self, channel: str):
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

    def valid_api_key(self, api_key: str):
        if not isinstance(api_key, str):
            return False

        if api_key == "":
            return False

        return True


class EquitiesQuoteReceiver(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self, group=None, args=(), kwargs={})
        self.daemon = True
        self.client = client
        self.enabled = True
        self.continuation_queue = queue.Queue(100)
        self.continuation_lock: threading.Lock = threading.Lock()

    def run(self):
        self.client.ws = websocket.WebSocketApp(
            self.client.websocket_url(),
            header={HEADER_MESSAGE_FORMAT_KEY: HEADER_MESSAGE_FORMAT_VALUE, HEADER_CLIENT_INFORMATION_KEY: HEADER_CLIENT_INFORMATION_VALUE},
            on_open=self.on_open,
            on_close=self.on_close,
            on_message=self.on_message,
            on_cont_message=self.on_cont_message,
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

    def stitch(self):
        full = None
        while not self.continuation_queue.empty():
            partial = self.continuation_queue.get(True, 1)
            if full is None:
                full = partial
            else:
                full = full.join(partial)
        return full

    def on_cont_message(self, partial_message, is_last): # The 3rd argument is continue flag. if 0, the data continue
        try:
            if DEBUGGING:  # This is here for performance reasons so we don't use slow reflection on every message.
                if isinstance(partial_message, str):
                    self.client.logger.debug(f"Received partial message (str): {partial_message.encode('utf-8').hex()}")
                else:
                    if isinstance(partial_message, bytes):
                        self.client.logger.debug(f"Received partial message (hex): {partial_message.hex()}")
            #self.client.logger.debug(f"Received partial message (hex): {partial_message.hex()}")

            self.continuation_lock.acquire()
            try:
                if is_last == 0:
                    self.continuation_queue.put(partial_message)
                else:
                    self.continuation_queue.put(partial_message)
                    full_message = self.stitch()
                    self.on_message(self.client.ws, full_message)
            finally:
                self.continuation_lock.release()
        except queue.Full:
            self.client.on_queue_full()
        except Exception as e:
            hex_message = ""
            if isinstance(partial_message, str):
                hex_message = partial_message.encode('utf-8').hex()
            else:
                if isinstance(partial_message, bytes):
                    hex_message = partial_message.hex()
            self.client.logger.error(f"Websocket on_message ERROR. Message as hex: {hex_message}; error: {repr(e)}")
            raise e

    def on_message(self, ws, message):
        try:
            if DEBUGGING:  # This is here for performance reasons so we don't use slow reflection on every message.
                if isinstance(message, str):
                    self.client.logger.debug(f"Received message (str): {message.encode('utf-8').hex()}")
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


class EquitiesQuoteHandler(threading.Thread):
    def __init__(self, client, bypass_parsing: bool):
        threading.Thread.__init__(self, group=None, args=(), kwargs={})
        self.daemon = True
        self.client = client
        self.bypass_parsing = bypass_parsing
        self.subprovider_codes = {
            0: NO_SUBPROVIDER,
            1: CTA_A,
            2: CTA_B,
            3: UTP,
            4: OTC,
            5: NASDAQ_BASIC,
            6: IEX,
            7: CBOE_ONE
        }

    def parse_quote(self, quote_bytes: bytes, start_index: int = 0) -> EquitiesQuote:
        buffer = memoryview(quote_bytes)
        symbol_length = buffer[start_index + 2]
        symbol = buffer[(start_index + 3):(start_index + 3 + symbol_length)].tobytes().decode("ascii")
        quote_type = "ask" if buffer[start_index] == 1 else "bid"
        price, size, timestamp = struct.unpack_from('<fLQ', buffer, start_index + 6 + symbol_length)

        condition_length = buffer[start_index + 22 + symbol_length]
        condition = ""
        if condition_length > 0:
            condition = buffer[(start_index + 23 + symbol_length):(start_index + 23 + symbol_length + condition_length)].tobytes().decode("ascii")

        subprovider = self.subprovider_codes.get(buffer[3 + symbol_length + start_index], IEX)  # default IEX for backward behavior consistency.
        market_center = buffer[(start_index + 4 + symbol_length):(start_index + 6 + symbol_length)].tobytes().decode("utf-16")

        return EquitiesQuote(symbol, quote_type, price, size, timestamp, subprovider, market_center, condition)


    def parse_trade(self, trade_bytes: bytes, start_index: int = 0) -> EquitiesTrade:
        buffer = memoryview(trade_bytes)
        symbol_length = buffer[start_index + 2]
        symbol = buffer[(start_index + 3):(start_index + 3 + symbol_length)].tobytes().decode("ascii")
        price, size, timestamp, total_volume = struct.unpack_from('<fLQL', buffer, start_index + 6 + symbol_length)
        
        condition_length = buffer[start_index + 26 + symbol_length]
        condition = ""
        if condition_length > 0:
            condition = buffer[(start_index + 27 + symbol_length):(start_index + 27 + symbol_length + condition_length)].tobytes().decode("ascii")
        
        subprovider = self.subprovider_codes.get(buffer[3 + symbol_length + start_index], IEX) # default IEX for backward behavior consistency.
        market_center = buffer[(start_index + 4 + symbol_length):(start_index + 6 + symbol_length)].tobytes().decode("utf-16")
        
        return EquitiesTrade(symbol, price, size, total_volume, timestamp, subprovider, market_center, condition)


    def parse_message(self, message_bytes: bytes, start_index: int, backlog_len: int) -> int:
        message_type = message_bytes[start_index]
        message_length = message_bytes[start_index + 1]
        new_start_index = start_index + message_length
        item = None
        if message_type == 0:  # this is a trade
            if callable(self.client.on_trade):
                try:
                    if self.bypass_parsing:
                        item = message_bytes[start_index:new_start_index - 1]
                    else:
                        item = self.parse_trade(message_bytes, start_index)
                    self.client.on_trade(item, backlog_len)
                except Exception as e:
                    self.client.logger.error(repr(e))
        else:  # message_type is ask or bid (quote)
            if callable(self.client.on_quote):
                try:
                    if self.bypass_parsing:
                        item = message_bytes[start_index:new_start_index - 1]
                    else:
                        item = self.parse_quote(message_bytes, start_index)
                    self.client.on_quote(item, backlog_len)
                except Exception as e:
                    self.client.logger.error(repr(e))

        return new_start_index

    def run(self):
        self.client.logger.debug("QuoteHandler ready")
        while True:
            message = self.client.quotes.get()
            backlog_len = self.client.quotes.qsize()
            if message is not None and len(message) > 0 and len(message) >= message[0] * 24: #sanity check on length. Should be at least as long as the smallest message times the number of messages it says it has.
                items_in_message = message[0]
                start_index = 1
                for i in range(0, items_in_message):
                    start_index = self.parse_message(message, start_index, backlog_len)