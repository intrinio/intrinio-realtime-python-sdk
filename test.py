from intriniorealtime.client import IntrinioRealtimeClient

def on_quote(quote, backlog): 
        print(quote, "| BACKLOG LENGTH:", backlog)
    
options = {
    'api_key': '',
    'provider': 'REALTIME',
    'on_quote': on_quote
}

client = IntrinioRealtimeClient(options)
client.join(['AAPL','GE','MSFT'])
#client.join(['lobby'])
client.connect()
client.keep_alive()