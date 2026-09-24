from distutils.command.config import config
import queue
import time
import threading
import requests
import websocket
import logging
import struct
from collections.abc import Callable
from enum import IntEnum, unique

from ._websocket import close_websocket_app, should_abort_handshake

_SELF_HEAL_BACKOFFS = [10, 30, 60, 300, 600]
_CONNECT_TIMEOUT_SECONDS = 30
_STABLE_SESSION_SECONDS = 1.0
_EMPTY_STRING = ""
_OPTIONS_TRADE_MESSAGE_SIZE = 72  # 61 used + 11 pad
_OPTIONS_QUOTE_MESSAGE_SIZE = 52  # 48 used + 4 pad
_OPTIONS_REFRESH_MESSAGE_SIZE = 52  # 44 used + 8 pad
_OPTIONS_UNUSUAL_ACTIVITY_MESSAGE_SIZE = 74  # 62 used + 12 pad
_NAN = float("NAN")

_stopFlag: threading.Event = threading.Event()
_logHandler: logging.Logger = logging.StreamHandler()
_logHandler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
_log: logging.Logger = logging.getLogger('intrinio_realtime_options')
_log.setLevel(logging.INFO)
_log.addHandler(_logHandler)

def log(message: str):
    _log.info(message)

def do_backoff(fn: Callable[[], bool], stop_flag: threading.Event = None):
    flag: threading.Event = stop_flag if stop_flag is not None else _stopFlag
    i: int = 0
    while not flag.is_set():
        try:
            if fn():
                return
        except Exception as e:
            _log.warning("Websocket - Attempt failed: {0}".format(e))
        if flag.is_set():
            return
        backoff: int = _SELF_HEAL_BACKOFFS[i]
        i = min(i + 1, len(_SELF_HEAL_BACKOFFS) - 1)
        if flag.wait(timeout=backoff):
            return

@unique
class Providers(IntEnum):
    OPRA = 1
    MANUAL = 2
    OPTIONS_EDGE = 3

@unique
class LogLevel(IntEnum):
    DEBUG = logging.DEBUG
    INFO = logging.INFO

class OptionsQuote:
    def __init__(self, contract: str, ask_price: float, ask_size: int, bid_price: float, bid_size: int, timestamp: float):
        self.contract: str = contract
        self.ask_price: float = ask_price
        self.bid_price: float = bid_price
        self.ask_size: int = ask_size
        self.bid_size: int = bid_size
        self.timestamp: float = timestamp

    def __str__(self) -> str:
        return "Quote (Contract: {0}, AskPrice: {1:.2f}, AskSize: {2}, BidPrice: {3:.2f}, BidSize: {4}, Timestamp: {5})"\
               .format(self.contract,
                       self.ask_price,
                       self.ask_size,
                       self.bid_price,
                       self.bid_size,
                       self.timestamp)

    def get_strike_price(self) -> float:
        whole: int = (ord(self.contract[13]) - ord('0')) * 10000 + (ord(self.contract[14]) - ord('0')) * 1000 + (ord(self.contract[15]) - ord('0')) * 100 + (ord(self.contract[16]) - ord('0')) * 10 + (ord(self.contract[17]) - ord('0'))
        part: float = float(ord(self.contract[18]) - ord('0')) * 0.1 + float(ord(self.contract[19]) - ord('0')) * 0.01 + float(ord(self.contract[20]) - ord('0')) * 0.001
        return float(whole) + part

    def is_put(self) -> bool:
        return self.contract[12] == 'P'

    def is_call(self) -> bool:
        return self.contract[12] == 'C'

    def get_expiration_date(self) -> time.struct_time:
        return time.strptime(self.contract[6:12], "%y%m%d")

    def get_underlying_symbol(self) -> str:
        return self.contract[0:6].rstrip('_')

@unique
class Exchange(IntEnum):
    NYSE_AMERICAN = ord('A')
    BOSTON = ord('B')
    CBOE = ord('C')
    MIAMI_EMERALD = ord('D')
    BATS_EDGX = ord('E')
    ISE_GEMINI = ord('H')
    ISE = ord('I')
    MERCURY = ord('J')
    MIAMI = ord('M')
    NYSE_ARCA = ord('N')
    MIAMI_PEARL = ord('O')
    NYSE_ARCA_DEPRECIATED = ord('P')
    NASDAQ = ord('Q')
    MIAX_SAPPHIRE = ord('S')
    NASDAQ_BX = ord('T')
    MEMX = ord('U')
    CBOE_C2 = ord('W')
    PHLX = ord('X')
    BATS_BZX = ord('Z')
    UNKNOWN = ord('?')

    @classmethod
    def _missing_(cls, value):
        return cls.UNKNOWN

class OptionsTrade:
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

    def __str__(self) -> str:
        return "Trade (Contract: {0}, Exchange: {1}, Price: {2:.2f}, Size: {3}, Timestamp: {4}, TotalVolume: {5}, Qualifiers: {6}, AskPriceAtExecution: {7:.2f}, BidPriceAtExecution: {8:.2f}, UnderlyingPriceAtExecution: {9:.2f})"\
               .format(self.contract,
                       self.exchange.name,
                       self.price,
                       self.size,
                       self.timestamp,
                       self.total_volume,
                       self.qualifiers,
                       self.ask_price_at_execution,
                       self.bid_price_at_execution,
                       self.underlying_price_at_execution)

    def get_strike_price(self) -> float:
        whole: int = (ord(self.contract[13]) - ord('0')) * 10000 + (ord(self.contract[14]) - ord('0')) * 1000 + (ord(self.contract[15]) - ord('0')) * 100 + (ord(self.contract[16]) - ord('0')) * 10 + (ord(self.contract[17]) - ord('0'))
        part: float = float(ord(self.contract[18]) - ord('0')) * 0.1 + float(ord(self.contract[19]) - ord('0')) * 0.01 + float(ord(self.contract[20]) - ord('0')) * 0.001
        return float(whole) + part

    def is_put(self) -> bool:
        return self.contract[12] == 'P'

    def is_call(self) -> bool:
        return self.contract[12] == 'C'

    def get_expiration_date(self) -> time.struct_time:
        return time.strptime(self.contract[6:12], "%y%m%d")

    def get_underlying_symbol(self) -> str:
        return self.contract[0:6].rstrip('_')

