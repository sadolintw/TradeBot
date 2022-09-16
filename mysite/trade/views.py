import json
import math
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
    precision = 2
    percentage = 0.95
    # percentage = 0.1
    if body_unicode:
        try:
            signal = json.loads(body_unicode)
            if signal['passphase'] == tradingview_passphase:
                withdrawAvailableUSDT = get_usdt()
                entry = round(float(signal['entry']), precision)
                ticker = signal['ticker']
                # side = signal['order'] == 'sell' ? 'SELL' : 'BUY'
                side = 'SELL' if signal['order'] == 'sell' else 'BUY'
                long_times = signal['strategy']['long']['times']
                long_stop_loss = signal['strategy']['long']['stopLoss']
                long_take_profit = signal['strategy']['long']['takeProfit']
                short_times = signal['strategy']['short']['times']
                short_stop_loss = signal['strategy']['short']['stopLoss']
                short_take_profit = signal['strategy']['short']['takeProfit']
                position_size = round(float(signal['position_size']), precision)
                raw_quantity = math.floor(100 * float(withdrawAvailableUSDT) * percentage / entry) / 100
                quantity = round(raw_quantity * int(long_times if side == 'BUY' else short_times), precision)
                print('quantity', quantity)
                has_position = check_position(symbol=ticker)
                # TODO close position and order (stop or take profit)
                if int(position_size) == 0:
                    return

                # handling current position
                # if has_position or position_size==0:
                #     print('has position', has_position, 'position size', position_size)
                # close_position(symbol=ticker, side=side, stop_price=entry)

                # create order by signal
                create_order(ticker, side, quantity, entry, long_stop_loss, long_take_profit, short_stop_loss,
                             short_take_profit, precision)
        except:
            print("error:", sys.exc_info())
    else:
        print('empty')

    return HttpResponse('received')


def get_usdt():
    balances = client.futures_account_balance()
    withdrawAvailableUSDT = 0
    for balance in balances:
        if balance['asset'] == 'USDT':
            withdrawAvailableUSDT = balance['withdrawAvailable']
    print('withdrawAvailableUSDT', withdrawAvailableUSDT)
    return withdrawAvailableUSDT


def create_order(symbol, side, quantity, entry, long_stop_loss, long_take_profit, short_stop_loss, short_take_profit, precision):
    print(symbol, side, quantity, entry)

    stop_loss_side = 'SELL' if side == 'BUY' else 'BUY'
    stop_loss_stop_price = round((float(entry) * (100 - float(long_stop_loss)) / 100) if side == 'BUY' else (
            float(entry) * (100 + float(short_stop_loss)) / 100), precision)

    take_profit_side = 'SELL' if side == 'BUY' else 'BUY'
    take_profit_stop_price = round((float(entry) * (100 + float(long_take_profit)) / 100) if side == 'BUY' else (
            float(entry) * (100 - float(short_take_profit)) / 100), precision)

    batch_payload = [
        {
            # 'newClientOrderId': '467fba09-a286-43c3-a79a-32efec4be80e',
            'symbol': symbol,
            'type': 'LIMIT',
            'quantity': str(quantity),
            'side': side,
            'timeInForce': 'GTC',
            'price': str(entry)
        },
        {
            # 'newClientOrderId': '6925e0cb-2d86-42af-875c-877da7b5fda5',
            'symbol': symbol,
            'type': 'STOP_MARKET',
            'quantity': str(quantity),
            'side': stop_loss_side,
            'stopPrice': str(stop_loss_stop_price),
            # 'timeInForce': 'GTE_GTC',
            'reduceOnly': 'True'
        },
        {
            # 'newClientOrderId': '121637a9-e15a-4f44-b62d-d424fb4870e0',
            'symbol': symbol,
            'type': 'TAKE_PROFIT_MARKET',
            'quantity': str(quantity),
            'side': take_profit_side,
            'stopPrice': str(take_profit_stop_price),
            # 'timeInForce': 'GTE_GTC',
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


def check_position(symbol):
    positions = client.futures_account()['positions']
    target = None
    for position in positions:
        if position['symbol'] == symbol:
            target = position
            print('has initial margin', float(position['initialMargin']) > 0)
            print('leverage', position['leverage'])
            return True
    return False


def close_position(symbol, side, stop_price):
    response = client.futures_create_order(
        symbol=symbol,
        # side='SELL' if side == 'BUY' else 'BUY',
        side=side,
        type='TAKE_PROFIT_MARKET',
        closePosition='True',
        stopPrice=stop_price
    )
    print(response)
