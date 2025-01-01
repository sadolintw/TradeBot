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
    get_monthly_rotating_logger,
    get_grid_position,
    create_new_grid_position,
    query_trade,
    get_total_quantity_for_strategy,
    close_positions_for_strategy,
    execute_split_fok_orders,
    OrderStatus,
    OrderType,
    OrderSide,
    PositionSide,
    BinanceWebsocketClient,
    generate_grid_levels,
    update_grid_positions_price,
    generate_trade_group_id,
    set_exchange_info_map,
    grid_v2_lab,
    update_all_future_positions,
    create_trade,
    get_strategy_by_symbol,
    create_order_execution,
    update_balance_and_pnl_by_custom_order_id,
    risk_control,
    recover_leverage,
    recover_all_active_strategy_leverage,
    update_balance_from_execution,
    create_trade_from_ws_order,
    create_balance_history_snapshot
)

notification_queue_entry = queue.Queue()
notification_queue_exit = queue.Queue()
balance_update_queue = queue.Queue()

main_account = get_main_account_info()

api_key = main_account.api_key
api_secret = main_account.api_secret

client = Client(api_key, api_secret)
tradingview_passphase = os.environ['TRADINGVIEW_PASSPHASE']

logger = get_monthly_rotating_logger('log', '../logs')
logger.info('start')

# WS
####################################
# 全局 WebSocket 客戶端實例
ws_client = None

def initialize_websocket():
    """初始化 WebSocket 連接"""
    global ws_client
    if ws_client is None:
        ws_client = BinanceWebsocketClient(
            api_key=api_key,
            api_secret=api_secret,
            callback=ws_callback,
            custom_logger=logger  # 使用 views 中定義的 logger
        )
        # 在新線程中啟動 websocket
        websocket_thread = threading.Thread(target=ws_client.run)
        websocket_thread.daemon = True
        websocket_thread.start()
        logger.info("WebSocket 客戶端已初始化")

def ws_callback(msg):
    """WebSocket 回調函數"""
    if msg['e'] == 'ORDER_TRADE_UPDATE':
        order = msg['o']
        order_id = order['i']
        
        # 使用常數進行比較
        status_message = {
            OrderStatus.NEW: f"訂單 {order_id} 已創建",
            OrderStatus.PARTIALLY_FILLED: f"訂單 {order_id} 部分成交",
            OrderStatus.FILLED: f"訂單 {order_id} 已完全成交",
            OrderStatus.CANCELED: f"訂單 {order_id} 已取消",
            OrderStatus.REJECTED: f"訂單 {order_id} 被拒絕",
            OrderStatus.EXPIRED: f"訂單 {order_id} 已過期",
            OrderStatus.PENDING_CANCEL: f"訂單 {order_id} 正在取消中"
        }
        
        if order['X'] in status_message:
            logger.info(status_message[order['X']])
            
            # 當訂單部分成交或完全成交時記錄執行情況
            if order['X'] in [OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED]:
                try:
                    symbol = order['s']
                    strategy = get_strategy_by_symbol(symbol)
                    execution_type = 'FULL' if order['X'] == OrderStatus.FILLED else 'PARTIAL'
                    create_order_execution(strategy, order, execution_type)
                    # 根據訂單類型決定交易類型 - 移到外面確保一定會被定義
                    trade_type = "GRID_V2_MARKET" if order['o'] == "MARKET" else "GRID_V2_LIMIT"
                    
                    # 創建交易記錄
                    create_trade_from_ws_order(order, strategy, trade_type)

                    # 更新餘額（已實現盈虧減去手續費）
                    realized_pnl = float(order.get('rp', 0))  # 已實現盈虧
                    commission = float(order.get('n', 0))     # 手續費
                    
                    # 調用新增的更新餘額函數
                    update_balance_from_execution(
                        strategy_id=strategy.strategy_id,
                        realized_pnl=realized_pnl,
                        commission=commission
                    )

                    v2 = ['ONDOUSDT', 'DOGEUSDT', 'WIFUSDT']

                    # 如果是ONDOUSDT，執行額外操作
                    if symbol in v2:
                        grid_v2_lab(client, strategy.passphrase, symbol)
                        close_orders = update_all_future_positions(client)
                        for symbol, close_order in close_orders.items():
                            if symbol in v2:
                                risk_control(client=client, symbol=symbol, close_order=close_order)
                except Exception as e:
                    logger.error(f"記錄交易時發生錯誤: {str(e)}")
                except Exception as e:
                    logger.error(f"記錄訂單執行時發生錯誤: {str(e)}")

