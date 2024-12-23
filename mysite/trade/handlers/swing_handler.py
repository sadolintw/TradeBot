import json
import time
import uuid
import math
from datetime import datetime
from binance import Client
from django.http import HttpResponse
import logging
import requests
from decimal import Decimal

from ..utils import (
    create_trades_from_binance,
    update_account_balance,
    update_trade_profit_loss,
    query_trades_by_group_id,
    calculate_total_realized_pnl,
    format_decimal,
    find_balance_by_strategy_id
)

from ..views import (
    send_telegram_message,
    change_leverage,
    get_usdt,
    cancel_all_open_order,
    get_position,
    close_position,
    close_position_at_price
)

logger = logging.getLogger('MyLogger')

# 常數設定
enable_all_api = True
enable_change_leverage = True
enable_get_usdt = True
enable_cancel_all_open_order = True
enable_get_position = True
enable_close_position = True
enable_close_position_at_price = True
enable_create_order = True
enable_send_telegram = True

close_position_delay = 2
create_order_delay = 2
get_account_trade_delay = 2
percentage = 0.95
preserve_prev_position_second = 20

def check_api_enable(is_api_enable):
    return enable_all_api and is_api_enable

def handle_swing_notification(req_id, strategy, notification):
    """
    處理 swing 交易通知
    已棄用 - 請使用 handle_swing_notification2
    """
    signal_message = notification['message']
    signal_message_json = json.loads(signal_message) if signal_message != '' else None
    signal_message_type = signal_message_json['type'] if signal_message_json is not None else None
    signal_message_lev = signal_message_json['lev'] if signal_message_json is not None else None
    signal_symbol = notification['ticker']
    signal_position_size = round(float(notification['position_size']), 2)

    strategy_client = Client(strategy.account.api_key, strategy.account.api_secret)
    _price_precision = int(exchange_info_map[signal_symbol]['pricePrecision'])

    position = get_position(req_id=req_id, strategy_client=strategy_client, symbol=signal_symbol)
    prev_quantity = 0
    prev_opposite_side = ''
    if position is not None:
        prev_quantity = position['positionAmt']
        prev_opposite_side = 'SELL' if float(prev_quantity) > 0 else 'BUY'

    if signal_message_type == 'long_exit' or signal_message_type == 'short_exit':
        logger.info(f"{req_id} - close position")
        if position is not None:
            logger.info(f"{req_id} - has position")
            close_position(req_id=req_id, strategy_client=strategy_client, symbol=signal_symbol, side=prev_opposite_side,
                         quantity=prev_quantity)
            time.sleep(get_account_trade_delay)
            # 更新該策略的balace
            order_list = query_trades_by_group_id(strategy.trade_group_id)
            logger.info(f"{req_id} - order list {order_list}")
            total_realized_pnl = calculate_total_realized_pnl(
                client.futures_account_trades(symbol=signal_symbol, limit=100), order_list)
            logger.info(f"{req_id} - total_realized_pnl {total_realized_pnl}")
            logger.info(f"{req_id} - {update_account_balance(total_realized_pnl, strategy.strategy_id)}")
            # 更新已實現盈虧到出場紀錄
            update_trade_profit_loss(strategy.trade_group_id, total_realized_pnl)
            logger.info(f"{req_id} - end")
            return HttpResponse('received')
        else:
            logger.info(f"{req_id} - no position")
            return HttpResponse('received')

    if signal_position_size == 0:
        logger.info(f"{req_id} - close position")
        if position is not None:
            logger.info(f"{req_id} - has position")
            close_position(req_id=req_id, strategy_client=strategy_client, symbol=signal_symbol, side=prev_opposite_side,
                         quantity=prev_quantity)
            time.sleep(get_account_trade_delay)
            # 更新該策略的balace
            order_list = query_trades_by_group_id(strategy.trade_group_id)
            logger.info(f"{req_id} - order list {order_list}")
            total_realized_pnl = calculate_total_realized_pnl(
                client.futures_account_trades(symbol=signal_symbol, limit=100), order_list)
            logger.info(f"{req_id} - total_realized_pnl {total_realized_pnl}")
            logger.info(f"{req_id} - {update_account_balance(total_realized_pnl, strategy.strategy_id)}")
            # 新增EXIT紀錄
            exit_data = {
                'symbol': signal_symbol,
                'side': prev_opposite_side,
                'type': 'EXIT'
            }
            create_trades_from_binance(binance_trades=[exit_data], strategy_id=strategy.strategy_id,
                                       trade_group_id=strategy.trade_group_id)
            # 更新Strategy狀態
            strategy.status = "INACTIVE"
            strategy.save()
            # 更新已實現盈虧到出場紀錄
            update_trade_profit_loss(strategy.trade_group_id, total_realized_pnl)
            logger.info(f"{req_id} - end")
            return HttpResponse('received')
        else:
            logger.info(f"{req_id} - no position")
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
    raw_quantity = 0 if usdt is None else math.floor(100000 * float(usdt) * percentage / signal_entry) / 100000

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

    create_swing_order(
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

def handle_swing_notification2(req_id, strategy, notification):
    """
    處理 swing 交易通知的改進版本
    """
    signal_message = notification['message']
    signal_message_json = json.loads(signal_message) if signal_message != '' else None
    signal_message_type = signal_message_json['type'] if signal_message_json is not None else None
    signal_message_lev = signal_message_json['lev'] if signal_message_json is not None else None
    signal_symbol = notification['ticker']
    signal_position_size = round(float(notification['position_size']), 2)

    strategy_client = Client(strategy.account.api_key, strategy.account.api_secret)
    _price_precision = int(exchange_info_map[signal_symbol]['pricePrecision'])

    position = get_position(req_id=req_id, strategy_client=strategy_client, symbol=signal_symbol)
    prev_quantity = 0
    prev_opposite_side = ''
    if position is not None:
        prev_quantity = position['positionAmt']
        prev_opposite_side = 'SELL' if float(prev_quantity) > 0 else 'BUY'

    if signal_message_type == 'long_exit' or signal_message_type == 'short_exit':
        logger.info(f"{req_id} - close position")
        if position is not None:
            logger.info(f"{req_id} - has position")
            close_position(req_id=req_id, strategy_client=strategy_client, symbol=signal_symbol, side=prev_opposite_side,
                         quantity=prev_quantity)
            time.sleep(get_account_trade_delay)
            # 更新該策略的balace
            order_list = query_trades_by_group_id(strategy.trade_group_id)
            logger.info(f"{req_id} - order list {order_list}")
            total_realized_pnl = calculate_total_realized_pnl(
                client.futures_account_trades(symbol=signal_symbol, limit=100), order_list)
            logger.info(f"{req_id} - total_realized_pnl {total_realized_pnl}")
            logger.info(f"{req_id} - {update_account_balance(total_realized_pnl, strategy.strategy_id)}")
            # 更新已實現盈虧到出場紀錄
            update_trade_profit_loss(strategy.trade_group_id, total_realized_pnl)
            logger.info(f"{req_id} - end")
            return HttpResponse('received')
        else:
            logger.info(f"{req_id} - no position")
            return HttpResponse('received')

    if signal_position_size == 0:
        logger.info(f"{req_id} - close position")
        if position is not None:
            logger.info(f"{req_id} - has position")
            close_position(req_id=req_id, strategy_client=strategy_client, symbol=signal_symbol, side=prev_opposite_side,
                         quantity=prev_quantity)
            time.sleep(get_account_trade_delay)
            # 更新該策略的balace
            order_list = query_trades_by_group_id(strategy.trade_group_id)
            logger.info(f"{req_id} - order list {order_list}")
            total_realized_pnl = calculate_total_realized_pnl(
                client.futures_account_trades(symbol=signal_symbol, limit=100), order_list)
            logger.info(f"{req_id} - total_realized_pnl {total_realized_pnl}")
            logger.info(f"{req_id} - {update_account_balance(total_realized_pnl, strategy.strategy_id)}")
            # 新增EXIT紀錄
            exit_data = {
                'symbol': signal_symbol,
                'side': prev_opposite_side,
                'type': 'EXIT'
            }
            create_trades_from_binance(binance_trades=[exit_data], strategy_id=strategy.strategy_id,
                                       trade_group_id=strategy.trade_group_id)
            # 更新Strategy狀態
            strategy.status = "INACTIVE"
            strategy.save()
            # 更新已實現盈虧到出場紀錄
            update_trade_profit_loss(strategy.trade_group_id, total_realized_pnl)
            logger.info(f"{req_id} - end")
            return HttpResponse('received')
        else:
            logger.info(f"{req_id} - no position")
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
    raw_quantity = 0 if usdt is None else math.floor(100000 * float(usdt) * percentage / signal_entry) / 100000

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

    create_swing_order(
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

def create_swing_order(
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
    """
    創建 swing 交易訂單
    """
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
    quantity_level2 = format_decimal(quantity * (2 / 11), _quantity_precision)
    quantity_level3 = format_decimal(quantity * (3 / 11), _quantity_precision)  # 3/(1+2+3+5) 的倉位大小

    # 初始化止盈點格
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
        create_trades_from_binance(binance_trades=response, strategy_id=strategy.strategy_id,
                                   trade_group_id=trade_group_id)
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
        create_trades_from_binance(binance_trades=response, strategy_id=strategy.strategy_id,
                                   trade_group_id=trade_group_id)
        logger.info(f"{req_id} - create_order response 2 {response}") 