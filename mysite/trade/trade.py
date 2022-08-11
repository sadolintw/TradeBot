from binance import Client, ThreadedWebsocketManager, ThreadedDepthCacheManager
import json
import os

api_key = os.environ['BINANCE_APIKEY']
api_secret = os.environ['BINANCE_SECRETKEY']

client = Client(api_key, api_secret)
import sys

def get_usdt():
    print('test')
    balances = client.futures_account_balance()
    withdrawAvailableUSDT = 0
    for balance in balances:
        if balance['asset'] == 'USDT':
            withdrawAvailableUSDT = balance['withdrawAvailable']
    print('withdrawAvailableUSDT', withdrawAvailableUSDT)
    return withdrawAvailableUSDT

def create_order(symbol, side, quantity, entry, long_stop_loss, long_take_profit, short_stop_loss, short_take_profit):
    print(symbol, side, quantity, entry)

    stop_loss_side = 'SELL' if side == 'BUY' else 'BUY'
    stop_loss_stop_price = round((float(entry) * (100 - float(long_stop_loss)) / 100) if side == 'BUY' else (float(entry) * (100 + float(short_stop_loss)) / 100), 2)

    take_profit_side = 'SELL' if side == 'BUY' else 'BUY'
    take_profit_stop_price = round((float(entry) * (100 + float(long_take_profit)) / 100) if side == 'BUY' else (float(entry) * (100 - float(short_take_profit)) / 100), 2)

    batch_payload = [
        {
            # 'newClientOrderId': '467fba09-a286-43c3-a79a-32efec4be80e',
            'symbol': symbol,
            'type': 'LIMIT',
            'quantity': str(quantity),
            'side': side,
            'timeInForce': 'GTC',
            'price': entry
        },
        {
            # 'newClientOrderId': '6925e0cb-2d86-42af-875c-877da7b5fda5',
            'symbol': symbol,
            'type': 'STOP_MARKET',
            'quantity': str(quantity),
            'side': stop_loss_side,
            'stopPrice': str(stop_loss_stop_price),
            'timeInForce': 'GTE_GTC',
            'reduceOnly': 'True'
        },
        {
            # 'newClientOrderId': '121637a9-e15a-4f44-b62d-d424fb4870e0',
            'symbol': symbol,
            'type': 'TAKE_PROFIT_MARKET',
            'quantity': str(quantity),
            'side': take_profit_side,
            'stopPrice': str(take_profit_stop_price),
            'timeInForce': 'GTE_GTC',
            'reduceOnly': 'True'
        }
    ]
    print(json.dumps(batch_payload), '\r\n')
    # response = client.create_test_order(
    #     symbol=symbol,
    #     side=side,
    #     type='LIMIT',
    #     quantity=quantity,
    #     timeInForce='GTC',
    #     price=entry
    # )
    # response = client.futures_create_order(
    #     symbol=symbol,
    #     side=side,
    #     type='LIMIT',
    #     quantity=quantity,
    #     timeInForce='GTC',
    #     price=entry
    # )
    response = client.futures_place_batch_order(batchOrders=json.dumps(batch_payload))
    print(response)