# def process_balance_updates():
#     """定期處理餘額更新佇列"""
#     while True:
#         try:
#             if not balance_update_queue.empty():
#                 update_data = balance_update_queue.get()
#                 max_retries = 2
#                 retry_delay = 5  # 秒
#
#                 for attempt in range(max_retries):
#                     try:
#                         time.sleep(retry_delay)  # 等待資料寫入
#                         update_balance_and_pnl_by_custom_order_id(
#                             client=update_data['client'],
#                             symbol=update_data['symbol'],
#                             trade_group_id=update_data['trade_group_id'],
#                             thirdparty_id=update_data['thirdparty_id'],
#                             strategy_id=update_data['strategy_id'],
#                             trade_type=update_data['trade_type'],
#                             update_profit_loss=True,
#                             use_api=False
#                         )
#                         break
#                     except Exception as e:
#                         logger.warning(f"更新餘額和盈虧重試 {attempt + 1}/{max_retries}: {str(e)}")
#                         if attempt == max_retries - 1:
#                             logger.error(f"更新餘額和盈虧失敗: {str(e)}")
#
#                 balance_update_queue.task_done()
#             time.sleep(2)  # 避免過度佔用CPU
#         except Exception as e:
#             logger.error(f"處理餘額更新時發生錯誤: {str(e)}")
#
# balance_update_thread = threading.Thread(
#     target=process_balance_updates,
#     daemon=True,
#     name="BalanceUpdateProcessor"
# )
# balance_update_thread.start()

# 在應用啟動時初始化 WebSocket
initialize_websocket()
####################################


def index(request):
    return HttpResponse("Hello, world. You're at the polls index.")


def wrap_str(_str):
    return "[" + _str + "]"

# Setting
####################################
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
# body_unicode = request.body.decode('utf-8')
close_position_delay = 2
close_all_position_delay = 4
create_order_delay = 2
get_account_trade_delay = 2
percentage = 0.95
# preserve prev position exists less than
preserve_prev_position_second = 20
####################################

def parse_type(notification):
    """
    解析通知訊息中的類型
    """
    try:
        notification_json = json.loads(notification)
        notification_type = json.loads(notification_json['message'])
        return notification_type['type']
    except Exception as e:
        logger.error(f"解析type時發生錯誤: {str(e)}")
        logger.error(f"通知內容: {notification}")
        raise  # 重新拋出異常，維持原本的中斷行為


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
    
    # 從 filters 中獲取 tick size
    tick_size = None
    for filter in symbol_info["filters"]:
        if filter["filterType"] == "PRICE_FILTER":
            tick_size = float(filter["tickSize"])
            break
            
    exchange_info_map[symbol] = {
        "pricePrecision": price_precision,
        "quantityPrecision": quantity_precision,
        "tickSize": tick_size
    }
set_exchange_info_map(exchange_info_map)

# 打印結果
# logger.info(exchange_info_map)
# 打印目前策略
strategy_list()


@api_view(['GET', 'POST'])
def webhook(request):
    logger.info("receive notification")
    plain = request.body.decode('utf-8')
    notification_type = parse_type(plain)
    if notification_type == 'long_exit' or \
            notification_type == 'short_exit' or \
            notification_type == 'exit' or \
            notification_type == 'close_all':
        notification_queue_exit.put(plain)
    if notification_type == 'long_entry' or \
            notification_type == 'short_entry' or \
            notification_type == 'entry':
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
    req_id = wrap_str(str(uuid.uuid1()).split("-")[0])
    logger.info(f"{req_id} - received signal: {body_unicode}")
    if body_unicode:
        try:
            notification = json.loads(body_unicode)
            strategy = find_strategy_by_passphrase(notification['passphrase'])
            if strategy is not None:
                logger.info(f"{req_id} - passphrase correct")
                # 檢查notification中是否存在'type'字段，並判斷其值
                if 'type' in notification:
                    notification_type = notification['type']
                    if notification_type == 'grid':
                        # 如果type為'grid'，則處理網格交易通知
                        handle_grid_notification(req_id, strategy, notification)
                    if notification_type == 'grid_v2':
                        handle_grid_notification_v2(req_id, strategy, notification)
                    elif notification_type == 'swing':
                        # 如果type為'swing'，則處理波段交易通知
                        handle_swing_notification(req_id, strategy, notification)
                else:
                    # 如果notification中不存在'type'字段，可以考慮記錄錯誤、拋出異常或者默認處理
                    logger.info(f"{req_id} - Notification type is missing or invalid.")

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


