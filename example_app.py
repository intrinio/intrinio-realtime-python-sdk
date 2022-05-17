import threading
import signal
import time
import sys
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
    'provider': 'REALTIME'
}


client = IntrinioRealtimeClient(options, on_trade, on_quote)
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

time.sleep(30)
# sigint, or ctrl+c, during the thread wait will also perform the same below code.
print("Stopping")
stop_event.set()
client.disconnect()
sys.exit(0)