@unique
class OptionsUnusualActivitySentiment(IntEnum):
    NEUTRAL = 0
    BULLISH = 1
    BEARISH = 2

@unique
class OptionsUnusualActivityType(IntEnum):
    BLOCK = 3
    SWEEP = 4
    LARGE = 5
    UNUSUAL_SWEEP = 6

class OptionsRefresh:
    def __init__(self, contract: str, open_interest: int, open_price: float, close_price: float, high_price: float, low_price: float):
        self.contract: str = contract
        self.open_interest: int = open_interest
        self.open_price: float = open_price
        self.close_price: float = close_price
        self.high_price: float = high_price
        self.low_price: float = low_price

    def __str__(self) -> str:
        return "Refresh (Contract: {0}, OpenInterest: {1}, OpenPrice: {2:.2f}, ClosePrice: {3:.2f}, HighPrice: {4:.2f}, LowPrice: {5:.2f})"\
               .format(self.contract,
                       self.open_interest,
                       self.open_price,
                       self.close_price,
                       self.high_price,
                       self.low_price)

    def get_strike_price(self) -> float:
        whole: int = (ord(self.contract[13]) - ord('0')) * 10000 + (ord(self.contract[14]) - ord('0')) * 1000 + (ord(self.contract[15]) - ord('0')) * 100 + (ord(self.contract[16]) - ord('0')) * 10 + (ord(self.contract[17]) - ord('0'))
        part: float = float(ord(self.contract[18]) - ord('0')) * 0.1 + float(ord(self.contract[19]) - ord('0')) * 0.01 + float(ord(self.contract[20]) - ord('0')) * 0.001
        return float(whole) + part

    def is_put(self) -> bool:
        return self.contract[12] == 'P'

    def is_call(self) -> bool:
        return self.contract[12] == 'C'

    def get_expiration_date(self) -> time.struct_time:
        return time.strptime(self.contract[6:12], "%y%m%d")

    def get_underlying_symbol(self) -> str:
        return self.contract[0:6].rstrip('_')

class OptionsUnusualActivity:
    def __init__(self,
                 contract: str,
                 activity_type: OptionsUnusualActivityType,
                 sentiment: OptionsUnusualActivitySentiment,
                 total_value: float,
                 total_size: int,
                 average_price: float,
                 ask_price_at_execution: float,
                 bid_price_at_execution: float,
                 underlying_price_at_execution: float,
                 timestamp: float):
        self.contract: str = contract
        self.activity_type: OptionsUnusualActivityType = activity_type
        self.sentiment: OptionsUnusualActivitySentiment = sentiment
        self.total_value: float = total_value
        self.total_size: int = total_size
        self.average_price: float = average_price
        self.ask_price_at_execution: float = ask_price_at_execution
        self.bid_price_at_execution: float = bid_price_at_execution
        self.underlying_price_at_execution: float = underlying_price_at_execution
        self.timestamp: float = timestamp

    def __str__(self) -> str:
        return "Unusual Activity (Contract: {0}, Type: {1}, Sentiment: {2}, Total Value: {3:.2f}, Total Size: {4}, Average Price: {5:.2f}, Ask at Execution: {6:.2f}, Bid at Execution: {7:.2f}, Underlying Price at Execution: {8:.2f}, Timestamp: {9})"\
                .format(self.contract,
                        self.activity_type,
                        self.sentiment,
                        self.total_value,
                        self.total_size,
                        self.average_price,
                        self.ask_price_at_execution,
                        self.bid_price_at_execution,
                        self.underlying_price_at_execution,
                        self.timestamp)

    def get_strike_price(self) -> float:
        whole: int = (ord(self.contract[13]) - ord('0')) * 10000 + (ord(self.contract[14]) - ord('0')) * 1000 + (ord(self.contract[15]) - ord('0')) * 100 + (ord(self.contract[16]) - ord('0')) * 10 + (ord(self.contract[17]) - ord('0'))
        part: float = float(ord(self.contract[18]) - ord('0')) * 0.1 + float(ord(self.contract[19]) - ord('0')) * 0.01 + float(ord(self.contract[20]) - ord('0')) * 0.001
        return float(whole) + part

    def is_put(self) -> bool:
        return self.contract[12] == 'P'

    def is_call(self) -> bool:
        return self.contract[12] == 'C'

    def get_expiration_date(self) -> time.struct_time:
        return time.strptime(self.contract[6:12], "%y%m%d")

    def get_underlying_symbol(self) -> str:
        return self.contract[0:6].rstrip('_')

def _get_option_mask(use_on_trade: bool, use_on_quote: bool, use_on_refresh: bool, use_on_unusual_activity: bool) -> int:
    mask: int = 0
    if use_on_trade:
        mask |= 0b0001
    if use_on_quote:
        mask |= 0b0010
    if use_on_refresh:
        mask |= 0b0100
    if use_on_unusual_activity:
        mask |= 0b1000
    return mask