def handle_grid_notification(req_id, strategy, notification):
    notification_message = json.loads(notification.get("message", {}))
    grids = int(notification_message.get("grids", 0))
    grid_index = int(notification_message.get("gridIndex", 0))  # 从通知中获取格子索引，缺省值为0
    leverage = int(notification_message.get("leverage", 1))  # 从通知中获取杠杆数，缺省值为1
    weight = int(notification_message.get("weight", 1))
    total_weight = int(notification_message.get("totalWeight", 10))
    notification_type = notification_message.get("type", "entry")  # 从通知中获取类型，缺省值为"entry"
    notification_symbol = notification.get("ticker")
    notification_entry = notification.get("entry")

    if not notification_symbol or not notification_entry:
        logger.info(f"{req_id} - Field not found in notification. {notification}")
        return
    if notification_type == "entry":
        # 处理开仓逻辑
        logger.info(
            f"{req_id} - Handling grid entry for strategy {strategy.strategy_id} on grid {grid_index} with leverage {leverage}")
        open_grid_position(
            req_id=req_id,
            strategy=strategy,
            grids=grids,
            grid_index=grid_index,
            leverage=leverage,
            weight=weight,
            total_weight=total_weight,
            notification_symbol=notification_symbol,
            notification_entry=notification_entry
        )
    elif notification_type == "exit":
        # 处理平仓逻辑
        logger.info(f"{req_id} - Handling grid exit for strategy {strategy.strategy_id} on grid {grid_index}")
        close_grid_position(
            req_id=req_id,
            strategy=strategy,
            grid_index=grid_index,
            notification_symbol=notification_symbol
        )
    elif notification_type == "close_all":
        # 处理關閉倉位邏輯
        logger.info(f"{req_id} - Handling close for strategy {strategy.strategy_id}")
        close_all_position(
            req_id=req_id,
            strategy=strategy,
            notification_symbol=notification_symbol
        )
    else:
        # 未知的通知类型
        logger.error(f"{req_id} - Unknown notification type {notification_type} for strategy {strategy.strategy_id}")

def handle_grid_notification_v2(req_id, strategy, notification):
    notification_message = json.loads(notification.get("message", {}))
    logger.info(f"{req_id} - notification_message {notification_message}")
    grids = int(notification_message.get("grids", 0))
    grid_index = int(notification_message.get("gridIndex", 0))  # 从通知中获取格子索引，缺省值为0
    leverage = int(notification_message.get("leverage", 1))  # 从通知中获取杠杆数，缺省值为1
    weight = int(notification_message.get("weight", 1))
    total_weight = int(notification_message.get("totalWeight", 10))
    notification_type = notification_message.get("type", "entry")  # 从通知中获取类型，缺省值为"entry"
    notification_symbol = notification.get("ticker")
    notification_entry = notification.get("entry")

    levels = generate_grid_levels(float(notification_message.get("lower_bound")), float(notification_message.get("upper_bound")), grids)
    logger.info(f"{req_id} - levels {levels}")
    update_grid_positions_price(strategy, levels)

    if not notification_symbol or not notification_entry:
        logger.info(f"{req_id} - Field not found in notification. {notification}")
        return
    if notification_type == "entry":
        # 处理开仓逻辑
        logger.info(
            f"{req_id} - Handling grid entry v2 for strategy {strategy.strategy_id} on grid {grid_index} with leverage {leverage}")
        # open_grid_position(
        #     req_id=req_id,
        #     strategy=strategy,
        #     grids=grids,
        #     grid_index=grid_index,
        #     leverage=leverage,
        #     weight=weight,
        #     total_weight=total_weight,
        #     notification_symbol=notification_symbol,
        #     notification_entry=notification_entry
        # )
    elif notification_type == "exit":
        # 处理平仓逻辑
        logger.info(f"{req_id} - Handling grid exit v2 for strategy {strategy.strategy_id} on grid {grid_index}")
        # close_grid_position(
        #     req_id=req_id,
        #     strategy=strategy,
        #     grid_index=grid_index,
        #     notification_symbol=notification_symbol
        # )
    elif notification_type == "close_all":
        # 处理關閉倉位邏輯
        logger.info(f"{req_id} - Handling close v2 for strategy {strategy.strategy_id}")
        # close_all_position(
        #     req_id=req_id,
        #     strategy=strategy,
        #     notification_symbol=notification_symbol
        # )
    else:
        # 未知的通知类型
        logger.error(f"{req_id} - Unknown notification type {notification_type} for strategy {strategy.strategy_id}")

