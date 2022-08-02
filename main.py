from binance import Client, ThreadedWebsocketManager, ThreadedDepthCacheManager

import os
import django

api_key=os.environ['BINANCE_APIKEY']
api_secret=os.environ['BINANCE_SECRETKEY']

client = Client(api_key, api_secret)

# print(client.futures_account_balance())
# print(client.get_account())
# print(client.get_margin_account())
# print(client.futures_account())
print(client.order)