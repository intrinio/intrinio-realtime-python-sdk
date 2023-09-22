import time
import datetime
import requests
import threading
import logging
import queue
import struct
import sys
import intrinio_sdk as intrinio
import tempfile
import os
import urllib.request

DEBUGGING = not (sys.gettrace() is None)


class IntrinioRealtimeConstants:
    REALTIME = "REALTIME"
    DELAYED_SIP = "DELAYED_SIP"
    NASDAQ_BASIC = "NASDAQ_BASIC"
    MANUAL = "MANUAL"
    PROVIDERS = [REALTIME, MANUAL, DELAYED_SIP, NASDAQ_BASIC]
    NO_PROVIDER = "NO_PROVIDER"
    NO_SUBPROVIDER = "NO_SUBPROVIDER"
    CTA_A = "CTA_A"
    CTA_B = "CTA_B"
    UTP = "UTP"
    OTC = "OTC"
    NASDAQ_BASIC = "NASDAQ_BASIC"
    IEX = "IEX"
    SUB_PROVIDERS = [NO_SUBPROVIDER, CTA_A, CTA_B, UTP, OTC, NASDAQ_BASIC, IEX]
    MAX_QUEUE_SIZE = 1000000
    EVENT_BUFFER_SIZE = 100


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


class Tick:
    def __init__(self, time_received, data):
        self.time_received = time_received
        self.data = data


class IntrinioReplayClient:
    def __init__(self, options, on_trade, on_quote):
        if options is None:
            raise ValueError("Options parameter is required")

        self.options = options
        self.api_key = options.get('api_key')
        self.provider = options.get('provider')
        self.tradesonly = options.get('tradesonly')
        self.replay_date = options.get('replay_date')
        self.with_simulated_delay = options.get('with_simulated_delay')
        self.delete_file_when_done = options.get('delete_file_when_done')
        self.worker_thread_count = options.get('worker_thread_count')

        if (self.worker_thread_count is None) or (type(self.worker_thread_count) is not int) or self.worker_thread_count < 1:
            self.worker_thread_count = 4

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
            if 'debug' in options and options['debug'] is True:
                self.logger.setLevel(logging.DEBUG)
            else:
                self.logger.setLevel(logging.INFO)
            self.logger.addHandler(log_handler)

        if 'max_queue_size' in options:
            self.events = queue.Queue(maxsize=options['max_queue_size'])
        else:
            self.events = queue.Queue(maxsize=IntrinioRealtimeConstants.MAX_QUEUE_SIZE)

        if self.api_key:
            if not self.valid_api_key(self.api_key):
                raise ValueError("API Key was formatted invalidly")
        else:
            raise ValueError("API key is required")

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

        if self.provider not in IntrinioRealtimeConstants.PROVIDERS:
            raise ValueError(f"Parameter 'provider' is invalid, use one of {IntrinioRealtimeConstants.PROVIDERS}")

        if ('replay_date' not in options) or (type(self.replay_date) is not datetime.date):
            raise ValueError(f"Parameter 'replay_date' is invalid, use a datetime.date.")

        if ('with_simulated_delay' not in options) or (type(self.with_simulated_delay) is not bool):
            raise ValueError(f"Parameter 'with_simulated_delay' is invalid, use a bool.")

        if ('delete_file_when_done' not in options) or (type(self.delete_file_when_done) is not bool):
            raise ValueError(f"Parameter 'delete_file_when_done' is invalid, use a bool.")

        self.file_parsing_thread = None
        self.quote_handling_threads = []
        self.joined_channels = set()
        self.last_queue_warning_time = 0

    @staticmethod
    def valid_api_key(api_key):
        if not isinstance(api_key, str):
            return False

        if api_key == "":
            return False

        return True

    def connect(self):
        try:
            self.logger.info("Connecting...")
            self.joined_channels = set()
            self.refresh_channels()
            self.file_parsing_thread = FileParsingThread(self)
            self.file_parsing_thread.start()
            self.quote_handling_threads = [None] * self.worker_thread_count
            for i in range(len(self.quote_handling_threads)):
                self.quote_handling_threads[i] = QuoteHandlingThread(self)
                self.quote_handling_threads[i].start()
        except Exception as e:
            self.logger.error(f"Cannot connect: {repr(e)}")

    def disconnect(self):
        self.joined_channels = set()
        if self.file_parsing_thread:
            self.file_parsing_thread.join()
        for thread in self.quote_handling_threads:
            thread.join()
        self.quote_handling_threads = []

    def on_queue_full(self):
        if time.time() - self.last_queue_warning_time > 1:
            self.logger.error("Quote queue is full! Dropped some new events")
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
        # Join new channels
        new_channels = self.channels - self.joined_channels
        self.logger.debug(f"New channels: {new_channels}")
        for channel in new_channels:
            self.logger.info(f"Joined channel {channel}")

        # Leave old channels
        old_channels = self.joined_channels - self.channels
        self.logger.debug(f"Old channels: {old_channels}")
        for channel in old_channels:
            self.logger.info(f"Left channel {channel}")

        self.joined_channels = self.channels.copy()
        self.logger.debug(f"Current channels: {self.joined_channels}")


