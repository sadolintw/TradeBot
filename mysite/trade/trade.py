from binance import Client, ThreadedWebsocketManager, ThreadedDepthCacheManager

import os

api_key = os.environ['BINANCE_APIKEY']
api_secret = os.environ['BINANCE_SECRETKEY']

client = Client(api_key, api_secret)
import sys

def get_usdt():
    print('test')
    balances = client.futures_account_balance()
    # withdrawAvailable = 0
    for balance in balances:
        if balance['asset'] == 'USDT':
            print(balance)
            # withdrawAvailable = balance['withdrawAvailable']

sys.modules[__name__] = get_usdt