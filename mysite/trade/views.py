import json
import math
import sys
import os
import uuid
import requests
import inspect
import time
from binance import Client, ThreadedWebsocketManager, ThreadedDepthCacheManager
from django.http import HttpResponse
# noinspection PyUnresolvedReferences
from rest_framework.decorators import api_view
from datetime import datetime

api_key = os.environ['BINANCE_APIKEY']
api_secret = os.environ['BINANCE_SECRETKEY']

client = Client(api_key, api_secret)
tradingview_passphase = os.environ['TRADINGVIEW_PASSPHASE']


def index(request):
    return HttpResponse("Hello, world. You're at the polls index.")


def wrap_str(_str):
    return "[" + _str + "]"


enable_all_api = False
enable_change_leverage = True
enable_get_usdt = True
enable_cancel_all_open_order = True
enable_get_position = True
enable_close_position = True
enable_close_position_at_price = True
enable_create_order = True
enable_send_telegram = True


def check_api_enable(is_api_enable):
    return enable_all_api & is_api_enable


@api_view(['GET', 'POST'])
def webhook(request):
    body_unicode = request.body.decode('utf-8')
    precision = 2
    percentage = 0.95
    # percentage = 0.1
    # preserve prev position exists less than
    preserve_prev_position_second = 30
    req_id = wrap_str(str(uuid.uuid1()).split("-")[0])
    print(req_id, wrap_str(inspect.stack()[0][3]), 'received signal: ', body_unicode)
    if body_unicode:
        try:
            signal = json.loads(body_unicode)
            if signal['passphrase'] == tradingview_passphase:
                print(req_id, wrap_str(inspect.stack()[0][3]), 'passphrase correct')
                signal_position_size = round(float(signal['position_size']), precision)
                signal_symbol = signal['ticker']
                signal_message_json = None
                signal_message = signal['message']
                if signal_message is not None:
                    signal_message_json = json.loads(signal_message)
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'signal message', signal_message_json)
                prev_quantity = 0
                prev_opposite_side = ''
                position = get_position(req_id=req_id, symbol=signal_symbol)
                allowed_close_position = False
                if position is not None:
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'position is not None')
                    prev_quantity = position['positionAmt']
                    prev_opposite_side = 'SELL' if float(prev_quantity) > 0 else (
                        '' if float(prev_quantity) == 0.0 else 'BUY')
                    prev_update_time = int(position['updateTime'])
                    now = datetime.now()
                    timestamp = datetime.timestamp(now) * 1000
                    # diff seconds
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'current timestamp', timestamp)
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'prev timestamp', prev_update_time)
                    diff = (timestamp - prev_update_time) / 1000
                    allowed_close_position = True if diff > preserve_prev_position_second else False

                print(req_id, wrap_str(inspect.stack()[0][3]), 'signal_position_size', signal_position_size)
                print(req_id, wrap_str(inspect.stack()[0][3]), 'allowed_close_position', allowed_close_position)

                # if signal position == 0, close position
                # and abs(float(prev_quantity)) > 0  ?
                if signal_position_size == 0 and allowed_close_position:
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'close signal')
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'close prev position')
                    close_position(req_id=req_id, symbol=signal_symbol, side=prev_opposite_side, quantity=prev_quantity)
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'send telegram message')

                    if check_api_enable(enable_send_telegram):
                        post_data = {
                            'symbol': signal_symbol,
                            'side': prev_opposite_side,
                            'msg': 'close prev position'
                        }
                        response = requests.post('http://127.0.0.1:5000/telegram', json=post_data)
                        content = response.content
                        print(req_id, wrap_str(inspect.stack()[0][3]), 'content', content)

                    print(req_id, wrap_str(inspect.stack()[0][3]), 'end')
                    return HttpResponse('received')

                print(req_id, wrap_str(inspect.stack()[0][3]), 'close prev open order')
                cancel_all_open_order(symbol=signal_symbol)
                time.sleep(1)

                # prepare param
                usdt = get_usdt(req_id=req_id)
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse entry')
                signal_entry = round(float(signal['entry']), precision)
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse side')
                signal_side = 'SELL' if signal['order'] == 'sell' else 'BUY'
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse long times')
                signal_long_times = int(signal['strategy']['long']['times'])
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse long stop loss')
                signal_long_stop_loss = signal['strategy']['long']['stopLoss']
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse long take profit')
                signal_long_take_profit = signal['strategy']['long']['takeProfit']
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse short times')
                signal_short_times = int(signal['strategy']['short']['times'])
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse short stop loss')
                signal_short_stop_loss = signal['strategy']['short']['stopLoss']
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse long take profit')
                signal_short_take_profit = signal['strategy']['short']['takeProfit']
                raw_quantity = 0 if usdt is None else math.floor(100 * float(usdt) * percentage / signal_entry) / 100

                # params override by message
                if signal_message_json is not None and 'type' in signal_message_json:
                    if signal_message_json['type'] == 'long_entry':
                        print(req_id, wrap_str(inspect.stack()[0][3]), 'parse long times from message')
                        signal_long_times = int(signal_message_json['lev'])
                    elif signal_message_json['type'] == 'short_entry':
                        print(req_id, wrap_str(inspect.stack()[0][3]), 'parse short times from message')
                        signal_short_times = int(signal_message_json['lev'])

                if signal_side == 'BUY':
                    change_leverage(signal_symbol, signal_long_times)
                else:
                    change_leverage(signal_symbol, signal_short_times)

                quantity = round(raw_quantity * int(signal_long_times if signal_side == 'BUY' else signal_short_times),
                                 precision)

                print(req_id, wrap_str(inspect.stack()[0][3]), 'raw_quantity', raw_quantity)
                print(req_id, wrap_str(inspect.stack()[0][3]), 'quantity', quantity)
                print(req_id, wrap_str(inspect.stack()[0][3]), 'signal_position_size', signal_position_size)

                stop_loss_stop_price = round(
                    (float(signal_entry) * (100 - float(signal_long_stop_loss)) / 100) if signal_side == 'BUY' else (
                            float(signal_entry) * (100 + float(signal_short_stop_loss)) / 100), precision)

                # params override by message
                if signal_message_json is not None and 'sl' in signal_message_json:
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'parse stop loss from message')
                    stop_loss_stop_price = round(float(signal_message_json['sl']), precision)

                take_profit_stop_price = round(
                    (float(signal_entry) * (100 + float(signal_long_take_profit)) / 100) if signal_side == 'BUY' else (
                            float(signal_entry) * (100 - float(signal_short_take_profit)) / 100), precision)

                create_order(
                    req_id,
                    signal_symbol,
                    signal_side,
                    quantity,
                    prev_quantity,
                    prev_opposite_side,
                    signal_entry,
                    'SELL' if signal_side == 'BUY' else 'BUY',
                    stop_loss_stop_price,
                    take_profit_stop_price
                )

                print(req_id, wrap_str(inspect.stack()[0][3]), 'send telegram message')
                post_data = {
                    'symbol': signal_symbol,
                    'entry': signal_entry,
                    'side': signal_side,
                    'msg': 'create order'
                }
                response = requests.post('http://127.0.0.1:5000/telegram', json=post_data)
                content = response.content
                print(req_id, wrap_str(inspect.stack()[0][3]), 'content', content)
            else:
                print(req_id, wrap_str(inspect.stack()[0][3]), 'passphrase incorrect')
                # send telegram msg
                # requests.get('http://127.0.0.1:5000/telegram')
        except:
            print(req_id, wrap_str(inspect.stack()[0][3]), "error:", sys.exc_info())
    else:
        print(req_id, wrap_str(inspect.stack()[0][3]), 'empty')

    print(req_id, wrap_str(inspect.stack()[0][3]), 'end')
    return HttpResponse('received')