def open_grid_position(
        req_id,
        strategy,
        grids,
        grid_index,
        leverage,
        weight,
        total_weight,
        notification_symbol,
        notification_entry
):
    """
    根据策略、网格索引和杠杆信息开仓。

    参数:
    - strategy: 策略实例
    - grid_index: 网格索引
    - leverage: 杠杆
    """
    try:
        strategy_client = Client(strategy.account.api_key, strategy.account.api_secret)
        logger.info(f"{req_id} - strategy client {strategy_client}")
        symbol_exchange_info = exchange_info_map[notification_symbol]
        _price_precision = int(symbol_exchange_info['pricePrecision'])
        _quantity_precision = int(symbol_exchange_info['quantityPrecision'])
        grid_position = get_grid_position(
            strategy=strategy,
            grid_index=grid_index
        )
        change_leverage(req_id, strategy_client, notification_symbol, leverage)
        balance = find_balance_by_strategy_id(strategy.strategy_id)
        leverage_rate = strategy.leverage_rate
        if balance is not None:
            usdt = balance.balance
            logger.info(f"{req_id} - has corresponding balance {usdt}")
        else:
            usdt = strategy.initial_capital

        usdt = str(usdt)
        raw_quantity = 0 if usdt is None else math.floor(
            100000 * float(usdt) * float(leverage_rate) * int(weight) / int(total_weight) / float(notification_entry)) / 100000
        quantity = round(raw_quantity * int(leverage), _quantity_precision)
        logger.info(f"{req_id} - raw_quantity {raw_quantity}")
        logger.info(f"{req_id} - quantity {quantity} quantity_precision {_quantity_precision}")

        if grid_position:
            logger.info(
                f"{req_id} - grid entry：strategy ID {strategy.strategy_id}, grid_index {grid_index} exist, leverage {leverage}")
            if grid_position.is_open:
                logger.info(f"{req_id} - grid_index already open")
                return

        else:
            logger.info(
                f"{req_id} - grid entry：strategy ID {strategy.strategy_id}, grid_index {grid_index} not exist, leverage {leverage}")
            grid_position = create_new_grid_position(strategy=strategy, grid_index=grid_index)

        # 建立trade_group_id
        trade_group_id = generate_trade_group_id()
        # 目前沒有設止盈止損 靠訊號關單
        create_grid_order(
            req_id=req_id,
            strategy_client=strategy_client,
            strategy=strategy,
            notification_symbol=notification_symbol,
            side_to_open="BUY",
            quantity_to_open=quantity,
            entry=notification_entry,
            trade_group_id=trade_group_id
        )
        grid_position.quantity = quantity
        grid_position.entry_price = notification_entry
        grid_position.is_open = True
        grid_position.trade_group_id = trade_group_id
        grid_position.save()

        post_data = {
            'symbol': notification_symbol,
            'entry': notification_entry,
            'side': "BUY",
            'type': "Grid Long Entry",
            'msg': 'create order'
        }
        send_telegram_message(req_id, post_data)

    except Exception as e:
        # 处理可能发生的其他异常
        logger.error(f"{req_id} - error：{e}")


def open_grid_position_v2(
        req_id,
        strategy,
        grids,
        grid_index,
        leverage,
        weight,
        total_weight,
        notification_symbol,
        notification_entry
):
    """
    使用FOK訂單的網格進場V2版本
    """
    try:
        # 直接使用strategy的api資訊
        strategy_client = Client(strategy.account.api_key, strategy.account.api_secret)
        if not strategy_client:
            logger.error(f"{req_id} - Failed to get strategy client")
            return False

        # 計算開倉數量
        balance = find_balance_by_strategy_id(strategy.strategy_id)
        usdt = balance.balance if balance else strategy.initial_capital
        raw_quantity = 0 if usdt is None else math.floor(
            100000 * float(usdt) * int(weight) / int(total_weight) / float(notification_entry)
        ) / 100000
        quantity_to_open = round(raw_quantity * int(leverage),
                                 int(exchange_info_map[notification_symbol]['quantityPrecision']))

        if quantity_to_open <= 0:
            logger.error(f"{req_id} - Invalid quantity: {quantity_to_open}")
            return False

        # 生成trade_group_id
        trade_group_id = generate_trade_group_id()

        # 獲取symbol資訊
        symbol_info = exchange_info_map[notification_symbol]
        quantity_precision = int(symbol_info['quantityPrecision'])
        price_precision = int(symbol_info['pricePrecision'])

        # 使用FOK分批進場
        entry_result = execute_split_fok_orders(
            client=strategy_client,
            symbol=notification_symbol,
            side="BUY",
            total_quantity=quantity_to_open,
            price=notification_entry,
            quantity_precision=quantity_precision,
            price_precision=price_precision,
            split_parts=3,
            max_workers=3,
            base_price_adjustment=0.00005
        )

        if entry_result['success']:
            # 創建或更新GridPosition
            grid_position = get_grid_position(
                strategy=strategy,
                grid_index=grid_index
            )

            if not grid_position:
                grid_position = create_new_grid_position(
                    strategy=strategy,
                    grid_index=grid_index
                )

            # 更新GridPosition
            grid_position.quantity = entry_result['total_executed']
            grid_position.entry_price = entry_result['avg_price']
            grid_position.is_open = True
            grid_position.trade_group_id = trade_group_id
            grid_position.save()

            # 創建交易記錄
            create_trades_from_binance(
                binance_trades=entry_result['orders'],
                strategy_id=strategy.strategy_id,
                trade_group_id=trade_group_id,
                trade_type_override="GRID_ENTRY"
            )

            # 更新策略狀態
            strategy.status = "ACTIVE"
            strategy.save()

            logger.info(f"{req_id} - Grid entry V2完成: "
                        f"總成交量={entry_result['total_executed']}, "
                        f"平均價格={entry_result['avg_price']}, "
                        f"最大價格調整={entry_result['max_price_adjustment'] * 100:.4f}%")

            # 發送Telegram通知
            post_data = {
                'symbol': notification_symbol,
                'entry': entry_result['avg_price'],
                'side': "BUY",
                'type': "Grid Long Entry V2",
                'msg': 'create order with FOK'
            }
            send_telegram_message(req_id, post_data)

            return True
        else:
            logger.error(f"{req_id} - Grid entry V2失敗: {entry_result.get('error')}")
            return False

    except Exception as e:
        logger.error(f"{req_id} - Open grid position V2 error: {str(e)}")
        return False


