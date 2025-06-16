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

_SELF_HEAL_BACKOFFS = [10, 30, 60, 300, 600]
_EMPTY_STRING = ""
_OPTIONS_TRADE_MESSAGE_SIZE = 72  # 61 used + 11 pad
_OPTIONS_QUOTE_MESSAGE_SIZE = 52  # 48 used + 4 pad
_OPTIONS_REFRESH_MESSAGE_SIZE = 52  # 44 used + 8 pad
_OPTIONS_UNUSUAL_ACTIVITY_MESSAGE_SIZE = 74  # 62 used + 12 pad
_NAN = float("NAN")

_stopFlag: threading.Event = threading.Event()
_dataMsgLock: threading.Lock = threading.Lock()
_dataMsgCount: int = 0
_txtMsgLock: threading.Lock = threading.Lock()
_txtMsgCount: int = 0
_logHandler: logging.Logger = logging.StreamHandler()
_logHandler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
_log: logging.Logger = logging.getLogger('intrinio_realtime_options')
_log.setLevel(logging.INFO)
_log.addHandler(_logHandler)

def log(message: str):
    _log.info(message)

def do_backoff(fn: Callable[[None], bool]):
    i: int = 0
    backoff: int = _SELF_HEAL_BACKOFFS[i]
    success: bool = fn()
    while (not success):
        time.sleep(backoff)
        i = min(i + 1, len(_SELF_HEAL_BACKOFFS) - 1)
        backoff = _SELF_HEAL_BACKOFFS[i]
        success = fn()

@unique
class Providers(IntEnum):
    OPRA = 1
    MANUAL = 2

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

class _WebSocket(websocket.WebSocketApp):
    def __init__(self,
                 ws_url: str,
                 ws_lock: threading.Lock,
                 worker_threads: list[threading.Thread],
                 get_channels: Callable[[None], set[tuple[str, bool]]],
                 get_token: Callable[[None], str],
                 get_url: Callable[[str], str],
                 use_on_trade: bool,
                 use_on_quote: bool,
                 use_on_refresh: bool,
                 use_on_ua: bool,
                 data_queue: queue.Queue):
        super().__init__(ws_url, on_open=self.__on_open, on_close=self.__on_close, on_data=self.__on_data, on_error=self.__on_error)
        self.__wsLock: threading.Lock = ws_lock
        self.__worker_threads: list[threading.Thread] = worker_threads
        self.__get_channels: Callable[[None], set[tuple[str, bool]]] = get_channels
        self.__get_token: Callable[[None], str] = get_token
        self.__get_url: Callable[[str], str] = get_url
        self.__use_on_trade: bool = use_on_trade
        self.__use_on_quote: bool = use_on_quote
        self.__use_on_refresh: bool = use_on_refresh
        self.__use_on_ua: bool = use_on_ua
        self.__data_queue: queue.Queue = data_queue
        self.__is_reconnecting: bool = False
        self.__last_reset: float = time.time()
        self.isReady: bool = False

    def __on_open(self, ws):
        _log.info("Websocket - Connected")
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
                        self.send_binary(message)

    def __try_reconnect(self) -> bool:
        _log.info("Websocket - Reconnecting...")
        if self.isReady:
            return True
        else:
            with self.__wsLock:
                self.__is_reconnecting = True
            token: str = self.__get_token(None)
            super().url = self.__get_url(token)
            self.start()
            return False

    def __on_close(self, ws, closeStatusCode, closeMsg):
        self.__wsLock.acquire()
        try:
            if (not self.__is_reconnecting):
                _log.info("Websocket - Closed - {0}: {1}".format(closeStatusCode, closeMsg))
                self.isReady = False
                if (not _stopFlag.is_set()):
                    do_backoff(self.__try_reconnect)
        finally:
            self.__wsLock.release()

    def __on_error(self, ws, error):
        _log.error("Websocket - Error - {0}".format(error))

    def __on_data(self, ws, data, code, continueFlag):
        if code == websocket.ABNF.OPCODE_BINARY:
            with _dataMsgLock:
                global _dataMsgCount
                _dataMsgCount += 1
            self.__data_queue.put_nowait(data)
        else:
            _log.debug("Websocket - Message received")
            with _txtMsgLock:
                global _txtMsgCount
                _txtMsgCount += 1
                _log.error("Error received: {0}".format(data))

    def start(self):
        super().run_forever(skip_utf8_validation=True)
        # super().run_forever(ping_interval = 5, ping_timeout = 2, skip_utf8_validation = True)

    def stop(self):
        super().close()

    def send(self, message: str):
        super().send(message, websocket.ABNF.OPCODE_TEXT)

    def send_binary(self, message: bytes):
        super().send(message, websocket.ABNF.OPCODE_BINARY)

    def reset(self):
        self.__last_reset = time.time()

