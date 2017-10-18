import time
import base64
import requests
import threading
import websocket
import json
import logging
import queue

SELF_HEAL_TIME = 1
HEARTBEAT_TIME = 3
IEX = "iex"
QUODD = "quodd"
PROVIDERS = [IEX, QUODD]
MAX_QUEUE_SIZE = 10000

class IntrinioRealtimeClient:
    def __init__(self, options):
        if options is None:
            raise ValueError("Options parameter is required")
            
        self.options = options
        self.username = options['username']
        self.password = options['password']
        self.provider = options['provider']
        
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
        
        if not self.username:
            raise ValueError("Parameter 'username' must be specified") 
            
        if not self.password:
            raise ValueError("Parameter 'password' must be specified")
        
        if 'on_quote' in options:
            if not callable(options['on_quote']):
                raise ValueError("Parameter 'on_quote' must be a function")
            else:
                self.on_quote = options['on_quote']
        else:
            self.on_quote = None
        
        if self.provider not in PROVIDERS:
            raise ValueError(f"Parameter 'provider' is invalid, use one of {PROVIDERS}")
        
        self.ready = False
        self.token = None
        self.ws = None
        self.quote_receiver = None
        self.quote_handler = None
        self.joined_channels = set()
        self.last_queue_warning_time = 0
        
        QuoteHandler(self).start()
        Heartbeat(self).start()

    def auth_url(self):
        if self.provider == IEX:
            return "https://realtime.intrinio.com/auth"
        elif self.provider == QUODD:
            return "https://api.intrinio.com/token?type=QUODD"
        
    def websocket_url(self):
        if self.provider == IEX:
            return "wss://realtime.intrinio.com/socket/websocket?vsn=1.0.0&token=" + self.token
        elif self.provider == QUODD:
            return "wss://www5.quodd.com/websocket/webStreamer/intrinio/" + self.token
        
    def connect(self):
        self.logger.info("Connecting...")
        
        self.ready = False
        self.joined_channels = set()
        
        if self.ws:
            self.ws.close()
            time.sleep(1)
            
        try:
            self.refresh_token()
            self.refresh_websocket()
        except Exception as e:
            self.logger.error(f"Cannot connect: {e}")
            return self.self_heal()
            
    def disconnect(self):
        self.ready = False
        self.joined_channels = set()
        
        if self.ws:
            self.ws.close()
            time.sleep(1)
            
    def keep_alive(self):
        while True:
            pass

    def refresh_token(self):
        response = requests.get(self.auth_url(), auth=(self.username, self.password))
        
        if response.status_code != 200:
            raise RuntimeError("Auth failed")
            
        self.token = response.text
        self.logger.info("Authentication successful!")

    def refresh_websocket(self):
        self.quote_receiver = QuoteReceiver(self)
        self.quote_receiver.start()

    def self_heal(self):
        time.sleep(SELF_HEAL_TIME)
        self.connect()
            
    def on_connect(self):
        self.ready = True
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
            msg = self.join_message(channel)
            self.ws.send(json.dumps(msg))
            self.logger.info(f"Joined channel {channel}")
        
        # Leave old channels
        old_channels = self.joined_channels - self.channels
        self.logger.debug(f"Old channels: {old_channels}")
        for channel in old_channels:
            msg = self.leave_message(channel)
            self.ws.send(json.dumps(msg))
            self.logger.info(f"Left channel {channel}")
        
        self.joined_channels = self.channels.copy()
        self.logger.debug(f"Current channels: {self.joined_channels}")
        
    def join_message(self, channel):
        if self.provider == IEX:
            return {
                'topic': self.parse_iex_topic(channel),
                'event': 'phx_join',
                'payload': {},
                'ref': None
            }
        elif self.provider == QUODD:
            return {
                'event': 'subscribe',
                'data': {
                    'ticker': channel,
                    'action': 'subscribe'
                }
            }
            
    def leave_message(self, channel):
        if self.provider == IEX:
            return {
                'topic': self.parse_iex_topic(channel),
                'event': 'phx_leave',
                'payload': {},
                'ref': None
            }
        elif self.provider == QUODD:
            return {
                'event': 'unsubscribe',
                'data': {
                    'ticker': channel,
                    'action': 'unsubscribe'
                }
            }
            
    def parse_iex_topic(self, channel):
        if channel == "$lobby":
            return "iex:lobby"
        elif channel == "$lobby_last_price":
            return "iex:lobby:last_price"
        else:
            return f"iex:securities:{channel}"
        
class QuoteReceiver(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.client = client
        self.enabled = True

    def run(self):
        self.client.ws = websocket.WebSocketApp(
            self.client.websocket_url(), 
            on_open = self.on_open, 
            on_close = self.on_close,
            on_message = self.on_message, 
            on_error = self.on_error
        )
            
        self.client.logger.debug("QuoteReceiver ready")
        self.client.ws.run_forever()
        self.client.logger.debug("QuoteReceiver exiting")
        
    def on_open(self, ws):
        self.client.logger.info("Websocket opened!")
        if self.client.provider == IEX:
            self.client.on_connect()

    def on_close(self, ws):
        self.client.logger.info("Websocket closed!")

    def on_error(self, ws, error):
        self.client.logger.error(f"Websocket ERROR: {error}")
        self.client.self_heal()
        
    def on_message(self, ws, message):
        message = json.loads(message)
        self.client.logger.debug(f"Received message: {message}")
        quote = None
        
        if self.client.provider == IEX:
            if message['event'] == "quote":
                quote = message['payload']
        elif self.client.provider == QUODD:
            if message['event'] == 'info' and message['data']['message'] == 'Connected':
                self.client.on_connect()
            if message['event'] == 'quote' or message['event'] == 'trade':
                quote = message['data']
        
        if quote:
            try:
                self.client.quotes.put_nowait(quote)
            except queue.Full:
                self.client.on_queue_full()

class QuoteHandler(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.client = client

    def run(self):
        self.client.logger.debug("QuoteHandler ready")
        while True:
            item = self.client.quotes.get()
            backlog_len = self.client.quotes.qsize()
            if callable(self.client.on_quote):
                try:
                    self.client.on_quote(item, backlog_len)
                except Exception as e:
                    self.client.logger.error(e)
        
class Heartbeat(threading.Thread):
    def __init__(self, client):
        threading.Thread.__init__(self, args=(), kwargs=None)
        self.daemon = True
        self.client = client

    def run(self):
        self.client.logger.debug("Heartbeat ready")
        while True:
            time.sleep(HEARTBEAT_TIME)
            if self.client.ready and self.client.ws:
                msg = None
                
                if self.client.provider == IEX:
                    msg = {'topic': 'phoenix', 'event': 'heartbeat', 'payload': {}, 'ref': None}
                elif self.client.provider == QUODD:
                    msg = {'event': 'heartbeat', 'data': {'action': 'heartbeat', 'ticker': int(time.time()*1000)}}
                    
                if msg:
                    self.client.logger.debug(msg)
                    self.client.ws.send(json.dumps(msg))
                    self.client.logger.debug("Heartbeat!")