def close_grid_position(
        req_id,
        strategy,
        grid_index,
        notification_symbol
):
    """
    根据策略和网格索引关闭网格位置。

    参数:
    - req_id: 請求id
    - strategy: 策略实例
    - grid_index: 网格索引
    - notification_symbol: 通知裡的幣種
    """
    try:
        strategy_client = Client(strategy.account.api_key, strategy.account.api_secret)
        # 查找所有对应的且当前为开仓状态的GridPosition记录
        grid_position_to_close = get_grid_position(
            strategy=strategy,
            grid_index=grid_index
        )

        if grid_position_to_close:
            logger.info(
                f"{req_id} - grid entry：strategy ID {strategy.strategy_id}, grid_index {grid_index} exist")
            if not grid_position_to_close.is_open:
                logger.info(f"{req_id} - grid_index already close")
                return
        else:
            logger.info(f"{req_id} - symbol {notification_symbol} grid_index not found")
            return

        position = get_position(req_id=req_id, strategy_client=strategy_client, symbol=notification_symbol)
        close_grid_order(
            req_id=req_id,
            strategy_client=strategy_client,
            strategy=strategy,
            side_to_close="SELL",
            position_info=position,
            grid_position=grid_position_to_close,
            notification_symbol=notification_symbol
        )
        ###
        grid_exit_trade = query_trade(trade_group_id=grid_position_to_close.trade_group_id, trade_type="GRID_EXIT")
        if grid_exit_trade is not None:
            start_time = str((grid_exit_trade.created_at_timestamp - 3600) * 1000)
            time.sleep(get_account_trade_delay)
            # 改由WS更新
            # update_balance_and_pnl(
            #     req_id=req_id,
            #     symbol=notification_symbol,
            #     trade_group_id=grid_position_to_close.trade_group_id,
            #     strategy_id=strategy.strategy_id,
            #     trade_type="GRID_EXIT",
            #     start_time=start_time
            # )
        else:
            logger.info(f"{req_id} - not grid exit record")
            return

        # 關閉網格倉位
        grid_position_to_close.is_open = False
        grid_position_to_close.save()

    except Exception as e:
        # 处理可能发生的其他异常
        logger.error(f"{req_id} - error：{e}")


def close_all_position(
        req_id,
        strategy,
        notification_symbol
):
    """
    根据策略和网格索引关闭网格位置。

    参数:
    - req_id: 請求id
    - strategy: 策略实例
    - notification_symbol: 通知裡的幣種
    """
    try:
        strategy_client = Client(strategy.account.api_key, strategy.account.api_secret)
        # 查找所有对应的且当前为开仓状态的GridPosition记录
        grid_quantity_to_close = get_total_quantity_for_strategy(
            strategy=strategy
        )

        close_positions_for_strategy(strategy=strategy)
        # 构造平仓订单
        order_payload = [
            # 市價出場
            {
                'symbol': notification_symbol,
                'type': 'MARKET',
                'quantity': format_decimal(grid_quantity_to_close,
                                           int(exchange_info_map[notification_symbol]['quantityPrecision'])),
                'side': "SELL",
                'reduceOnly': 'true'
            }
        ]
        logger.info(order_payload)
        logger.info(f"{req_id} - Closing order: {json.dumps(order_payload)}")
        if check_api_enable(enable_create_order):
            exit_response = strategy_client.futures_place_batch_order(batchOrders=json.dumps(order_payload))
            # 生成trade_group_id
            trade_group_id = uuid.uuid4()
            # 創建Trade實例
            create_trades_from_binance(
                binance_trades=exit_response,
                strategy_id=strategy.strategy_id,
                trade_group_id=trade_group_id,
                trade_type_override="GRID_EXIT"
            )

            time.sleep(close_all_position_delay)

            start_time = str(int(datetime.timestamp(datetime.now()) - 3600) * 1000)

            # 更新餘額和盈虧
            update_balance_and_pnl(
                req_id=req_id,
                symbol=notification_symbol,
                trade_group_id=trade_group_id,
                strategy_id=strategy.strategy_id,
                trade_type="GRID_EXIT",
                start_time=start_time
            )

            logger.info(f"{req_id} - Close order response: {exit_response}")

    except Exception as e:
        logger.error(f"{req_id} - error：{e}")


