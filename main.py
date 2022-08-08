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
# print(client.futures_create_order(symbol='ETHUSDT', side='BUY', type='LIMIT'))
# print(client.create_test_order(
#     symbol='ETHUSDT',
#     side='BUY',
#     type='LIMIT',
#     quantity=1,
#     timeInForce='GTC',
#     price=1600
# ))
# print(client.create_test_order(
#     symbol='ETHUSDT',
#     side='BUY',
#     type='MARKET',
#     quantity=0.01,
#     # timeInForce='GTC',
#     # price=1600
# ))
# print(
#     client.futures_order_book(symbol='ETHUSDT')
# )
print(client.futures_get_open_orders(symbol='ETHUSDT'))