class _WebSocket:
    on_cont_message = None

    def __init__(self,
                 ws_url: str,
                 ws_lock: threading.Lock,
                 channels_lock: threading.RLock,
                 sent_channels: set[str],
                 stats: dict[str, int],
                 stats_lock: threading.Lock,
                 worker_threads: list[threading.Thread],
                 get_channels: Callable[[], set],
                 get_token: Callable[[], str],
                 get_url: Callable[[str], str],
                 use_on_trade: bool,
                 use_on_quote: bool,
                 use_on_refresh: bool,
                 use_on_ua: bool,
                 data_queue: queue.Queue,
                 stop_flag: threading.Event):
        self.url: str = ws_url
        self.__wsLock: threading.Lock = ws_lock
        self.__channels_lock: threading.RLock = channels_lock
        self.__sent_channels: set[str] = sent_channels
        self.__stats: dict[str, int] = stats
        self.__stats_lock: threading.Lock = stats_lock
        self.__worker_threads: list[threading.Thread] = worker_threads
        self.__get_channels: Callable[[], set] = get_channels
        self.__get_token: Callable[[], str] = get_token
        self.__get_url: Callable[[str], str] = get_url
        self.__use_on_trade: bool = use_on_trade
        self.__use_on_quote: bool = use_on_quote
        self.__use_on_refresh: bool = use_on_refresh
        self.__use_on_ua: bool = use_on_ua
        self.__data_queue: queue.Queue = data_queue
        self.__is_reconnecting: bool = False
        self.__last_reset: float = time.time()
        self.isReady: bool = False
        self.__handshake_event: threading.Event = threading.Event()
        self.__attempt_cancelled: threading.Event = threading.Event()
        self.__connect_deadline: float = float("inf")
        self.__backoff_index: int = 0
        self.__connect_generation: int = 0
        self.__connect_lock: threading.Lock = threading.Lock()
        self.__app_lock: threading.Lock = threading.Lock()
        self.__app = None
        self.__session_opened: bool = False
        self.__session_opened_at: float = 0.0
        self.__stop_flag: threading.Event = stop_flag

    def __close_leftover_sock(self):
        with self.__app_lock:
            app = self.__app
        close_websocket_app(app)

    def __on_open(self, ws):
        rejected = False
        with self.__connect_lock:
            if (self.__attempt_cancelled.is_set()
                    or self.__stop_flag.is_set()
                    or time.monotonic() >= self.__connect_deadline):
                self.__attempt_cancelled.set()
                rejected = True
            else:
                self.__session_opened = True
                self.__session_opened_at = time.monotonic()
            self.__handshake_event.set()
        if rejected:
            close_websocket_app(ws)
            return
        _log.info("Websocket - Connected")
        rejected = False
        with self.__channels_lock:
            if self.__attempt_cancelled.is_set() or self.__stop_flag.is_set():
                self.__attempt_cancelled.set()
                rejected = True
            else:
                self.__sent_channels.clear()
                self.__wsLock.acquire()
                try:
                    self.isReady = True
                    self.__is_reconnecting = False
                    for worker in self.__worker_threads:
                        if not worker.is_alive():
                            worker.start()
                finally:
                    self.__wsLock.release()
                if self.__get_channels and callable(self.__get_channels):
                    channels: set[str] = self.__get_channels()
                    if channels and (len(channels) > 0):
                        for symbol in channels:
                            symbol_bytes = bytes(symbol, 'utf-8')
                            message: bytes = bytearray(len(symbol_bytes) + 2)
                            message[0] = 74  # join code
                            message[1] = _get_option_mask(self.__use_on_trade, self.__use_on_quote, self.__use_on_refresh, self.__use_on_ua)
                            message[2:] = symbol_bytes
                            if self.isReady:
                                _log.info("Websocket - Joining channel: {0}".format(symbol))
                                try:
                                    self.send_binary(message)
                                    self.__sent_channels.add(symbol)
                                except Exception:
                                    self.__wsLock.acquire()
                                    try:
                                        self.isReady = False
                                    finally:
                                        self.__wsLock.release()
                                    self.__sent_channels.clear()
                                    close_websocket_app(ws)
                                    raise
        if rejected:
            close_websocket_app(ws)

    def __on_close(self, ws, closeStatusCode, closeMsg):
        _log.info("Websocket - Closed - {0}: {1}".format(closeStatusCode, closeMsg))
        with self.__channels_lock:
            self.__sent_channels.clear()
        self.__wsLock.acquire()
        try:
            self.isReady = False
        finally:
            self.__wsLock.release()

    def __on_error(self, ws, error):
        _log.error("Websocket - Error - {0}".format(error))

    def __on_data(self, ws, data, code, is_last):
        if code == websocket.ABNF.OPCODE_BINARY:
            with self.__stats_lock:
                self.__stats["data"] += 1
            self.__data_queue.put(data)
        else:
            _log.debug("Websocket - Message received")
            with self.__stats_lock:
                self.__stats["text"] += 1
                _log.error("Error received: {0}".format(data))

    def start(self):
        while not self.__stop_flag.is_set():
            handshake_event = threading.Event()
            self.__handshake_event = handshake_event
            attempt_cancelled = threading.Event()
            self.__attempt_cancelled = attempt_cancelled
            connect_deadline = time.monotonic() + _CONNECT_TIMEOUT_SECONDS
            self.__connect_deadline = connect_deadline
            with self.__connect_lock:
                self.__connect_generation += 1
                generation = self.__connect_generation
            self.__session_opened = False
            self.__wsLock.acquire()
            try:
                self.isReady = False
            finally:
                self.__wsLock.release()
            self.__close_leftover_sock()

            # Leave on_cont_message unset so websocket-client aggregates fragmented messages.
            app = websocket.WebSocketApp(
                self.url,
                on_open=self.__on_open,
                on_close=self.__on_close,
                on_data=self.__on_data,
                on_error=self.__on_error,
            )
            with self.__app_lock:
                if self.__stop_flag.is_set():
                    close_websocket_app(app)
                    return
                self.__app = app

            def watchdog(
                this_generation=generation,
                handshake_event=handshake_event,
                cancelled=attempt_cancelled,
                deadline=connect_deadline,
                attempt_app=app,
            ):
                if should_abort_handshake(
                    handshake_event,
                    self.__stop_flag,
                    this_generation,
                    lambda: self.__connect_generation,
                    deadline,
                ):
                    with self.__connect_lock:
                        if this_generation != self.__connect_generation:
                            return
                        _log.warning("Websocket - Connect timed out after {0}s".format(_CONNECT_TIMEOUT_SECONDS))
                        cancelled.set()
                        close_websocket_app(attempt_app)

            threading.Thread(target=watchdog, daemon=True).start()
            if self.__stop_flag.is_set():
                close_websocket_app(app)
                return
            try:
                app.run_forever(skip_utf8_validation=True)
            except Exception as e:
                _log.warning("Websocket - Attempt failed: {0}".format(e))
            finally:
                with self.__app_lock:
                    if self.__app is app:
                        self.__app = None

            self.__wsLock.acquire()
            try:
                self.isReady = False
            finally:
                self.__wsLock.release()

            if self.__stop_flag.is_set():
                return

            _log.info("Websocket - Reconnecting...")
            skip_wait: bool = (
                self.__session_opened
                and (time.monotonic() - self.__session_opened_at) >= _STABLE_SESSION_SECONDS
            )
            if skip_wait:
                self.__backoff_index = 0
            token_ready: bool = False
            while not self.__stop_flag.is_set():
                if skip_wait:
                    skip_wait = False
                else:
                    backoff: int = _SELF_HEAL_BACKOFFS[self.__backoff_index]
                    self.__backoff_index = min(self.__backoff_index + 1, len(_SELF_HEAL_BACKOFFS) - 1)
                    if self.__stop_flag.wait(timeout=backoff):
                        return
                try:
                    token: str = self.__get_token()
                    if token:
                        self.url = self.__get_url(token)
                        token_ready = True
                        break
                    _log.warning("Websocket - Reconnect attempt failed: no token")
                except Exception as e:
                    _log.warning("Websocket - Reconnect attempt failed: {0}".format(e))
            if not token_ready:
                return
        # super().run_forever(ping_interval = 5, ping_timeout = 2, skip_utf8_validation = True)

    def stop(self):
        self.__close_leftover_sock()

    def close(self):
        self.stop()

    # SDK-owned callback entry points; these do not expose or mutate
    # websocket-client state.
    def on_open(self, ws):
        self.__on_open(ws)

    def on_data(self, ws, data, code, is_last):
        self.__on_data(ws, data, code, is_last)

    def send(self, message: str):
        with self.__app_lock:
            app = self.__app
        if app is None:
            raise RuntimeError("Websocket is not connected")
        app.send(message, websocket.ABNF.OPCODE_TEXT)

    def send_binary(self, message: bytes):
        with self.__app_lock:
            app = self.__app
        if app is None:
            raise RuntimeError("Websocket is not connected")
        app.send(message, websocket.ABNF.OPCODE_BINARY)

    def reset(self):
        self.__last_reset = time.time()

