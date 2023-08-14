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
import schedule
import time
import queue
import threading

notification_queue_entry = queue.Queue()
notification_queue_exit = queue.Queue()

api_key = os.environ['BINANCE_APIKEY']
api_secret = os.environ['BINANCE_SECRETKEY']

client = Client(api_key, api_secret)
tradingview_passphase = os.environ['TRADINGVIEW_PASSPHASE']


def index(request):
    return HttpResponse("Hello, world. You're at the polls index.")


def wrap_str(_str):
    return "[" + _str + "]"


enable_all_api = True
enable_change_leverage = True
enable_get_usdt = True
enable_cancel_all_open_order = True
enable_get_position = True
enable_close_position = True
enable_close_position_at_price = True
enable_create_order = True
enable_send_telegram = True


def handlerA():
    if not notification_queue_entry.empty():
        message = notification_queue_entry.get()
        print("Handling message:", message)
        notification_queue_entry.task_done()


def parse_type(notification):
    notification_json = json.loads(notification)
    notification_type = json.loads(notification_json['message'])
    return notification_type['type']


@api_view(['GET', 'POST'])
def message(request):
    req_id = wrap_str(str(uuid.uuid1()).split("-")[0])
    print(req_id + "message")
    plain = request.body.decode('utf-8')
    print("type: " + parse_type(plain))
    notification_queue_entry.put(plain)
    return HttpResponse('received')


##################

def check_api_enable(is_api_enable):
    return enable_all_api and is_api_enable


@api_view(['GET', 'POST'])
def webhook(request):
    print("receive notification")
    plain = request.body.decode('utf-8')
    notification_type = parse_type(plain)
    if notification_type == 'long_exit' or notification_type == 'short_exit':
        notification_queue_exit.put(plain)
    if notification_type == 'long_entry' or notification_type == 'short_entry':
        notification_queue_entry.put(plain)
    return HttpResponse('received')


def handle_webhook_entry_schedule():
    # print("handle_webhook_entry_schedule")
    if notification_queue_exit.empty() and not notification_queue_entry.empty():
        notification = notification_queue_entry.get()
        handle_webhook(notification)
        notification_queue_entry.task_done()


def handle_webhook_exit_schedule():
    # print("handle_webhook_exit_schedule")
    if not notification_queue_exit.empty():
        notification = notification_queue_exit.get()
        handle_webhook(notification)
        notification_queue_exit.task_done()


