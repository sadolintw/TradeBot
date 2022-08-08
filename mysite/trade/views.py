import json
import sys
import os
from binance import Client, ThreadedWebsocketManager, ThreadedDepthCacheManager
from django.http import HttpResponse
# noinspection PyUnresolvedReferences
from rest_framework.decorators import api_view

api_key = os.environ['BINANCE_APIKEY']
api_secret = os.environ['BINANCE_SECRETKEY']

client = Client(api_key, api_secret)
tradingview_passphase = os.environ['TRADINGVIEW_PASSPHASE']


def index(request):
    return HttpResponse("Hello, world. You're at the polls index.")

@api_view(['GET', 'POST'])
def webhook(request):
    body_unicode = request.body.decode('utf-8')
    print('received signal: ', body_unicode)
    if body_unicode:
        try:
            signal = json.loads(body_unicode)
            if signal['passphase'] == tradingview_passphase:
                withdrawAvailableUSDT = get_usdt()
                entry = signal['entry']
                ticker = signal['ticker']
                # side = signal['order'] == 'sell' ? 'SELL' : 'BUY'
                side = 'SELL' if signal['order'] == 'sell' else 'BUY'
                long_times = signal['strategy']['long']['times']
                long_stop_loss = signal['strategy']['long']['stopLoss']
                long_take_profit = signal['strategy']['long']['takeProfit']
                short_times = signal['strategy']['short']['times']
                short_stop_loss = signal['strategy']['short']['stopLoss']
                short_take_profit = signal['strategy']['short']['takeProfit']
                quantity = round(float(withdrawAvailableUSDT)*0.98/float(entry), 2) * int(long_times if side=='BUY' else short_times)
                create_order(ticker, side, quantity, entry, long_stop_loss, long_take_profit, short_stop_loss, short_take_profit)
        except:
            print("error:", sys.exc_info())
    else:
        print('empty')

    return HttpResponse('received')


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