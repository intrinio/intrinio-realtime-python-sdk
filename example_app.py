import threading
from threading import Timer,Thread,Event
from intriniorealtime.client import IntrinioRealtimeClient

trade_count = 0
ask_count = 0
bid_count = 0
backlog_count = 0

def on_quote(quote, backlog): 
        global trade_count
        global ask_count
        global bid_count
        global backlog_count
        backlog_count = backlog
        if 'type' in quote.__dict__:
            if quote.type == "ask": ask_count += 1
            else: bid_count += 1
        else: trade_count += 1

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
    'api_key': '',
    'provider': 'REALTIME',
    'on_quote': on_quote
}

client = IntrinioRealtimeClient(options)
client.join(['AAPL','GE','MSFT'])
#client.join(['lobby'])
client.connect()
stopFlag = Event()
summarize_thread = Summarize(stopFlag)
summarize_thread.start()
client.keep_alive()
# this will stop the timer
stopFlag.set()