def handle_webhook(body_unicode):
    # body_unicode = request.body.decode('utf-8')
    close_position_delay = 2
    create_order_delay = 2
    precision = 2
    percentage = 0.95
    # percentage = 0.1
    # preserve prev position exists less than
    preserve_prev_position_second = 30
    req_id = wrap_str(str(uuid.uuid1()).split("-")[0])
    print(req_id, wrap_str(inspect.stack()[0][3]), 'received signal: ', body_unicode)
    if body_unicode:
        try:
            notification = json.loads(body_unicode)
            if notification['passphrase'] == tradingview_passphase:
                print(req_id, wrap_str(inspect.stack()[0][3]), 'passphrase correct')
                signal_position_size = round(float(notification['position_size']), precision)
                signal_symbol = notification['ticker']
                signal_message_json = None
                signal_message_type = None
                signal_message_lev = None
                signal_message = notification['message']
                if signal_message is not None:
                    signal_message_json = json.loads(signal_message)
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'signal message', signal_message_json)
                if signal_message_json is not None and 'type' in signal_message_json:
                    signal_message_type = signal_message_json['type']
                if signal_message_json is not None and 'lev' in signal_message_json:
                    signal_message_lev = signal_message_json['lev']
                prev_quantity = 0
                prev_opposite_side = ''
                allowed_close_position = False

                print(req_id, wrap_str(inspect.stack()[0][3]), 'check current position')
                time.sleep(close_position_delay)
                position = get_position(req_id=req_id, symbol=signal_symbol)
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
                    post_data = {
                        'symbol': signal_symbol,
                        'side': prev_opposite_side,
                        'type': signal_message_type,
                        'msg': 'close prev position'
                    }
                    send_telegram_message(req_id, post_data)

                    print(req_id, wrap_str(inspect.stack()[0][3]), 'end')
                    return HttpResponse('received')

                print(req_id, wrap_str(inspect.stack()[0][3]), 'close prev open order')
                cancel_all_open_order(symbol=signal_symbol)

                # handle exit signal
                if signal_message_type == 'long_exit' or signal_message_type == 'short_exit':
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'exit: ', signal_message_json['type'])
                    post_data = {
                        'symbol': signal_symbol,
                        'side': prev_opposite_side,
                        'type': signal_message_type,
                        'msg': 'close prev position'
                    }
                    send_telegram_message(req_id, post_data)
                    return HttpResponse('received')

                time.sleep(create_order_delay)
                # prepare param
                usdt = get_usdt(req_id=req_id)
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse entry')
                signal_entry = round(float(notification['entry']), precision)
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse side')
                signal_side = 'SELL' if notification['order'] == 'sell' else 'BUY'
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse long times')
                signal_long_times = int(notification['strategy']['long']['times'])
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse long stop loss')
                signal_long_stop_loss = notification['strategy']['long']['stopLoss']
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse long take profit')
                signal_long_take_profit = notification['strategy']['long']['takeProfit']
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse short times')
                signal_short_times = int(notification['strategy']['short']['times'])
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse short stop loss')
                signal_short_stop_loss = notification['strategy']['short']['stopLoss']
                print(req_id, wrap_str(inspect.stack()[0][3]), 'parse long take profit')
                signal_short_take_profit = notification['strategy']['short']['takeProfit']
                raw_quantity = 0 if usdt is None else math.floor(100 * float(usdt) * percentage / signal_entry) / 100

                # params override by message
                if signal_message_type == 'long_entry':
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'parse long leverage from message')
                    signal_long_times = int(signal_message_lev)
                elif signal_message_type == 'short_entry':
                    print(req_id, wrap_str(inspect.stack()[0][3]), 'parse short leverage from message')
                    signal_short_times = int(signal_message_lev)

                if signal_side == 'BUY':
                    change_leverage(req_id, signal_symbol, signal_long_times)
                else:
                    change_leverage(req_id, signal_symbol, signal_short_times)

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

                # entry exit
                post_data = {
                    'symbol': signal_symbol,
                    'entry': signal_entry,
                    'side': signal_side,
                    'type': signal_message_type,
                    'msg': 'create order'
                }
                send_telegram_message(req_id, post_data)

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


def send_telegram_message(req_id, post_data):
    if not check_api_enable(enable_send_telegram):
        return None

    print(req_id, wrap_str(inspect.stack()[0][3]), 'send telegram message')
    response = requests.post('http://127.0.0.1:5000/telegram', json=post_data)
    content = response.content
    print(req_id, wrap_str(inspect.stack()[0][3]), 'content', content)


def change_leverage(req_id, symbol, leverage):
    if not check_api_enable(enable_change_leverage):
        return None

    print(req_id, wrap_str(inspect.stack()[0][3]), 'change leverage', symbol, leverage)
    client.futures_change_leverage(symbol=symbol, leverage=leverage)


def get_usdt(req_id):
    if not check_api_enable(enable_get_usdt):
        return None

    balances = client.futures_account_balance()
    withdraw_available_usdt = 0
    for balance in balances:
        if balance['asset'] == 'USDT':
            withdraw_available_usdt = balance['availableBalance']
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
    print(req_id, wrap_str(inspect.stack()[0][3]), "create_order response ", response)


##############

def run_schedule():
    # 每 5 秒運行一次
    schedule.every(5).seconds.do(handle_webhook_entry_schedule)
    schedule.every(5).seconds.do(handle_webhook_exit_schedule)

    while True:
        schedule.run_pending()
        time.sleep(1)


# 在單獨的線程中運行定時任務
entry_schedule_thread = threading.Thread(target=run_schedule, daemon=True)
entry_schedule_thread.start()
