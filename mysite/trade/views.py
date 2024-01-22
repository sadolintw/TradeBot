import json
import math
import sys
import os
import uuid
from decimal import Decimal

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
from .utils import (
    strategy_list,
    find_strategy_by_passphrase,
    find_balance_by_strategy_id,
    create_trades_from_binance,
    format_decimal,
    get_main_account_info,
    convert_to_trade_array,
    query_trades_by_group_id,
    calculate_total_realized_pnl,
    update_account_balance,
    update_trade_profit_loss,
    get_monthly_rotating_logger
)

notification_queue_entry = queue.Queue()
notification_queue_exit = queue.Queue()

main_account = get_main_account_info()

api_key = main_account.api_key
api_secret = main_account.api_secret

client = Client(api_key, api_secret)
tradingview_passphase = os.environ['TRADINGVIEW_PASSPHASE']

logger = get_monthly_rotating_logger('log', '../logs')
logger.info('start')

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

exchange_info_map = {}

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


##################

# get exchange info
exchange_info = client.futures_exchange_info()
exchange_info_map
for symbol_info in exchange_info["symbols"]:
    symbol = symbol_info["symbol"]
    price_precision = symbol_info["pricePrecision"]
    quantity_precision = symbol_info["quantityPrecision"]
    exchange_info_map[symbol] = {
        "pricePrecision": price_precision,
        "quantityPrecision": quantity_precision
    }

# 打印結果
logger.info(exchange_info_map)
# 打印目前策略
strategy_list()