###


def handle_notification_common(req_id, strategy, notification, position):
    logger.info(f"{req_id} - check current position")
    time.sleep(close_position_delay)
    if position is not None:
        return handle_existing_position(req_id, strategy, notification, position)
    else:
        return False


def handle_existing_position(
        req_id,
        strategy,
        notification,
        position,
        symbol_exchange_info,
        strategy_client,
        signal_symbol,
        signal_message_type
):
    logger.info(f"{req_id} - position is not None")
    prev_quantity = position['positionAmt']
    _price_precision = int(symbol_exchange_info['pricePrecision'])
    _quantity_precision = int(symbol_exchange_info['quantityPrecision'])
    signal_position_size = round(float(notification['position_size']), _quantity_precision)
    prev_opposite_side = 'SELL' if float(prev_quantity) > 0 else (
        '' if float(prev_quantity) == 0.0 else 'BUY')
    prev_update_time = int(position['updateTime'])
    now = datetime.now()
    timestamp = datetime.timestamp(now) * 1000
    # diff seconds
    logger.info(f"{req_id} - current timestamp {timestamp}")
    logger.info(f"{req_id} - prev timestamp {prev_update_time}")
    diff = (timestamp - prev_update_time) / 1000
    # 幣安的掛單先止盈可能也會是False
    allowed_close_position = True if diff > preserve_prev_position_second else False

    logger.info(f"{req_id} - signal_position_size {signal_position_size}")
    logger.info(f"{req_id} - allowed_close_position {allowed_close_position}")

    if signal_position_size == 0 and allowed_close_position:
        logger.info(f"{req_id} - close signal")
        logger.info(f"{req_id} - close prev open order for close signal")
        cancel_all_open_order(symbol=signal_symbol, strategy_client=strategy_client)
        logger.info(f"{req_id} - close prev position")
        close_response = close_position(
            req_id=req_id,
            strategy_client=strategy_client,
            symbol=signal_symbol,
            side=prev_opposite_side,
            quantity=prev_quantity
        )
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

        trade_array = convert_to_trade_array(response=close_response, trade_type_override='EXIT', symbol=signal_symbol)
        create_trades_from_binance(
            binance_trades=trade_array,
            strategy_id=strategy.strategy_id,
            trade_group_id=strategy.trade_group_id
        )
        order_list = query_trades_by_group_id(strategy.trade_group_id)
        # 更新該策略的balance
        logger.info(f"{req_id} - order list {order_list}")
        total_realized_pnl = calculate_total_realized_pnl(
            client.futures_account_trades(symbol=signal_symbol, limit=100), order_list)
        logger.info(f"{req_id} - total_realized_pnl {total_realized_pnl}")
        logger.info(f"{req_id} - {update_account_balance(total_realized_pnl, strategy.strategy_id)}")
        # 更新已實現盈虧到出場紀錄
        update_trade_profit_loss(strategy.trade_group_id, total_realized_pnl)
        return True
    else:
        return False


# TODO refactor
def handle_swing_notification2(req_id, strategy, notification):
    strategy_client = Client(strategy.account.api_key, strategy.account.api_secret)
    logger.info(f"{req_id} - strategy client {strategy_client}")
    signal_symbol = notification['ticker']
    symbol_exchange_info = exchange_info_map[signal_symbol]
    _price_precision = int(symbol_exchange_info['pricePrecision'])
    _quantity_precision = int(symbol_exchange_info['quantityPrecision'])

    logger.info(f"{req_id} - check current position")
    time.sleep(close_position_delay)
    position = get_position(
        req_id=req_id,
        strategy_client=strategy_client,
        symbol=signal_symbol
    )
    prev_quantity = position['positionAmt']
    prev_opposite_side = 'SELL' if float(prev_quantity) > 0 else (
        '' if float(prev_quantity) == 0.0 else 'BUY')
    signal_symbol = notification['ticker']
    signal_message_json = json.loads(notification['message']) if notification.get('message') else None
    logger.info(f"{req_id} - signal message {signal_message_json}") if signal_message_json else None

    signal_message_type = signal_message_json['type'] if signal_message_json and 'type' in signal_message_json else None
    signal_message_lev = signal_message_json['lev'] if signal_message_json and 'lev' in signal_message_json else None

    signal_message_eq = int(
        float(signal_message_json['eq'])) if signal_message_json and 'eq' in signal_message_json else 0
    signal_message_eq = 95 if signal_message_eq > 95 else signal_message_eq
    equity_percentage = signal_message_eq / 100
    signal_position_size = round(float(notification['position_size']), _quantity_precision)
    is_close_notification = handle_notification_common(
        req_id=req_id,
        strategy=strategy,
        notification=notification,
        position=position,
        symbol_exchange_info=symbol_exchange_info,
        strategy_client=strategy_client,
        signal_symbol=signal_symbol,
        signal_message_type=signal_message_type
    )

    if is_close_notification:
        return

    # 根据信号类型处理订单创建和关闭
    signal_message_type = signal_message_json.get('type') if signal_message_json else None
    if signal_message_type in ['long_entry', 'short_entry']:
        create_order_based_on_notification(
            req_id=req_id,
            strategy=strategy,
            notification=notification,
            signal_message_json=signal_message_json,
            strategy_client=strategy_client,
            signal_symbol=signal_symbol,
            signal_message_type=signal_message_type,
            signal_message_lev=signal_message_lev,
            symbol_exchange_info=symbol_exchange_info,
            signal_position_size=signal_position_size,
            equity_percentage=equity_percentage,
            prev_quantity=prev_quantity,
            prev_opposite_side=prev_opposite_side
        )
    elif signal_message_type in ['long_exit', 'short_exit']:
        close_order_based_on_notification(
            req_id=req_id,
            strategy=strategy,
            signal_message_json=signal_message_json,
            signal_symbol=signal_symbol,
            prev_opposite_side=prev_opposite_side,
            signal_message_type=signal_message_type
        )