class Config:
    def __init__(self, api_key: str, provider: Providers, num_threads: int = 4, log_level: LogLevel = LogLevel.INFO,
                 manual_ip_address: str = None, symbols: set[str] = None, delayed: bool = False):
        if isinstance(num_threads, bool) or not isinstance(num_threads, int) or num_threads < 1:
            raise ValueError("num_threads must be a positive integer")
        self.api_key: str = api_key
        self.provider: Providers = provider
        self.num_threads: int = num_threads
        self.manual_ip_address: str = manual_ip_address
        self.symbols: set[str] = symbols
        self.log_level: LogLevel = log_level
        self.delayed: bool = delayed

def _transform_contract_to_new(contract: str) -> str:
    if (len(contract) <= 9) or (contract.find('.') >= 9):
        return contract
    else:  # this is of the old format and we need to translate it. ex: AAPL__220101C00140000, TSLA__221111P00195000
        symbol: str = contract[0:6].rstrip('_')
        date: str = contract[6:12]
        call_put: str = contract[12]
        whole_price: str = contract[13:18].lstrip('0')
        if whole_price == '':
            whole_price = '0'
        decimal_price: str = contract[18:]
        if decimal_price[2] == '0':
            decimal_price = decimal_price[0:2]
        return "{symbol}_{date}{call_put}{whole_price}.{decimal_price}".format(
            symbol=symbol,
            date=date,
            call_put=call_put,
            whole_price=whole_price,
            decimal_price=decimal_price)

def _copy_to(src: list, dest: list, dest_index: int):
    for i in range(0, len(src)):
        dest[i + dest_index] = src[i]

def _transform_contract_to_old(alternate_formatted_contract: bytes) -> str:
    # Transform from server format to normal format
    # From this: AAPL_201016C100.00 or ABC_201016C100.003
    # To this: AAPL__201016C00100000 or ABC___201016C00100003
    contract_chars: list = [ord('_'), ord('_'), ord('_'), ord('_'), ord('_'), ord('_'), ord('2'), ord('2'), ord('0'), ord('1'), ord('0'), ord('1'), ord('C'), ord('0'), ord('0'), ord('0'), ord('0'), ord('0'), ord('0'), ord('0'), ord('0')]
    underscore_index: int = alternate_formatted_contract.find(ord('_'))
    decimal_index: int = alternate_formatted_contract[9:].find(ord('.')) + 9  # ignore decimals in tickersymbol
    _copy_to(alternate_formatted_contract[0:underscore_index], contract_chars, 0)  # copy symbol
    _copy_to(alternate_formatted_contract[underscore_index+1:underscore_index+7], contract_chars, 6)  # copy date
    _copy_to(alternate_formatted_contract[underscore_index+7:underscore_index+8], contract_chars, 12)  # copy put / call
    _copy_to(alternate_formatted_contract[underscore_index+8:decimal_index], contract_chars, 18 - (decimal_index - underscore_index - 8))  # whole number copy
    _copy_to(alternate_formatted_contract[decimal_index+1:], contract_chars, 18)  # decimal number copy
    return bytes(contract_chars).decode('ascii')

def _get_seconds_from_epoch_from_ticks(ticks: int) -> float:
    return float(ticks) / 1_000_000_000.0

def _scale_value(value: int, scale_type: int) -> float:
    match scale_type:
        case 0x00:
            return float(value)  # divided by 1
        case 0x01:
            return float(value) / 10.0
        case 0x02:
            return float(value) / 100.0
        case 0x03:
            return float(value) / 1_000.0
        case 0x04:
            return float(value) / 10_000.0
        case 0x05:
            return float(value) / 100_000.0
        case 0x06:
            return float(value) / 1_000_000.0
        case 0x07:
            return float(value) / 10_000_000.0
        case 0x08:
            return float(value) / 100_000_000.0
        case 0x09:
            return float(value) / 1_000_000_000.0
        case 0x0A:
            return float(value) / 512.0
        case 0x0F:
            return 0.0
        case _:
            return float(value)  # divided by 1

def _scale_uint64(value: int, scale_type: int) -> float:
    if value == 18446744073709551615:
        return _NAN
    else:
        return _scale_value(value, scale_type)

def _scale_int32(value: int, scale_type: int) -> float:
    if value == 2147483647 or value == -2147483648:
        return _NAN
    else:
        return _scale_value(value, scale_type)

