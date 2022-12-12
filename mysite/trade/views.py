import json
import math
import sys
import os
import uuid
import requests
from binance import Client, ThreadedWebsocketManager, ThreadedDepthCacheManager
from django.http import HttpResponse
# noinspection PyUnresolvedReferences
from rest_framework.decorators import api_view
from datetime import datetime

api_key = os.environ['BINANCE_APIKEY']
api_secret = os.environ['BINANCE_SECRETKEY']

client = Client(api_key, api_secret)
tradingview_passphase = os.environ['TRADINGVIEW_PASSPHASE']

# from telegram.ext import *
# import telegram
# import os
# telegram_bot_access_token = os.environ['TELEGRAM_BOT_ACCESS_TOKEN']
# telegram_bot_chat_id = os.environ['TELEGRAM_BOT_CHAT_ID']
# bot = telegram.Bot(token=telegram_bot_access_token)
# bot.send_message(chat_id=telegram_bot_chat_id, text="""hi""")

def index(request):
    return HttpResponse("Hello, world. You're at the polls index.")


@api_view(['GET', 'POST'])
def webhook(request):
    body_unicode = request.body.decode('utf-8')
    precision = 2
    # percentage = 0.95
    percentage = 0.1
    # preserve prev position exists less than
    preserve_prev_position_second = 30
    req_id = "[" + str(uuid.uuid1()).split("-")[0] + "]"
    print(req_id, 'received signal: ', body_unicode)
    if body_unicode:
        try:
            signal = json.loads(body_unicode)
            if signal['passphase'] == tradingview_passphase:
                print(req_id, 'passphase correct')
                withdrawAvailableUSDT = get_usdt(req_id=req_id)
                entry = round(float(signal['entry']), precision)
                ticker = signal['ticker']
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
                print(req_id, 'raw_quantity', raw_quantity)
                print(req_id, 'quantity', quantity)
                print(req_id, 'position_size', position_size)
                prev_quantity = 0
                prev_opposite_side = ''
                position = get_position(req_id=req_id, symbol=ticker)
                allowed_close_position = False
                if position is not None:
                    prev_quantity = position['positionAmt']
                    prev_opposite_side = 'SELL' if float(position['positionAmt']) > 0 else 'BUY'
                    prev_update_time = int(position['updateTime'])
                    now = datetime.now()
                    timestamp = datetime.timestamp(now) * 1000
                    # diff seconds
                    diff = (timestamp - prev_update_time) / 1000
                    allowed_close_position = True if diff > preserve_prev_position_second else False
                if round(float(position_size), 3) == 0 and abs(float(prev_quantity)) > 0 and allowed_close_position:
                    print(req_id, 'no position size, close prev position')
                    close_position(req_id=req_id, symbol=ticker, side=prev_opposite_side, quantity=prev_quantity)
                    print(req_id, 'end')
                    return

                # create order by signal
                create_order(req_id, ticker, side, quantity, prev_quantity, prev_opposite_side, entry, long_stop_loss,
                             long_take_profit, short_stop_loss,
                             short_take_profit, precision)

                post_data = {
                    'symbol': ticker,
                    'entry': entry,
                    'side': side,
                }
                response = requests.post('http://127.0.0.1:5000/telegram', json=post_data)
                content = response.content
                print(req_id, 'content', content)
            else:
                print(req_id, 'passphase incorrect')
                #send telegram msg
                # requests.get('http://127.0.0.1:5000/telegram')
        except:
            print(req_id, "error:", sys.exc_info())
    else:
        print(req_id, 'empty')

    return HttpResponse('received')


def get_usdt(req_id):
    balances = client.futures_account_balance()
    withdrawAvailableUSDT = 0
    for balance in balances:
        if balance['asset'] == 'USDT':
            withdrawAvailableUSDT = balance['withdrawAvailable']
    print(req_id, 'withdrawAvailableUSDT', withdrawAvailableUSDT)
    return withdrawAvailableUSDT


def cancel_all_open_order(symbol):
    client.futures_cancel_all_open_orders(symbol=symbol)


def get_position(req_id, symbol):
    print(req_id, 'get_position')
    positions = client.futures_account()['positions']
    target = None
    for position in positions:
        if position['symbol'] == symbol:
            target = position
            print(req_id, 'position', position)
            print(req_id, 'has initial margin', float(position['initialMargin']) > 0)
            print(req_id, 'leverage', position['leverage'])
            print(req_id, 'quantity', position['positionAmt'])
            print(req_id, 'opposite_side', 'SELL' if float(position['positionAmt']) > 0 else 'BUY')
            return target
    return None


def close_position(req_id, symbol, side, quantity):
    precision = 3
    print(req_id, 'close_position', symbol, side, round(float(quantity), precision))
    if abs(float(quantity)) != 0.0:
        print(req_id, 'has position')
        response = client.futures_create_order(
            symbol=symbol,
            type="MARKET",
            side=side,
            quantity=round(abs(float(quantity)), precision),
            reduceOnly='True'
        )
        print(req_id, 'close_position succ', response)
    else:
        print(req_id, 'no position')


def close_position_at_price(req_id, symbol, side, stop_price):
    response = client.futures_create_order(
        symbol=symbol,
        # side='SELL' if side == 'BUY' else 'BUY',
        side=side,
        type='TAKE_PROFIT_MARKET',
        closePosition='True',
        stopPrice=stop_price
    )
    print(req_id, response)


def create_order(req_id, symbol, side, quantity, prev_quantity, prev_opposite_side, entry, long_stop_loss,
                 long_take_profit, short_stop_loss, short_take_profit,
                 precision):
    print(req_id, 'create_order', symbol, side, quantity, entry)

    print(req_id, 'cancel prev open order')
    cancel_all_open_order(symbol=symbol)
    print(req_id, 'close prev position')
    if abs(float(prev_quantity)) > 0.0:
        print(req_id, 'has position')
        close_position(req_id=req_id, symbol=symbol, side=prev_opposite_side, quantity=prev_quantity)

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
    print(req_id, 'batch order', json.dumps(batch_payload), '\r\n')
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
    print(req_id, response)