def change_leverage(symbol, leverage):
    if not check_api_enable(enable_change_leverage):
        return None

    client.futures_change_leverage(symbol=symbol, leverage=leverage)


def get_usdt(req_id):
    if not check_api_enable(enable_get_usdt):
        return None

    balances = client.futures_account_balance()
    withdraw_available_usdt = 0
    for balance in balances:
        if balance['asset'] == 'USDT':
            withdraw_available_usdt = balance['withdrawAvailable']
    print(req_id, wrap_str(inspect.stack()[0][3]), withdraw_available_usdt)
    return withdraw_available_usdt


def cancel_all_open_order(symbol):
    if not check_api_enable(enable_cancel_all_open_order):
        return None

    client.futures_cancel_all_open_orders(symbol=symbol)


def get_position(req_id, symbol):
    if not check_api_enable(enable_get_position):
        return None

    print(req_id, wrap_str(inspect.stack()[0][3]))
    positions = client.futures_account()['positions']
    target = None
    for position in positions:
        if position['symbol'] == symbol:
            target = position
            print(req_id, wrap_str(inspect.stack()[0][3]), 'position', position)
            print(req_id, wrap_str(inspect.stack()[0][3]), 'has initial margin', float(position['initialMargin']) > 0)
            print(req_id, wrap_str(inspect.stack()[0][3]), 'leverage', position['leverage'])
            print(req_id, wrap_str(inspect.stack()[0][3]), 'quantity', position['positionAmt'])
            print(req_id, wrap_str(inspect.stack()[0][3]), 'opposite_side',
                  'SELL' if float(position['positionAmt']) > 0 else 'BUY')
            return target
    return None