@api_view(['GET', 'POST'])
def webhook(request):
    logger.info("receive notification")
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
    percentage = 0.95
    # preserve prev position exists less than
    preserve_prev_position_second = 20
    req_id = wrap_str(str(uuid.uuid1()).split("-")[0])
    logger.info(f"{req_id} - received signal: {body_unicode}")
    if body_unicode:
        try:
            notification = json.loads(body_unicode)
            strategy = find_strategy_by_passphrase(notification['passphrase'])
            if strategy is not None:
                logger.info(f"{req_id} - passphrase correct")
                strategy_client = Client(strategy.account.api_key, strategy.account.api_secret)
                logger.info(f"{req_id} - strategy client {strategy_client}")
                signal_symbol = notification['ticker']
                _price_precision = int(exchange_info_map[signal_symbol]['pricePrecision'])
                _quantity_precision = int(exchange_info_map[signal_symbol]['quantityPrecision'])
                signal_position_size = round(float(notification['position_size']), _quantity_precision)
                signal_message_json = None
                signal_message_type = None
                signal_message_lev = None
                signal_message_eq = None
                signal_message = notification['message']
                if signal_message is not None:
                    signal_message_json = json.loads(signal_message)
                    logger.info(f"{req_id} - signal message {signal_message_json}")
                if signal_message_json is not None and 'type' in signal_message_json:
                    signal_message_type = signal_message_json['type']
                if signal_message_json is not None and 'lev' in signal_message_json:
                    signal_message_lev = signal_message_json['lev']
                if signal_message_json is not None and 'eq' in signal_message_json:
                    signal_message_eq = int(float(signal_message_json['eq']))
                    if signal_message_eq > 95:
                        signal_message_eq = 95
                    percentage = signal_message_eq / 100
                prev_quantity = 0
                prev_opposite_side = ''
                allowed_close_position = False

                logger.info(f"{req_id} - check current position")
                time.sleep(close_position_delay)
                position = get_position(req_id=req_id, strategy_client=strategy_client, symbol=signal_symbol)
                if position is not None:
                    logger.info(f"{req_id} - position is not None")
                    prev_quantity = position['positionAmt']
                    prev_opposite_side = 'SELL' if float(prev_quantity) > 0 else (
                        '' if float(prev_quantity) == 0.0 else 'BUY')
                    prev_update_time = int(position['updateTime'])
                    now = datetime.now()
                    timestamp = datetime.timestamp(now) * 1000
                    # diff seconds
                    logger.info(f"{req_id} - current timestamp {timestamp}")
                    logger.info(f"{req_id} - prev timestamp {prev_update_time}")
                    diff = (timestamp - prev_update_time) / 1000
                    allowed_close_position = True if diff > preserve_prev_position_second else False

                logger.info(f"{req_id} - signal_position_size {signal_position_size}")
                logger.info(f"{req_id} - allowed_close_position {allowed_close_position}")

                # if signal position == 0, close position
                # and abs(float(prev_quantity)) > 0  ?
                if signal_position_size == 0 and allowed_close_position:
                    logger.info(f"{req_id} - close signal")
                    logger.info(f"{req_id} - close prev open order for close signal")
                    cancel_all_open_order(symbol=signal_symbol, strategy_client=strategy_client)
                    logger.info(f"{req_id} - close prev position")
                    close_response = close_position(req_id=req_id, strategy_client=strategy_client, symbol=signal_symbol, side=prev_opposite_side, quantity=prev_quantity)
                    logger.info(f"{req_id} - close response {close_response}")

                    # 更新Strategy狀態
                    strategy.status = "INACTIVE"
                    strategy.save()

                    post_data = {
                        'symbol': signal_symbol,
                        'side': prev_opposite_side,
                        'type': signal_message_type,
                        'msg': 'close prev position'
                    }
                    try:
                        send_telegram_message(req_id, post_data)
                    except Exception as e:
                        logger.info(f"{req_id} - An error occurred while sending Telegram message: {e}")

                    trade_array = convert_to_trade_array(response=close_response, trade_type_override='EXIT')
                    create_trades_from_binance(binance_trades=trade_array, strategy_id=strategy.strategy_id, trade_group_id=strategy.trade_group_id)
                    order_list = query_trades_by_group_id(strategy.trade_group_id)

                    logger.info(f"{req_id} - order list {order_list}")
                    # 更新該策略的balace
                    logger.info(f"{req_id} - calculate total realized pnl")
                    total_realized_pnl = calculate_total_realized_pnl(
                        client.futures_account_trades(symbol=signal_symbol, limit=100), order_list)
                    logger.info(f"{req_id} - total_realized_pnl {total_realized_pnl}")
                    logger.info(f"{req_id} - {update_account_balance(total_realized_pnl, strategy.strategy_id)}")
                    # 更新已實現盈虧到出場紀錄
                    update_trade_profit_loss(strategy.trade_group_id, total_realized_pnl)
                    logger.info(f"{req_id} - end")
                    return HttpResponse('received')

                # handle exit signal
                if signal_message_type == 'long_exit' or signal_message_type == 'short_exit':
                    logger.info(f"{req_id} - exit: {signal_message_json['type']}")
                    post_data = {
                        'symbol': signal_symbol,
                        'side': prev_opposite_side,
                        'type': signal_message_type,
                        'msg': 'close prev position'
                    }
                    send_telegram_message(req_id, post_data)
                    return HttpResponse('received')

                logger.info(f"{req_id} - close prev open order for entry signal")
                cancel_all_open_order(symbol=signal_symbol, strategy_client=strategy_client)

                time.sleep(create_order_delay)
                # prepare param
                all_usdt = Decimal(get_usdt(req_id=req_id, strategy_client=strategy_client))
                balance = find_balance_by_strategy_id(strategy.strategy_id)

                if balance is not None:
                    usdt = balance.balance
                    logger.info(f"{req_id} - has corresponding balance {usdt}")
                    balance.equity = percentage
                    balance.save()
                    logger.info(f"{req_id} - update equity % {percentage}")
                else:
                    usdt = strategy.initial_capital

                if usdt > all_usdt:
                    logger.info(f"{req_id} - exceed all usdt")
                    usdt = all_usdt

                usdt = str(usdt)

                logger.info(f"{req_id} - used usdt {usdt}")

                logger.info(f"{req_id} - parse entry")
                signal_entry = round(float(notification['entry']), _price_precision)
                logger.info(f"{req_id} - parse side")
                signal_side = 'SELL' if notification['order'] == 'sell' else 'BUY'
                logger.info(f"{req_id} - parse long times")
                signal_long_times = int(notification['strategy']['long']['times'])
                logger.info(f"{req_id} - parse long stop loss")
                signal_long_stop_loss = notification['strategy']['long']['stopLoss']
                logger.info(f"{req_id} - parse long take profit")
                signal_long_take_profit = notification['strategy']['long']['takeProfit']
                logger.info(f"{req_id} - parse short times")
                signal_short_times = int(notification['strategy']['short']['times'])
                logger.info(f"{req_id} - parse short stop loss")
                signal_short_stop_loss = notification['strategy']['short']['stopLoss']
                logger.info(f"{req_id} - parse long take profit")
                signal_short_take_profit = notification['strategy']['short']['takeProfit']
                raw_quantity = 0 if usdt is None else math.floor(100 * float(usdt) * percentage / signal_entry) / 100

                # params override by message
                if signal_message_type == 'long_entry':
                    logger.info(f"{req_id} - parse long leverage from message")
                    signal_long_times = int(signal_message_lev)
                elif signal_message_type == 'short_entry':
                    logger.info(f"{req_id} - parse short leverage from message")
                    signal_short_times = int(signal_message_lev)

                if signal_side == 'BUY':
                    change_leverage(req_id, strategy_client, signal_symbol, signal_long_times)
                else:
                    change_leverage(req_id, strategy_client, signal_symbol, signal_short_times)

                quantity = round(raw_quantity * int(signal_long_times if signal_side == 'BUY' else signal_short_times),
                                 _quantity_precision)

                logger.info(f"{req_id} - raw_quantity {raw_quantity}")
                logger.info(f"{req_id} - quantity {quantity}")
                logger.info(f"{req_id} - signal_position_size {signal_position_size}")

                stop_loss_stop_price = round(
                    (float(signal_entry) * (100 - float(signal_long_stop_loss)) / 100) if signal_side == 'BUY' else (
                            float(signal_entry) * (100 + float(signal_short_stop_loss)) / 100), _price_precision)

                # params override by message
                if signal_message_json is not None and 'sl' in signal_message_json:
                    logger.info(f"{req_id} - parse stop loss from message")
                    stop_loss_stop_price = round(float(signal_message_json['sl']), _price_precision)

                take_profit_stop_price = round(
                    (float(signal_entry) * (100 + float(signal_long_take_profit)) / 100) if signal_side == 'BUY' else (
                            float(signal_entry) * (100 - float(signal_short_take_profit)) / 100), _price_precision)

                create_order(
                    req_id,
                    strategy_client,
                    strategy,
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
                logger.info(f"{req_id} - passphrase incorrect")
                # send telegram msg
                # requests.get('http://127.0.0.1:5000/telegram')
        except:
            logger.info(f"{req_id} - error:", sys.exc_info())
    else:
        logger.info(f"{req_id} - empty")

    logger.info(f"{req_id} - end")
    return HttpResponse('received')


def send_telegram_message(req_id, post_data):
    if not check_api_enable(enable_send_telegram):
        return None

    logger.info(f"{req_id} - send telegram message")
    response = requests.post('http://127.0.0.1:5000/telegram', json=post_data)
    content = response.content
    logger.info(f"{req_id} - content {content}")


def change_leverage(req_id, strategy_client, symbol, leverage):
    if not check_api_enable(enable_change_leverage):
        return None

    logger.info(f"{req_id} - change leverage, {symbol}, {leverage}")
    strategy_client.futures_change_leverage(symbol=symbol, leverage=leverage)


def get_usdt(req_id, strategy_client):
    if not check_api_enable(enable_get_usdt):
        return None

    balances = strategy_client.futures_account_balance()
    withdraw_available_usdt = 0
    for balance in balances:
        if balance['asset'] == 'USDT':
            withdraw_available_usdt = balance['availableBalance']
    logger.info(f"{req_id} - {withdraw_available_usdt}")
    return withdraw_available_usdt


def cancel_all_open_order(symbol, strategy_client):
    if not check_api_enable(enable_cancel_all_open_order):
        return None

    strategy_client.futures_cancel_all_open_orders(symbol=symbol)


def get_position(req_id, strategy_client, symbol):
    if not check_api_enable(enable_get_position):
        return None

    logger.info(f"{req_id} - start get position")
    positions = strategy_client.futures_account()['positions']
    target = None
    for position in positions:
        if position['symbol'] == symbol:
            target = position
            logger.info(f"{req_id} - position {position}")
            logger.info(f"{req_id} - has initial margin {float(position['initialMargin']) > 0}")
            logger.info(f"{req_id} - leverage {position['leverage']}")
            logger.info(f"{req_id} - quantity', {position['positionAmt']}")
            logger.info(f"{req_id} - opposite_side' {'SELL' if float(position['positionAmt']) > 0 else 'BUY'}")
            return target
    return None


def close_position(req_id, strategy_client, symbol, side, quantity):
    if not check_api_enable(enable_close_position):
        return None

    if side == '':
        return
    _quantity_precision = int(exchange_info_map[symbol]['quantityPrecision'])
    logger.info(f"{req_id} - {symbol}, {side}, {round(float(quantity), _quantity_precision)}")
    # cancel_order_response = client.futures_cancel_all_open_orders(symbol=symbol)
    # print('cancel_order_response', cancel_order_response)
    if abs(float(quantity)) != 0.0:
        logger.info(f"{req_id} - has position")
        response = strategy_client.futures_create_order(
            symbol=symbol,
            type="MARKET",
            side=side,
            quantity=round(abs(float(quantity)), _quantity_precision),
            reduceOnly='True'
        )
        logger.info(f"{req_id} - succ', {response}")
        return response
    else:
        logger.info(f"{req_id} - no position")


def close_position_at_price(req_id, strategy_client, symbol, side, stop_price):
    if not check_api_enable(enable_close_position_at_price):
        return None

    response = strategy_client.futures_create_order(
        symbol=symbol,
        # side='SELL' if side == 'BUY' else 'BUY',
        side=side,
        type='TAKE_PROFIT_MARKET',
        closePosition='True',
        stopPrice=stop_price
    )
    logger.info(f"{req_id} - {response}")


def create_order(
        req_id,
        strategy_client,
        strategy,
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
    logger.info(f"{req_id} - create_order, {symbol}, {side}, {quantity}, {entry}")
    logger.info(f"{req_id} - close prev position")
    if abs(float(prev_quantity)) > 0.0:
        logger.info(f"{req_id} - has position")
        close_position(req_id=req_id, symbol=symbol, side=prev_opposite_side, quantity=prev_quantity)
    _price_precision = int(exchange_info_map[symbol]['pricePrecision'])
    _quantity_precision = int(exchange_info_map[symbol]['quantityPrecision'])
    logger.info(f"{req_id} - quantity precision {_quantity_precision}")
    if (format_decimal(quantity, _quantity_precision)) == 0:
        logger.info(f"{req_id} - Unable to open a position: the quantity becomes 0 after precision adjustment")

    # 建立trade_group_id
    trade_group_id = uuid.uuid4()
    logger.info(f"{req_id} - trade_group_id {trade_group_id}")

    # 先計算前三個止盈點的訂單量
    quantity_level1 = format_decimal(quantity * (1 / 11), _quantity_precision)  # 1/(1+2+3+5) 的倉位大小
    quantity_level2 = format_decimal(quantity * (2 / 11), _quantity_precision)  # 2/(1+2+3+5) 的倉位大小
    quantity_level3 = format_decimal(quantity * (3 / 11), _quantity_precision)  # 3/(1+2+3+5) 的倉位大小

    # 初始化止盈點價格
    take_profit_price1 = take_profit_price2 = take_profit_price3 = take_profit_price4 = 0

    # 計算兩價格之間的差值
    price_difference = take_profit_stop_price - entry
    quarter_difference = abs(price_difference / 4)  # 取絕對值確保quarter_difference總是正值

    if side == 'BUY':
        take_profit_price1 = format_decimal(entry + quarter_difference, _price_precision)  # 第1/4的位置
        take_profit_price2 = format_decimal(entry + 2 * quarter_difference, _price_precision)  # 第2/4的位置
        take_profit_price3 = format_decimal(entry + 3 * quarter_difference, _price_precision)  # 第3/4的位置
        take_profit_price4 = format_decimal(take_profit_stop_price, _price_precision)  # 第4/4的位置，即take_profit_stop_price
    elif side == 'SELL':
        take_profit_price1 = format_decimal(entry - quarter_difference, _price_precision)  # 第1/4的位置
        take_profit_price2 = format_decimal(entry - 2 * quarter_difference, _price_precision)  # 第2/4的位置
        take_profit_price3 = format_decimal(entry - 3 * quarter_difference, _price_precision)  # 第3/4的位置
        take_profit_price4 = format_decimal(take_profit_stop_price, _price_precision)  # 第4/4的位置，即take_profit_stop_price

    # 止損價格
    _stop_loss_stop_price = format_decimal(stop_loss_stop_price, _price_precision)

    # batch order 上限為5 因此分兩次開單
    # 構造訂單 1
    batch_payload = [
        # 市價入場
        {
            'symbol': symbol,
            'type': 'MARKET',
            'quantity': format_decimal(quantity, _quantity_precision),
            'side': side
        },
        # 第四止盈點 (全部平倉)
        {
            'symbol': symbol,
            'type': 'TAKE_PROFIT_MARKET',
            'quantity': '0',
            'closePosition': 'true',
            'side': close_side,
            'priceProtect': 'true',
            'workingType': 'MARK_PRICE',
            'stopPrice': take_profit_price4
        },
        # 倉位止損
        {
            'symbol': symbol,
            'type': 'STOP_MARKET',
            'quantity': '0',
            'closePosition': 'true',
            'side': close_side,
            'priceProtect': 'true',
            'workingType': 'MARK_PRICE',
            'stopPrice': _stop_loss_stop_price
        }
    ]

    logger.info(f"{req_id} - batch order 1', {json.dumps(batch_payload)}")
    if check_api_enable(enable_create_order):
        response = strategy_client.futures_place_batch_order(batchOrders=json.dumps(batch_payload))
        # 創建Trade實例
        create_trades_from_binance(binance_trades=response, strategy_id=strategy.strategy_id, trade_group_id=trade_group_id)
        # 更新Strategy狀態
        strategy.status = "ACTIVE"
        strategy.trade_group_id = trade_group_id
        strategy.save()
        logger.info(f"{req_id} - create_order response 1 {response}")

    time.sleep(2)

    if float(format_decimal(float(quantity_level1), _quantity_precision)) == 0:
        quantity_message = f"Unable to open a position: the quantity becomes 0 after precision adjustment"
        logger.info(f"{req_id} - {quantity_message}")
        return

    # 構造訂單 2
    batch_payload = [
        # 第一止盈點
        {
            'symbol': symbol,
            'type': 'TAKE_PROFIT_MARKET',
            'quantity': quantity_level1,
            'side': close_side,
            'reduceOnly': 'true',
            'priceProtect': 'true',
            'workingType': 'MARK_PRICE',
            'stopPrice': take_profit_price1
        },
        # 第二止盈點
        {
            'symbol': symbol,
            'type': 'TAKE_PROFIT_MARKET',
            'quantity': quantity_level2,
            'side': close_side,
            'reduceOnly': 'true',
            'priceProtect': 'true',
            'workingType': 'MARK_PRICE',
            'stopPrice': take_profit_price2
        },
        # 第三止盈點
        {
            'symbol': symbol,
            'type': 'TAKE_PROFIT_MARKET',
            'quantity': quantity_level3,
            'side': close_side,
            'reduceOnly': 'true',
            'priceProtect': 'true',
            'workingType': 'MARK_PRICE',
            'stopPrice': take_profit_price3
        }
    ]

    logger.info(f"{req_id} - batch order 2, {json.dumps(batch_payload)}")
    if check_api_enable(enable_create_order):
        response = strategy_client.futures_place_batch_order(batchOrders=json.dumps(batch_payload))
        # 創建Trade實例
        create_trades_from_binance(binance_trades=response, strategy_id=strategy.strategy_id, trade_group_id=trade_group_id)
        logger.info(f"{req_id} - create_order response 2 {response}")


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