class Config:
    def __init__(self, api_key: str, provider: Providers, num_threads: int = 4, log_level: LogLevel = LogLevel.INFO,
                 manual_ip_address: str = None, symbols: set[str] = None, delayed: bool = False):
        self.api_key: str = api_key
        self.provider: Providers = provider
        self.num_threads: int = num_threads
        self.manual_ip_address: str = manual_ip_address
        self.symbols: list[str] = symbols
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
               on_unusual_activity: Callable[[OptionsUnusualActivity], None] = None):
    _log.debug("Starting worker thread {0}".format(index))
    while not _stopFlag.is_set():
        try:
            datum: bytes = data.get(True, 1.0)
            count: int = datum[0]
            start_index: int = 1
            for _ in range(count):
                msg_type: int = datum[start_index + 22]
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
        if config.symbols and (isinstance(config.symbols, list)) and (len(config.symbols) > 0):
            self.__channels: set[str] = set((_transform_contract_to_new(symbol)) for symbol in config.symbols)
        else:
            self.__channels: set[str] = set()
        self.__data: queue.Queue = queue.Queue()
        self.__t_lock: threading.Lock = threading.Lock()
        self.__ws_lock: threading.Lock = threading.Lock()
        self.__worker_threads: list[threading.Thread] = [threading.Thread(None,
                                                                          _thread_fn,
                                                                          args=[i, self.__data, on_trade, on_quote, on_refresh, on_unusual_activity],
                                                                          daemon=True) for i in range(config.num_threads)]
        self.__socket_thread: threading.Thread = None
        self.__is_started: bool = False
        _log.setLevel(config.log_level)

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
        elif self.__provider == Providers.MANUAL:
            return "http://" + self.__manualIP + "/auth?api_key=" + self.__apiKey
        else:
            raise ValueError("Provider not specified")

    def __get_web_socket_url(self, token: str) -> str:
        delay: str = "&delayed=true" if self.__delayed else ""
        if self.__provider == Providers.OPRA:
            return "wss://realtime-options.intrinio.com/socket/websocket?vsn=1.0.0&token=" + token + delay
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
                do_backoff(self.__try_set_token)
            return self.__token[0]
        finally:
            self.__t_lock.release()

    def __get_channels(self) -> set[str]:
        return self.__channels

    def __join(self, symbol: str):
        transformed_symbol: str = _transform_contract_to_new(symbol)
        if transformed_symbol not in self.__channels:
            self.__channels.add(transformed_symbol)
            symbol_bytes = bytes(transformed_symbol, 'utf-8')
            message: bytes = bytearray(len(symbol_bytes)+2)
            message[0] = 74  # join code
            message[1] = _get_option_mask(self.__use_on_trade, self.__use_on_quote, self.__use_on_refresh, self.__use_on_unusual_activity)
            message[2:] = symbol_bytes
            if self.__webSocket.isReady:
                _log.info("Websocket - Joining channel: {0}".format(transformed_symbol))
                self.__webSocket.send_binary(message)

    def __leave(self, symbol: str):
        transformed_symbol: str = _transform_contract_to_new(symbol)
        if transformed_symbol in self.__channels:
            self.__channels.remove(transformed_symbol)
            symbol_bytes = bytes(transformed_symbol, 'utf-8')
            message: bytes = bytearray(len(symbol_bytes) + 2)
            message[0] = 76  # leave code
            message[1] = _get_option_mask(self.__use_on_trade, self.__use_on_quote, self.__use_on_refresh, self.__use_on_unusual_activity)
            message[2:] = symbol_bytes
            if self.__webSocket.isReady:
                _log.info("Websocket - Leaving channel: {0}".format(transformed_symbol))
                self.__webSocket.send_binary(message)

    def join(self, *symbols):
        if self.__is_started:
            while not self.__all_ready():
                time.sleep(1.0)
        for (symbol) in symbols:
            self.__join(symbol)

    def join_firehose(self):
        if "$FIREHOSE" in self.__channels:
            _log.warn("This client has already joined the firehose channel")
        else:
            if self.__is_started:
                while not self.__all_ready():
                    time.sleep(1.0)
            self.__join("$FIREHOSE")

    def leave(self, *symbols):
        if not symbols:
            _log.info("Leaving all channels")
            channels: set[str] = self.__channels.copy()
            for (symbol) in channels:
                self.__leave(symbol)
        symbol_set: set[str] = set(symbols)
        for sym in symbol_set:
            self.__leave(sym)

    def leave_firehose(self):
        if "$FIREHOSE" in self.__channels:
            self.__leave("$FIREHOSE")

    def __socket_start_fn(self, token: str):
        _log.info("Websocket - Connecting...")
        ws_url: str = self.__get_web_socket_url(token)
        self.__webSocket = _WebSocket(ws_url,
                                      self.__ws_lock,
                                      self.__worker_threads,
                                      self.__get_channels,
                                      self.__get_token,
                                      self.__get_web_socket_url,
                                      self.__use_on_trade,
                                      self.__use_on_quote,
                                      self.__use_on_refresh,
                                      self.__use_on_unusual_activity,
                                      self.__data)
        self.__webSocket.start()

    def start(self):
        if (not (self.__use_on_trade or self.__use_on_quote or self.__use_on_refresh or self.__use_on_unusual_activity)):
            raise ValueError("You must set at least one callback method before starting client")
        token: str = self.__get_token()
        self.__ws_lock.acquire()
        try:
            self.__socket_thread = threading.Thread = threading.Thread(None, self.__socket_start_fn, args=[token], daemon=True)
        finally:
            self.__ws_lock.release()
        self.__socket_thread.start()
        self.__is_started = True

    def stop(self):
        _log.info("Stopping...")
        if len(self.__channels) > 0:
            self.leave()
        time.sleep(1.0)
        self.__ws_lock.acquire()
        try:
            self.__webSocket.isReady = False
        finally:
            self.__ws_lock.release()
        _stopFlag.set()
        self.__webSocket.stop()
        for i in range(len(self.__worker_threads)):
            self.__worker_threads[i].join()
            _log.debug("Worker thread {0} joined".format(i))
        self.__socket_thread.join()
        _log.debug("Socket thread joined")
        _log.info("Stopped")

    def get_stats(self) -> tuple[int, int, int]:
        return _dataMsgCount, _txtMsgCount, self.__data.qsize()