class FileParsingThread(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.client = client
        self.enabled = True

    def run(self):
        self.client.logger.debug("FileParsingThread ready")
        file_paths = self.get_all_files()
        ticks_group = [None] * len(file_paths)
        for i in range(len(file_paths)):
            ticks_group[i] = self.replay_tick_file_without_delay(file_paths[i])

        aggregated_ticks = []
        if self.client.with_simulated_delay:
            aggregated_ticks = self.replay_file_group_with_delay(ticks_group)
        else:
            aggregated_ticks = self.replay_file_group_without_delay(ticks_group)

        for tick in aggregated_ticks:
            self.client.events.put_nowait(tick.data)

        if self.client.delete_file_when_done:
            for file_path in file_paths:
                if os.path.exists(file_path):
                    self.client.logger.info("Deleting file " + file_path)
                    os.remove(file_path)
        self.client.logger.debug("FileParsingThread exiting")

    @staticmethod
    def map_subprovider_to_api_value(sub_provider):
        match sub_provider:
            case IntrinioRealtimeConstants.IEX:
                return "iex"
            case IntrinioRealtimeConstants.UTP:
                return "utp_delayed"
            case IntrinioRealtimeConstants.CTA_A:
                return "cta_a_delayed"
            case IntrinioRealtimeConstants.CTA_B:
                return "cta_b_delayed"
            case IntrinioRealtimeConstants.OTC:
                return "otc_delayed"
            case IntrinioRealtimeConstants.NASDAQ_BASIC:
                return "nasdaq_basic"
            case _:
                return "iex"

    @staticmethod
    def map_provider_to_subproviders(provider):
        match provider:
            case IntrinioRealtimeConstants.NO_PROVIDER:
                return []
            case IntrinioRealtimeConstants.MANUAL:
                return []
            case IntrinioRealtimeConstants.REALTIME:
                return [IntrinioRealtimeConstants.IEX]
            case IntrinioRealtimeConstants.DELAYED_SIP:
                return [IntrinioRealtimeConstants.UTP, IntrinioRealtimeConstants.CTA_A, IntrinioRealtimeConstants.CTA_B,
                        IntrinioRealtimeConstants.OTC]
            case IntrinioRealtimeConstants.NASDAQ_BASIC:
                return [IntrinioRealtimeConstants.NASDAQ_BASIC]
            case _:
                return []

    def get_file(self, subprovider):
        intrinio.ApiClient().configuration.api_key['api_key'] = self.client.api_key
        intrinio.ApiClient().allow_retries(True)
        security_api = intrinio.SecurityApi()
        api_response = security_api.get_security_replay_file(self.map_subprovider_to_api_value(subprovider), self.client.replay_date)
        decoded_url = api_response.url.replace("\u0026", "&")
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, api_response.name)
        self.client.logger.info("Downloading file to " + file_path)
        urllib.request.urlretrieve(decoded_url, file_path)
        return file_path

    def get_all_files(self):
        subproviders = self.map_provider_to_subproviders(self.client.provider)
        file_names = []
        for subprovider in subproviders:
            try:
                file_names.append(self.get_file(subprovider))
            except Exception as e:
                self.client.logger.info("Could not retrieve file for " + subprovider)
        return file_names

    @staticmethod
    def read_file_chunk(file_obj, chunk_size):
        data = file_obj.read(chunk_size)
        if not data:
            return None
        return data

    @staticmethod
    def copy_into(source, destination, destination_start_index):
        for i in range(0, len(source)):
            destination[destination_start_index + i] = source[i]

    def replay_tick_file_without_delay(self, file_path):
        if os.path.exists(file_path):
            file = open(file_path, "rb")
            read_result = self.read_file_chunk(file, 1)
            while read_result is not None:
                event_bytes = [0] * IntrinioRealtimeConstants.EVENT_BUFFER_SIZE
                event_bytes[0] = 1  # This is the number of messages in the group
                event_bytes[1] = int.from_bytes(read_result, "big")  # This is message type
                event_bytes[2] = int.from_bytes(self.read_file_chunk(file, 1), "big")  # This is message length, including this and the previous byte.
                self.copy_into(self.read_file_chunk(file, event_bytes[2] - 2), event_bytes, 3)  # read the rest of the message
                time_received_bytes = self.read_file_chunk(file, 8)
                time_received = struct.unpack_from('<Q', time_received_bytes, 0)[0]
                yield Tick(time_received, bytearray(event_bytes))
                read_result = self.read_file_chunk(file, 1)
            file.close()
        else:
            yield None

    def replay_file_group_with_delay(self, all_ticks):
        multiplier = 1000000000
        start = datetime.datetime.now(datetime.timezone.utc).timestamp() * multiplier
        offset = 0
        for tick in self.replay_file_group_without_delay(all_ticks):
            if offset == 0:
                offset = start - tick.time_received

            # sleep until the tick happens
            if (tick.time_received + offset) <= datetime.datetime.now(datetime.timezone.utc).timestamp() * multiplier:
                sleep_time = (datetime.datetime.now(datetime.timezone.utc).timestamp() * multiplier - (tick.time_received + offset)) / multiplier
                time.sleep(sleep_time)
            yield tick

    @staticmethod
    def fill_next_ticks(enumerators, next_ticks):
        for i in range(len(next_ticks)):
            if next_ticks[i] is None:
                try:
                    next_ticks[i] = next(enumerators[i])
                except StopIteration:
                    pass

    @staticmethod
    def pull_next_tick(next_ticks):
        pull_index = 0
        t = 9223372036854775806  # max value
        for i in range(len(next_ticks)):
            if next_ticks[i] is not None and next_ticks[i].time_received < t:
                pull_index = i
                t = next_ticks[i].time_received

        pulled_tick = next_ticks[pull_index]
        next_ticks[pull_index] = None
        return pulled_tick

    @staticmethod
    def has_any_value(next_ticks):
        has_value = False
        for i in range(len(next_ticks)):
            if next_ticks[i] is not None:
                has_value = True
        return has_value

    def replay_file_group_without_delay(self, tick_group):
        next_ticks = [None] * len(tick_group)
        enumerators = [None] * len(tick_group)
        for i in range(len(tick_group)):
            enumerators[i] = tick_group[i]

        self.fill_next_ticks(enumerators, next_ticks)
        while self.has_any_value(next_ticks):
            next_tick = self.pull_next_tick(next_ticks)
            if next_tick is not None:
                yield next_tick
            self.fill_next_ticks(enumerators, next_ticks)

    def on_message(self, ws, message):
        try:
            if DEBUGGING:  # This is here for performance reasons so we don't use slow reflection on every message.
                self.client.logger.debug(f"Received message (hex): {message.hex()}")
            self.client.events.put_nowait(message)
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