def _thread_fn(index: int, data: queue.Queue,
               on_trade: Callable[[OptionsTrade], None],
               on_quote: Callable[[OptionsQuote], None] = None,
               on_refresh: Callable[[OptionsRefresh], None] = None,
               on_unusual_activity: Callable[[OptionsUnusualActivity], None] = None,
               stop_flag: threading.Event = None):
    flag: threading.Event = stop_flag if stop_flag is not None else _stopFlag
    _log.debug("Starting worker thread {0}".format(index))
    datum: bytes = None
    count: int = 0
    start_index: int = 1
    msg_type: int = 0
    message: bytes = None
    while not flag.is_set():
        try:
            datum = data.get(True, 1.0)
            count = datum[0]
            start_index = 1
            for _ in range(count):
                if flag.is_set():
                    break
                msg_type = datum[start_index + 22]
                if msg_type == 1:  # Quote
                    message: bytes = datum[start_index:(start_index + _OPTIONS_QUOTE_MESSAGE_SIZE)]
                    # byte structure:
                    # 	contract length [0]
                    # 	contract [1-21] utf-8 string
                    # 	event type [22] uint8
                    # 	price type [23] uint8
                    # 	ask price [24-27] int32
                    # 	ask size [28-31] uint32
                    # 	bid price [32-35] int32
                    # 	bid size [36-39] uint32
                    # 	timestamp [40-47] uint64
                    contract: str = _transform_contract_to_old(message[1:message[0]+1])
                    ask_price: float = _scale_int32(struct.unpack_from('<l', message, 24)[0], message[23])
                    ask_size: int = struct.unpack_from('<L', message, 28)[0]
                    bid_price: float = _scale_int32(struct.unpack_from('<l', message, 32)[0], message[23])
                    bid_size: int = struct.unpack_from('<L', message, 36)[0]
                    timestamp: float = _get_seconds_from_epoch_from_ticks(struct.unpack_from('<Q', message, 40)[0])
                    if on_quote:
                        on_quote(OptionsQuote(contract, ask_price, ask_size, bid_price, bid_size, timestamp))
                    start_index = start_index + _OPTIONS_QUOTE_MESSAGE_SIZE
                elif msg_type == 0:  # Trade
                    message: bytes = datum[start_index:(start_index + _OPTIONS_TRADE_MESSAGE_SIZE)]
                    #  byte structure:
                    #  contract length [0] uint8
                    #  contract [1-21] utf-8 string
                    #  event type [22] uint8
                    #  price type [23] uint8
                    #  underlying price type [24] uint8
                    #  price [25-28] int32
                    #  size [29-32] uint32
                    #  timestamp [33-40] uint64
                    #  total volume [41-48] uint64
                    #  ask price at execution [49-52] int32
                    #  bid price at execution [53-56] int32
                    #  underlying price at execution [57-60] int32
                    #  qualifiers [61-64]
                    #  exchange [65]
                    contract: str = _transform_contract_to_old(message[1:message[0]+1])
                    price: float = _scale_int32(struct.unpack_from('<l', message, 25)[0], message[23])
                    size: int = struct.unpack_from('<L', message, 29)[0]
                    timestamp: float = _get_seconds_from_epoch_from_ticks(struct.unpack_from('<Q', message, 33)[0])
                    total_volume: int = struct.unpack_from('<Q', message, 41)[0]
                    ask_price_at_execution: int = _scale_int32(struct.unpack_from('<l', message, 49)[0], message[23])
                    bid_price_at_execution: int = _scale_int32(struct.unpack_from('<l', message, 53)[0], message[23])
                    underlying_price_at_execution: int = _scale_int32(struct.unpack_from('<l', message, 57)[0], message[24])
                    qualifiers: tuple = (message[61], message[62], message[63], message[64])
                    exchange: Exchange = Exchange(message[65])
                    if on_trade:
                        on_trade(OptionsTrade(contract, exchange, price, size, timestamp, total_volume, qualifiers, ask_price_at_execution, bid_price_at_execution, underlying_price_at_execution))
                    start_index = start_index + _OPTIONS_TRADE_MESSAGE_SIZE
                elif msg_type > 2:  # Unusual Activity
                    message: bytes = datum[start_index:(start_index + _OPTIONS_UNUSUAL_ACTIVITY_MESSAGE_SIZE)]
                    # byte structure:
                    # contract length [0] uint8
                    # contract [1-21] utf-8 string
                    # event type [22] uint8
                    # sentiment type [23] uint8
                    # price type [24] uint8
                    # underlying price type [25] uint8
                    # total value [26-33] uint64
                    # total size [34-37] uint32
                    # average price [38-41] int32
                    # ask price at execution [42-45] int32
                    # bid price at execution [46-49] int32
                    # underlying price at execution [50-53] int32
                    # timestamp [54-61] uint64
                    contract: str = _transform_contract_to_old(message[1:message[0]+1])
                    activity_type: OptionsUnusualActivityType = message[22]
                    sentiment: OptionsUnusualActivitySentiment = message[23]
                    total_value: float = _scale_uint64(struct.unpack_from('<Q', message, 26)[0], message[24])
                    total_size: int = struct.unpack_from('<L', message, 34)[0]
                    average_price: float = _scale_int32(struct.unpack_from('<l', message, 38)[0], message[24])
                    ask_price_at_execution: float = _scale_int32(struct.unpack_from('<l', message, 42)[0], message[24])
                    bid_price_at_execution: float = _scale_int32(struct.unpack_from('<l', message, 46)[0], message[24])
                    underlying_price_at_execution: float = _scale_int32(struct.unpack_from('<l', message, 50)[0], message[25])
                    timestamp: float = _get_seconds_from_epoch_from_ticks(struct.unpack_from('<Q', message, 54)[0])
                    if on_unusual_activity:
                        on_unusual_activity(OptionsUnusualActivity(contract, activity_type, sentiment, total_value, total_size, average_price, ask_price_at_execution, bid_price_at_execution, underlying_price_at_execution, timestamp))
                    start_index = start_index + _OPTIONS_UNUSUAL_ACTIVITY_MESSAGE_SIZE
                elif msg_type == 2:  # Refresh
                    message: bytes = datum[start_index:(start_index + _OPTIONS_REFRESH_MESSAGE_SIZE)]
                    # byte structure:
                    # contract length [0] uint8
                    # contract [1-21] utf-8 string
                    # event type [22] uint8
                    # price type [23] uint8
                    # open interest [24-27] uint32
                    # open price [28-31] int32
                    # close price [32-35] int32
                    # high price [36-39] int32
                    # low price [40-43] int32
                    contract: str = _transform_contract_to_old(message[1:message[0]+1])
                    open_interest: int = struct.unpack_from('<L', message, 24)[0]
                    open_price: float = _scale_int32(struct.unpack_from('<l', message, 28)[0], message[23])
                    close_price: float = _scale_int32(struct.unpack_from('<l', message, 32)[0], message[23])
                    high_price: float = _scale_int32(struct.unpack_from('<l', message, 36)[0], message[23])
                    low_price: float = _scale_int32(struct.unpack_from('<l', message, 40)[0], message[23])
                    if on_refresh:
                        on_refresh(OptionsRefresh(contract, open_interest, open_price, close_price, high_price, low_price))
                    start_index = start_index + _OPTIONS_REFRESH_MESSAGE_SIZE
                else:
                    _log.warn("Invalid Message Type: {0}".format(msg_type))
        except queue.Empty:
            continue
        except Exception as e:
            _log.error(f"Worker thread {index} Exception {e}")
            datum_hex = datum.hex() if isinstance(datum, (bytes, bytearray)) else repr(datum)
            message_hex = message.hex() if isinstance(message, (bytes, bytearray)) else repr(message)
            _log.error(f"\tCurrent count: {count}\r\n\tCurrent start_index: {start_index}\r\n\tCurrent msg_type: {msg_type}\r\n\tFull message (hex): {datum_hex}\r\n\tScoped message: {message_hex}")
            continue
    _log.debug("Worker thread {0} stopped".format(index))

