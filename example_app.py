import threading
import signal
import time
import sys
from threading import Event
from intriniorealtime.client import IntrinioRealtimeClient
from intriniorealtime.replay_client import IntrinioReplayClient
from intriniorealtime.client import Quote

trade_count = 0
ask_count = 0
bid_count = 0
backlog_count = 0

def on_quote(quote, backlog):
        global ask_count
        global bid_count
        global backlog_count
        backlog_count = backlog
        if isinstance(quote, Quote) and 'type' in quote.__dict__:
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


options = {
    'api_key': 'API_KEY_HERE',
    'provider': 'REALTIME',  # 'REALTIME' or DELAYED_SIP or NASDAQ_BASIC
    # ,'replay_date': datetime.date.today() - datetime.timedelta(days=1)  # needed for ReplayClient. The date to replay.
    # ,'with_simulated_delay': False  # needed for ReplayClient. This plays back the events at the same rate they happened in market.
    # ,'delete_file_when_done': True  # needed for ReplayClient
    # ,'write_to_csv': False  # needed for ReplayClient
    # ,'csv_file_path': 'data.csv'  # needed for ReplayClient
    # 'bypass_parsing': True # if you want to handle parsing yourself, set this to True. Otherwise, leave it alone.
}


client = IntrinioRealtimeClient(options, on_trade, on_quote)
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