class QuoteHandlingThread(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.client = client

    @staticmethod
    def parse_quote(quote_bytes, start_index):
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
                subprovider = IntrinioRealtimeConstants.NO_SUBPROVIDER
            case 1:
                subprovider = IntrinioRealtimeConstants.CTA_A
            case 2:
                subprovider = IntrinioRealtimeConstants.CTA_B
            case 3:
                subprovider = IntrinioRealtimeConstants.UTP
            case 4:
                subprovider = IntrinioRealtimeConstants.OTC
            case 5:
                subprovider = IntrinioRealtimeConstants.NASDAQ_BASIC
            case 6:
                subprovider = IntrinioRealtimeConstants.IEX
            case _:
                subprovider = IntrinioRealtimeConstants.IEX

        market_center = quote_bytes[(start_index + 4 + symbol_length):(start_index + 6 + symbol_length)].decode("utf-16")

        condition = ""
        if condition_length > 0:
            condition = quote_bytes[(start_index + 23 + symbol_length):(start_index + 23 + symbol_length + condition_length)].decode("ascii")

        return Quote(symbol, quote_type, price, size, timestamp, subprovider, market_center, condition)

    @staticmethod
    def parse_trade(trade_bytes, start_index):
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
                subprovider = IntrinioRealtimeConstants.NO_SUBPROVIDER
            case 1:
                subprovider = IntrinioRealtimeConstants.CTA_A
            case 2:
                subprovider = IntrinioRealtimeConstants.CTA_B
            case 3:
                subprovider = IntrinioRealtimeConstants.UTP
            case 4:
                subprovider = IntrinioRealtimeConstants.OTC
            case 5:
                subprovider = IntrinioRealtimeConstants.NASDAQ_BASIC
            case 6:
                subprovider = IntrinioRealtimeConstants.IEX
            case _:
                subprovider = IntrinioRealtimeConstants.IEX

        market_center = trade_bytes[(start_index + 4 + symbol_length):(start_index + 6 + symbol_length)].decode("utf-16")

        condition = ""
        if condition_length > 0:
            condition = trade_bytes[(start_index + 27 + symbol_length):(start_index + 27 + symbol_length + condition_length)].decode("ascii")

        return Trade(symbol, price, size, total_volume, timestamp, subprovider, market_center, condition)

    def subscribed(self, ticker):
        return 'lobby' in self.client.joined_channels or ticker in self.client.joined_channels

    def parse_message(self, bytes, start_index, backlog_len):
        message_type = bytes[start_index]
        message_length = bytes[start_index + 1]
        new_start_index = start_index + message_length
        item = None
        if message_type == 0:  # this is a trade
            item = self.parse_trade(bytes, start_index)
            if callable(self.client.on_trade) and self.subscribed(item.symbol):
                try:
                    self.client.on_trade(item, backlog_len)
                except Exception as e:
                    self.client.logger.error(repr(e))
        else:  # message_type is ask or bid (quote)
            if not self.client.tradesonly:
                item = self.parse_quote(bytes, start_index)
                if callable(self.client.on_quote) and self.subscribed(item.symbol):
                    try:
                        self.client.on_quote(item, backlog_len)
                    except Exception as e:
                        self.client.logger.error(repr(e))
        return new_start_index

    def run(self):
        self.client.logger.debug("QuoteHandlingThread ready")
        while True:
            message = self.client.events.get()
            backlog_len = self.client.events.qsize()
            items_in_message = message[0]
            start_index = 1
            for i in range(0, items_in_message):
                start_index = self.parse_message(message, start_index, backlog_len)