class IntrinioRealtimeOptionsClient:
    def __init__(self, config: Config, on_trade: Callable[[OptionsTrade], None], on_quote: Callable[[OptionsQuote], None] = None,
                 on_refresh: Callable[[OptionsRefresh], None] = None,
                 on_unusual_activity: Callable[[OptionsUnusualActivity], None] = None):
        if not config:
            raise ValueError("Config is required")
        if (not config.api_key) or (not isinstance(config.api_key, str)):
            raise ValueError("You must provide a valid API key")
        if (not config.provider) or (not isinstance(config.provider, Providers)):
            raise ValueError("You must specify a valid provider")
        if ((config.provider == Providers.MANUAL)) and (
                (not config.manual_ip_address) or (not isinstance(config.manual_ip_address, str))):
            raise ValueError("You must specify an IP address for a manual configuration")
        if on_trade:
            if callable(on_trade):
                self.__use_on_trade: bool = True
            else:
                raise ValueError("Parameter 'on_trade' must be a function")
        else:
            self.__use_on_trade: bool = False
        if on_quote:
            if callable(on_quote):
                self.__use_on_quote: bool = True
            else:
                raise ValueError("Parameter 'on_quote' must be a function")
        else:
            self.__use_on_quote: bool = False
        if on_refresh:
            if callable(on_refresh):
                self.__use_on_refresh: bool = True
            else:
                raise ValueError("Parameter 'on_refresh' must be a function")
        else:
            self.__use_on_refresh: bool = False
        if on_unusual_activity:
            if callable(on_unusual_activity):
                self.__use_on_unusual_activity: bool = True
            else:
                raise ValueError("Parameter 'on_unusual_activity' must be a function")
        else:
            self.__use_on_unusual_activity: bool = False
        if (not config.delayed) or (not isinstance(config.delayed, bool)):
            self.__delayed: bool = False
        else:
            self.__delayed: bool = config.delayed
        self.__provider: Providers = config.provider
        self.__apiKey: str = config.api_key
        self.__manualIP: str = config.manual_ip_address
        self.__token: tuple[str, float] = (None, 0.0)
        self.__webSocket: _WebSocket = None
        if config.symbols and isinstance(config.symbols, (list, set)):
            self.__channels: set[str] = set((_transform_contract_to_new(symbol)) for symbol in config.symbols)
        else:
            self.__channels: set[str] = set()
        self.__data: queue.Queue = queue.Queue()
        self.__t_lock: threading.Lock = threading.Lock()
        self.__ws_lock: threading.Lock = threading.Lock()
        self.__channels_lock: threading.RLock = threading.RLock()
        self.__sent_channels: set[str] = set()
        self.__stats: dict[str, int] = {"data": 0, "text": 0}
        self.__stats_lock: threading.Lock = threading.Lock()
        self.__lifecycle_lock: threading.RLock = threading.RLock()
        self.__on_trade = on_trade
        self.__on_quote = on_quote
        self.__on_refresh = on_refresh
        self.__on_unusual_activity = on_unusual_activity
        if (isinstance(config.num_threads, bool)
                or not isinstance(config.num_threads, int)
                or config.num_threads < 1):
            raise ValueError("num_threads must be a positive integer")
        self.__num_threads: int = config.num_threads
        self._stop_event: threading.Event = threading.Event()
        self._stop_generation: int = 0
        self.__worker_threads: list[threading.Thread] = self.__make_worker_threads()
        self.__socket_thread: threading.Thread = None
        self.__is_started: bool = False
        self.__is_starting: bool = False
        self.__is_stopping: bool = False
        self.__start_complete: threading.Event = threading.Event()
        self.__start_complete.set()
        self.__start_result = {"error": None}
        self.__stopping_thread = None
        self.__stop_complete: threading.Event = threading.Event()
        self.__stop_complete.set()
        self.__stop_result = {"error": None}
        _log.setLevel(config.log_level)

    def __make_worker_threads(self) -> list[threading.Thread]:
        return [threading.Thread(
            group=None,
            target=_thread_fn,
            args=(i, self.__data, self.__on_trade, self.__on_quote, self.__on_refresh, self.__on_unusual_activity, self._stop_event),
            kwargs={},
            daemon=True
        ) for i in range(self.__num_threads)]

    def __all_ready(self) -> bool:
        self.__ws_lock.acquire()
        ready: bool = True
        try:
            ready = (self.__webSocket is not None) and (self.__webSocket.isReady)
        finally:
            self.__ws_lock.release()
        return ready

    def __get_websocket(self) -> _WebSocket:
        return self.__webSocket

    def __get_auth_url(self) -> str:
        if self.__provider == Providers.OPRA:
            return "https://realtime-options.intrinio.com/auth?api_key=" + self.__apiKey
        elif self.__provider == Providers.OPTIONS_EDGE:
            return "https://options-edge.intrinio.com/auth?api_key=" + self.__apiKey
        elif self.__provider == Providers.MANUAL:
            return "http://" + self.__manualIP + "/auth?api_key=" + self.__apiKey
        else:
            raise ValueError("Provider not specified")

    def __get_web_socket_url(self, token: str) -> str:
        delay: str = "&delayed=true" if self.__delayed else ""
        if self.__provider == Providers.OPRA:
            return "wss://realtime-options.intrinio.com/socket/websocket?vsn=1.0.0&token=" + token + delay
        elif self.__provider == Providers.OPTIONS_EDGE:
            return "wss://options-edge.intrinio.com/socket/websocket?vsn=1.0.0&token=" + token + delay
        elif self.__provider == Providers.MANUAL:
            return "ws://" + self.__manualIP + "/socket/websocket?vsn=1.0.0&token=" + token + delay
        else:
            raise ValueError("Provider not specified")

    def __try_set_token(self) -> bool:
        _log.info("Authorizing...")
        headers = {"Client-Information": "IntrinioOptionsPythonSDKv2.5"}
        try:
            response: requests.Response = requests.get(self.__get_auth_url(), headers=headers, timeout=1)
            if response.status_code != 200:
                _log.error(
                    "Authorization Failure (status code = {0}): The authorization key you provided is likely incorrect.".format(
                        response.status_code))
                return False
            self.__token = (response.text, time.time())
            _log.info("Authorization successful.")
            return True
        except requests.exceptions.Timeout:
            _log.error("Authorization Failure: The request timed out.")
            return False
        except requests.exceptions.ConnectionError as err:
            _log.error("Authorization Failure: {0}".format(err))
            return False

    def __get_token(self) -> str:
        self.__t_lock.acquire()
        try:
            if ((time.time() - self.__token[1]) > (60 * 60 * 24)):  # 60sec/min * 60min/hr * 24hrs = 1 day
                do_backoff(self.__try_set_token, self._stop_event)
            return self.__token[0]
        finally:
            self.__t_lock.release()

    def __refresh_token(self) -> str:
        self.__t_lock.acquire()
        try:
            self.__token = (None, 0.0)
        finally:
            self.__t_lock.release()
        return self.__get_token()

    def __get_channels(self) -> set[str]:
        with self.__channels_lock:
            return self.__channels.copy()

    def __join(self, symbol: str):
        transformed_symbol: str = _transform_contract_to_new(symbol)
        with self.__channels_lock:
            if transformed_symbol not in self.__channels:
                self.__channels.add(transformed_symbol)
            if transformed_symbol not in self.__sent_channels:
                symbol_bytes = bytes(transformed_symbol, 'utf-8')
                message: bytes = bytearray(len(symbol_bytes)+2)
                message[0] = 74  # join code
                message[1] = _get_option_mask(self.__use_on_trade, self.__use_on_quote, self.__use_on_refresh, self.__use_on_unusual_activity)
                message[2:] = symbol_bytes
                ws = self.__webSocket
                if ws is not None and ws.isReady:
                    _log.info("Websocket - Joining channel: {0}".format(transformed_symbol))
                    ws.send_binary(message)
                    self.__sent_channels.add(transformed_symbol)

    def __leave(self, symbol: str, forget: bool = True):
        transformed_symbol: str = _transform_contract_to_new(symbol)
        with self.__channels_lock:
            if transformed_symbol not in self.__channels:
                return
            symbol_bytes = bytes(transformed_symbol, 'utf-8')
            message: bytes = bytearray(len(symbol_bytes) + 2)
            message[0] = 76  # leave code
            message[1] = _get_option_mask(self.__use_on_trade, self.__use_on_quote, self.__use_on_refresh, self.__use_on_unusual_activity)
            message[2:] = symbol_bytes
            try:
                ws = self.__webSocket
                if ws is not None and ws.isReady:
                    _log.info("Websocket - Leaving channel: {0}".format(transformed_symbol))
                    ws.send_binary(message)
            finally:
                self.__sent_channels.discard(transformed_symbol)
                if forget:
                    self.__channels.discard(transformed_symbol)

    def join(self, *symbols):
        for (symbol) in symbols:
            self.__join(symbol)

    def join_firehose(self):
        with self.__channels_lock:
            if "$FIREHOSE" in self.__channels:
                _log.warn("This client has already joined the firehose channel")
                return
        self.__join("$FIREHOSE")

    def leave(self, *symbols):
        if not symbols:
            _log.info("Leaving all channels")
            with self.__channels_lock:
                channels: set[str] = self.__channels.copy()
            for (symbol) in channels:
                self.__leave(symbol)
        symbol_set: set[str] = set(symbols)
        for sym in symbol_set:
            self.__leave(sym)

    def leave_firehose(self):
        with self.__channels_lock:
            joined = "$FIREHOSE" in self.__channels
        if joined:
            self.__leave("$FIREHOSE")

    def __socket_start_fn(self, token: str, stop_event: threading.Event,
                          worker_threads: list[threading.Thread], data_queue: queue.Queue):
        if not token:
            _log.error("Websocket - Missing token")
            return
        _log.info("Websocket - Connecting...")
        ws_url: str = self.__get_web_socket_url(token)
        if stop_event.is_set():
            return
        web_socket = _WebSocket(ws_url,
                                self.__ws_lock,
                                self.__channels_lock,
                                self.__sent_channels,
                                self.__stats,
                                self.__stats_lock,
                                worker_threads,
                                self.__get_channels,
                                self.__refresh_token,
                                self.__get_web_socket_url,
                                self.__use_on_trade,
                                self.__use_on_quote,
                                self.__use_on_refresh,
                                self.__use_on_unusual_activity,
                                data_queue,
                                stop_event)
        self.__webSocket = web_socket
        web_socket.start()

    def start(self):
        if (not (self.__use_on_trade or self.__use_on_quote or self.__use_on_refresh or self.__use_on_unusual_activity)):
            raise ValueError("You must set at least one callback method before starting client")
        with self.__lifecycle_lock:
            if (self.__is_started
                    and self.__socket_thread is not None
                    and self.__socket_thread.is_alive()
                    and not self._stop_event.is_set()):
                return
            if self.__is_stopping:
                raise RuntimeError("Cannot start while client is stopping")
            if self.__is_starting:
                start_complete = self.__start_complete
                start_result = self.__start_result
                wait_for_start = True
            else:
                wait_for_start = False
                self.__is_starting = True
                start_complete = threading.Event()
                start_result = {"error": None}
                self.__start_complete = start_complete
                self.__start_result = start_result
                stop_generation = self._stop_generation
        if wait_for_start:
            start_complete.wait()
            if start_result["error"] is not None:
                raise RuntimeError(
                    "Concurrent start failed: {0}".format(start_result["error"])
                ) from start_result["error"]
            return

        failure = None
        try:
            while True:
                leftover = self.__socket_thread
                if leftover is not None and leftover.is_alive():
                    close_websocket_app(self.__webSocket)
                    leftover.join(timeout=_CONNECT_TIMEOUT_SECONDS + 1)
                    if leftover.is_alive():
                        with self.__lifecycle_lock:
                            if self._stop_generation != stop_generation:
                                raise RuntimeError("Cannot start while client is stopping")
                        raise RuntimeError("Previous socket thread did not exit")
                if any(worker.is_alive() for worker in self.__worker_threads):
                    raise RuntimeError("Previous worker threads did not exit")
                with self.__lifecycle_lock:
                    if self._stop_generation != stop_generation:
                        raise RuntimeError("Cannot start while client is stopping")
                    self._stop_event = threading.Event()
                    stop_event = self._stop_event
                    self.__data = queue.Queue()
                    data_queue = self.__data
                    self.__worker_threads = self.__make_worker_threads()
                    worker_threads = self.__worker_threads
                token: str = self.__get_token()
                if stop_event.is_set():
                    raise RuntimeError("Cannot start while client is stopping")
                if not token:
                    if stop_event.wait(timeout=_SELF_HEAL_BACKOFFS[0]):
                        raise RuntimeError("Cannot start while client is stopping")
                    continue
                with self.__lifecycle_lock:
                    if (self._stop_generation != stop_generation
                            or self._stop_event is not stop_event
                            or stop_event.is_set()):
                        raise RuntimeError("Cannot start while client is stopping")
                    socket_thread = threading.Thread(
                        group=None,
                        target=self.__socket_start_fn,
                        args=(token, stop_event, worker_threads, data_queue),
                        kwargs={},
                        daemon=True
                    )
                    self.__socket_thread = socket_thread
                    self.__is_started = True
                    try:
                        socket_thread.start()
                    except Exception:
                        self.__is_started = False
                        raise
                    if (self._stop_generation != stop_generation
                            or self._stop_event is not stop_event
                            or stop_event.is_set()):
                        self.__is_started = False
                        raise RuntimeError("Cannot start while client is stopping")
                return
        except BaseException as error:
            failure = error
            raise
        finally:
            with self.__lifecycle_lock:
                self.__is_starting = False
                start_result["error"] = failure
                start_complete.set()

    def stop(self):
        current_thread = threading.current_thread()
        with self.__lifecycle_lock:
            if self.__is_stopping:
                if self.__stopping_thread is current_thread:
                    return
                stop_complete = self.__stop_complete
                stop_result = self.__stop_result
                wait_for_stop = True
            else:
                wait_for_stop = False
                self.__is_stopping = True
                self.__stopping_thread = current_thread
                stop_complete = threading.Event()
                stop_result = {"error": None}
                self.__stop_complete = stop_complete
                self.__stop_result = stop_result
                _log.info("Stopping...")
                self._stop_generation += 1
                self._stop_event.set()
                ws = self.__webSocket
                worker_threads = self.__worker_threads
                socket_thread = self.__socket_thread
                shutdown_deadline = time.monotonic() + _CONNECT_TIMEOUT_SECONDS + 1
        if wait_for_stop:
            stop_complete.wait()
            if stop_result["error"] is not None:
                raise RuntimeError(stop_result["error"])
            return

        candidate_threads = [
            worker for worker in worker_threads
            if worker.ident is not None and worker is not current_thread
        ]
        if (socket_thread is not None
                and socket_thread.ident is not None
                and socket_thread is not current_thread):
            candidate_threads.append(socket_thread)
        surviving_threads = []
        failure = None
        try:
            try:
                if ws is not None:
                    self.__ws_lock.acquire()
                    try:
                        ws.isReady = False
                    finally:
                        self.__ws_lock.release()
                    ws.stop()
                with self.__channels_lock:
                    self.__sent_channels.clear()
            finally:
                self.__ws_lock.acquire()
                try:
                    if ws is not None:
                        ws.isReady = False
                finally:
                    self.__ws_lock.release()
                if ws is not None:
                    ws.stop()
                for i in range(len(worker_threads)):
                    worker = worker_threads[i]
                    if worker.ident is not None and worker is not current_thread:
                        worker.join(timeout=max(0.0, shutdown_deadline - time.monotonic()))
                if (socket_thread is not None
                        and socket_thread.ident is not None
                        and socket_thread is not current_thread):
                    socket_thread.join(timeout=max(0.0, shutdown_deadline - time.monotonic()))

            surviving_threads = [thread for thread in candidate_threads if thread.is_alive()]
        except BaseException as error:
            failure = error
        finally:
            # Recheck after all joins and cleanup so threads that exited while a
            # later thread was being joined are not retained as false survivors.
            surviving_threads = [thread for thread in candidate_threads if thread.is_alive()]
            if failure is None and surviving_threads:
                failure = RuntimeError("Client stop incomplete: threads did not exit")
            with self.__lifecycle_lock:
                self.__is_started = bool(surviving_threads)
                self.__is_stopping = False
                self.__stopping_thread = None
                stop_result["error"] = str(failure) if failure is not None else None
                stop_complete.set()
            if failure is not None:
                _log.error("Stop incomplete: {0}".format(failure))
            else:
                _log.info("Stopped")
        if failure is not None:
            raise failure

    def get_stats(self) -> tuple[int, int, int]:
        with self.__stats_lock:
            data_count = self.__stats["data"]
            text_count = self.__stats["text"]
        return data_count, text_count, self.__data.qsize()