def create_order_based_on_notification(
        req_id,
        strategy,
        notification,
        signal_message_json,
        strategy_client,
        signal_symbol,
        signal_message_type,
        signal_message_lev,
        symbol_exchange_info,
        signal_position_size,
        equity_percentage,
        prev_quantity,
        prev_opposite_side
):
    logger.info(f"{req_id} - close prev open order for entry signal")
    cancel_all_open_order(symbol=signal_symbol, strategy_client=strategy_client)
    _price_precision = int(symbol_exchange_info['pricePrecision'])
    _quantity_precision = int(symbol_exchange_info['quantityPrecision'])
    time.sleep(create_order_delay)
    # prepare param
    all_usdt = Decimal(get_usdt(req_id=req_id, strategy_client=strategy_client))
    balance = find_balance_by_strategy_id(strategy.strategy_id)
    if balance is not None:
        usdt = balance.balance
        logger.info(f"{req_id} - has corresponding balance {usdt}")
        balance.equity = equity_percentage
        balance.save()
        logger.info(f"{req_id} - update equity % {equity_percentage}")
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
    raw_quantity = 0 if usdt is None else math.floor(100000 * float(usdt) * equity_percentage / signal_entry) / 100000

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


def close_order_based_on_notification(
        req_id,
        strategy,
        signal_message_json,
        signal_symbol,
        prev_opposite_side,
        signal_message_type
):
    logger.info(f"{req_id} - exit: {signal_message_json['type']}")
    post_data = {
        'symbol': signal_symbol,
        'side': prev_opposite_side,
        'type': signal_message_type,
        'msg': 'close prev position'
    }
    send_telegram_message(req_id, post_data)
    # 更新該策略的balace
    order_list = query_trades_by_group_id(strategy.trade_group_id)
    # 用原本的掛單查
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


###


def handle_swing_notification(req_id, strategy, notification):
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
        # 幣安的掛單先止盈可能也會是False
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
        close_response = close_position(req_id=req_id, strategy_client=strategy_client, symbol=signal_symbol,
                                        side=prev_opposite_side, quantity=prev_quantity)
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

        trade_array = convert_to_trade_array(response=close_response, trade_type_override='EXIT', symbol=signal_symbol)
        create_trades_from_binance(binance_trades=trade_array, strategy_id=strategy.strategy_id,
                                   trade_group_id=strategy.trade_group_id)
        order_list = query_trades_by_group_id(strategy.trade_group_id)
        # 更新該策略的balace
        logger.info(f"{req_id} - order list {order_list}")
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
        # 更新該策略的balace
        order_list = query_trades_by_group_id(strategy.trade_group_id)
        # 用原本的掛單查
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
    trade_group_id = generate_trade_group_id()
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
            'side': side,
            'newClientOrderId': str(trade_group_id)  # 加入自定義訂單ID
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


def create_grid_order(
        req_id,
        strategy_client,
        strategy,
        notification_symbol,
        side_to_open,
        quantity_to_open,
        entry,
        trade_group_id
):
    logger.info(f"{req_id} - create_order, {notification_symbol}, {side_to_open}, {quantity_to_open}, {entry}")
    _price_precision = int(exchange_info_map[notification_symbol]['pricePrecision'])
    _quantity_precision = int(exchange_info_map[notification_symbol]['quantityPrecision'])
    logger.info(f"{req_id} - quantity precision {_quantity_precision}")
    if (format_decimal(quantity_to_open, _quantity_precision)) == 0:
        logger.info(f"{req_id} - Unable to open a position: the quantity becomes 0 after precision adjustment")

    # 構造訂單
    batch_payload = [
        # 市價入場
        {
            'symbol': notification_symbol,
            'type': 'MARKET',
            'quantity': format_decimal(quantity_to_open, _quantity_precision),
            'side': side_to_open,
            'newClientOrderId': str(trade_group_id)  # 加入自定義訂單ID
        }
    ]

    logger.info(f"{req_id} - batch order', {json.dumps(batch_payload)}")
    if check_api_enable(enable_create_order):
        response = strategy_client.futures_place_batch_order(batchOrders=json.dumps(batch_payload))
        # 創建Trade實例
        create_trades_from_binance(
            binance_trades=response,
            strategy_id=strategy.strategy_id,
            trade_group_id=trade_group_id,
            trade_type_override="GRID_ENTRY"
        )
        # 更新Strategy狀態
        strategy.status = "ACTIVE"
        strategy.save()
        logger.info(f"{req_id} - Create order response {response}")