def close_position(req_id, symbol, side, quantity):
    if not check_api_enable(enable_close_position):
        return None

    if side == '':
        return
    precision = 3
    print(req_id, wrap_str(inspect.stack()[0][3]), symbol, side, round(float(quantity), precision))
    if abs(float(quantity)) != 0.0:
        print(req_id, wrap_str(inspect.stack()[0][3]), 'has position')
        response = client.futures_create_order(
            symbol=symbol,
            type="MARKET",
            side=side,
            quantity=round(abs(float(quantity)), precision),
            reduceOnly='True'
        )
        print(req_id, wrap_str(inspect.stack()[0][3]), 'succ', response)
    else:
        print(req_id, wrap_str(inspect.stack()[0][3]), 'no position')


def close_position_at_price(req_id, symbol, side, stop_price):
    if not check_api_enable(enable_close_position_at_price):
        return None

    response = client.futures_create_order(
        symbol=symbol,
        # side='SELL' if side == 'BUY' else 'BUY',
        side=side,
        type='TAKE_PROFIT_MARKET',
        closePosition='True',
        stopPrice=stop_price
    )
    print(req_id, wrap_str(inspect.stack()[0][3]), response)


def create_order(
        req_id,
        symbol,
        side,
        quantity,
        prev_quantity,
        prev_opposite_side,
        entry,
        close_side,
        stop_loss_stop_price,
        take_profit_stop_price
):
    if not check_api_enable(enable_create_order):
        return None

    print(req_id, wrap_str(inspect.stack()[0][3]), 'create_order', symbol, side, quantity, entry)

    print(req_id, wrap_str(inspect.stack()[0][3]), 'close prev position')
    if abs(float(prev_quantity)) > 0.0:
        print(req_id, wrap_str(inspect.stack()[0][3]), 'has position')
        close_position(req_id=req_id, symbol=symbol, side=prev_opposite_side, quantity=prev_quantity)

    batch_payload = [
        {
            # 'newClientOrderId': '467fba09-a286-43c3-a79a-32efec4be80e',
            'symbol': symbol,
            'type': 'MARKET',  # or LIMIT
            'quantity': str(quantity),
            'side': side
            # 'timeInForce': 'GTC',
            # 'price': str(entry)
        },
        {
            # 'newClientOrderId': '6925e0cb-2d86-42af-875c-877da7b5fda5',
            'symbol': symbol,
            'type': 'STOP_MARKET',
            'quantity': str(quantity),
            'side': close_side,
            'stopPrice': str(stop_loss_stop_price),
            # 'timeInForce': 'GTE_GTC',
            'reduceOnly': 'True'
        },
        {
            # 'newClientOrderId': '121637a9-e15a-4f44-b62d-d424fb4870e0',
            'symbol': symbol,
            'type': 'TAKE_PROFIT_MARKET',
            'quantity': str(quantity),
            'side': close_side,
            'stopPrice': str(take_profit_stop_price),
            # 'timeInForce': 'GTE_GTC',
            'reduceOnly': 'True'
        }
    ]
    print(req_id, wrap_str(inspect.stack()[0][3]), 'batch order', json.dumps(batch_payload), '\r\n')
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
    print(req_id, wrap_str(inspect.stack()[0][3]), response)