def close_grid_order(
        req_id,
        strategy_client,
        strategy,
        side_to_close,
        position_info,
        grid_position,
        notification_symbol
):
    quantity_to_close = grid_position.quantity
    trade_group_id = grid_position.trade_group_id
    logger.info(f"{req_id} - Attempting to close position for {notification_symbol} with quantity {quantity_to_close}")

    if position_info is not None:
        current_position_amt = float(position_info['positionAmt'])

        # 如果当前持仓量小于或等于要平仓的量，则平掉所有持仓
        quantity_to_close = abs(current_position_amt) if abs(
            current_position_amt) < quantity_to_close else quantity_to_close

        _quantity_precision = int(exchange_info_map[notification_symbol]['quantityPrecision'])
        formatted_quantity = format_decimal(quantity_to_close, _quantity_precision)

        # 构造平仓订单
        order_payload = [
            # 市價出場
            {
                'symbol': notification_symbol,
                'type': 'MARKET',
                'quantity': formatted_quantity,
                'side': side_to_close
            }
        ]

        logger.info(f"{req_id} - Closing order: {json.dumps(order_payload)}")
        if check_api_enable(enable_create_order):
            response = strategy_client.futures_place_batch_order(batchOrders=json.dumps(order_payload))
            # 創建Trade實例
            create_trades_from_binance(
                binance_trades=response,
                strategy_id=strategy.strategy_id,
                trade_group_id=trade_group_id,
                trade_type_override="GRID_EXIT"
            )
            # TODO 判斷是否全部平掉 是的話更新INACTIVE
            # 记录平仓操作
            logger.info(f"{req_id} - Close order response: {response}")
    else:
        logger.error(f"{req_id} - Failed to get position info for {notification_symbol}")


# 排程
##############

def run_schedule():
    # 每 5 秒運行一次
    schedule.every(5).seconds.do(handle_webhook_entry_schedule)
    schedule.every(5).seconds.do(handle_webhook_exit_schedule)
    
    # 新增每天午夜執行的槓桿率恢復排程
    schedule.every().day.at("00:00").do(recover_all_active_strategy_leverage)
    
    # 每半小時執行一次 (在每小時的 0 分和 30 分執行)
    schedule.every().hour.at(":00").do(create_balance_history_snapshot)
    schedule.every().hour.at(":30").do(create_balance_history_snapshot)
    
    while True:
        schedule.run_pending()
        time.sleep(1)


# 在單獨的線程中運行定時任務
entry_schedule_thread = threading.Thread(target=run_schedule, daemon=True)
entry_schedule_thread.start()


def update_balance_and_pnl(
    req_id, 
    symbol, 
    trade_group_id, 
    strategy_id, 
    start_time=None, 
    trade_type="EXIT",
    update_profit_loss=True
):
    """
    更新策略餘額和已實現盈虧
    
    Args:
        req_id: 請求ID用於日誌追蹤
        symbol: 交易幣種
        trade_group_id: 交易組ID 
        strategy_id: 策略ID
        start_time: 開始時間 (可選)
        trade_type: 交易類型 (預設為 "EXIT")
        update_profit_loss: 是否更新已實現盈虧 (預設為 True)
    """
    # 查詢相關訂單
    order_list = query_trades_by_group_id(trade_group_id)
    logger.info(f"{req_id} - order list {order_list}")

    # 計算已實現盈虧
    account_trades_params = {
        'symbol': symbol,
        'limit': 100
    }
    if start_time:
        account_trades_params['startTime'] = start_time

    total_realized_pnl = calculate_total_realized_pnl(
        client.futures_account_trades(**account_trades_params),
        order_list
    )
    logger.info(f"{req_id} - total_realized_pnl {total_realized_pnl}")

    # 更新策略餘額
    update_result = update_account_balance(total_realized_pnl, strategy_id)
    logger.info(f"{req_id} - {update_result}")

    # 根據參數決定是否更新已實現盈虧
    if update_profit_loss:
        update_trade_profit_loss(trade_group_id, total_realized_pnl, trade_type)

