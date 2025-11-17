import os
import json
import time  # 新增 time 模組
import uuid
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from binance.exceptions import BinanceAPIException  # 新增 BinanceAPIException
from .models import AccountInfo, Strategy, AccountBalance, Trade, GridPosition, OrderExecution, AccountBalanceHistory
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import threading
from binance import ThreadedWebsocketManager
from django.db.utils import DatabaseError
from collections import defaultdict
import math
import queue
from logging.handlers import QueueHandler, QueueListener
import re


def get_monthly_rotating_logger(logger_name, log_dir='logs'):
    logger = logging.getLogger(logger_name)
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)

        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # 使用當前時間建立檔案名
        current_month = datetime.now().strftime("%Y-%m")
        log_filename = f'{log_dir}/my_trade_system_{current_month}.log'

        # 修改 TimedRotatingFileHandler 的設置
        handler = TimedRotatingFileHandler(
            log_filename,
            when='midnight',
            interval=1,
            backupCount=30,  # 保留30天的日誌
            encoding='utf-8',
            delay=True
        )
        
        # 自定義檔案名格式
        handler.suffix = "%Y-%m-%d"
        # 使用 re.compile 來創建正則表達式對象
        handler.extMatch = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        
        # 設定檔案權限
        if os.name != 'nt':  # 非Windows系統
            os.chmod(log_filename, 0o666)
            
        formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(funcName)s] [%(lineno)d] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        
        # 新增 Queue Handler 處理多線程
        queue_handler = QueueHandler(queue.Queue())
        queue_listener = QueueListener(
            queue_handler.queue,
            handler,
            respect_handler_level=True
        )
        queue_listener.start()
        
        logger.addHandler(queue_handler)
        
        # 新增控制台輸出
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger

# 在文件開頭引入
logger = get_monthly_rotating_logger('trade')

exchange_info_map = {}

def set_exchange_info_map(new_exchange_info_map):
    global exchange_info_map
    exchange_info_map = new_exchange_info_map

def format_decimal(value, digit):
    format_string = "{:." + str(digit) + "f}"
    return format_string.format(value)

def format_decimal_symbol_quantity(symbol, value):
    return format_decimal(value, exchange_info_map[symbol]['quantityPrecision'])

def format_decimal_symbol_price(symbol, value):
    return format_decimal(value, exchange_info_map[symbol]['pricePrecision'])

def strategy_list(status: str = None) -> list:
    """
    列出策略清單,可選擇依狀態篩選
    
    Args:
        status: 策略狀態(可選),例如 'ACTIVE'
        
    Returns:
        list: Strategy 物件列表
    """
    try:
        # 根據是否有 status 參數來決定查詢條件
        if status:
            strategies = Strategy.objects.filter(status=status)
            logger.info(f'列出 {status} 狀態的策略')
        else:
            strategies = Strategy.objects.all()
            logger.info('列出所有策略')
            
        # 列印策略資訊
        # for strategy in strategies:
        #     logger.info(str(strategy))
            
        return list(strategies)
        
    except Exception as e:
        logger.error(f"列出策略清單時發生錯誤: {str(e)}")
        return []


def find_strategy_by_passphrase(passphrase):
    try:
        strategy = Strategy.objects.get(passphrase=passphrase)
        return strategy
    except Strategy.DoesNotExist:
        return None


def find_balance_by_strategy_id(strategy_id):
    try:
        # 嘗試查找對應的 AccountBalance 實例
        account_balance = AccountBalance.objects.get(strategy_id=strategy_id)
        return account_balance
    except AccountBalance.DoesNotExist:
        # 如果沒有找到對應的 AccountBalance，返回 None 或適當的錯誤處理
        return None


def create_trades_from_binance(binance_trades, strategy_id, trade_group_id="NA", trade_type_override=None):
    """
    从Binance交易数据创建Trade实例。

    :param binance_trades: 来自Binance的交易数据列表。
    :param strategy_id: 使用的策略ID。
    :param trade_group_id: 交易组ID。
    :param trade_type_override: 覆盖交易类型（如果提供）。
    """
    # 获取策略实例
    strategy_instance = Strategy.objects.get(strategy_id=strategy_id)

    # 创建Trade实例
    for trade_data in binance_trades:
        try:
            print('trade_data', trade_data)
            # 使用提供的trade_type_override或响应中的trade_type
            trade_type = trade_type_override if trade_type_override else trade_data.get('type', 'NA')

            trade = Trade(
                thirdparty_id=trade_data.get('orderId', 0),
                strategy=strategy_instance,
                symbol=trade_data.get('symbol', 'NA'),
                trade_side=trade_data.get('side', 'NA'),
                trade_type=trade_type,
                quantity=Decimal(trade_data.get('origQty', '0')),
                price=Decimal(trade_data.get('price', '0')),
                trade_group_id=trade_group_id
            )
            trade.save()

            print(f"Trade with order ID {trade.thirdparty_id} created successfully.")
        except Exception as e:
            print(f"An error occurred while processing the trade: {e}")

def create_trade(
    strategy_id: str,
    symbol: str,
    trade_side: str,
    trade_type: str,
    quantity: float,
    price: float,
    trade_group_id: str = "NA",
    thirdparty_id: str = "0"
) -> Trade:
    """
    建立單筆交易記錄

    Args:
        strategy_id: 策略ID
        symbol: 交易對符號
        trade_side: 交易方向 (BUY/SELL)
        trade_type: 交易類型
        quantity: 交易數量
        price: 交易價格
        trade_group_id: 交易組ID (選填)
        thirdparty_id: 第三方訂單ID (選填)

    Returns:
        Trade: 創建的交易實例
        
    Raises:
        Strategy.DoesNotExist: 找不到對應的策略
        Exception: 其他錯誤
    """
    try:
        # 獲取策略實例
        strategy_instance = Strategy.objects.get(strategy_id=strategy_id)
        
        # 創建 Trade 實例
        trade = Trade(
            thirdparty_id=thirdparty_id,
            strategy=strategy_instance,
            symbol=symbol,
            trade_side=trade_side,
            trade_type=trade_type,
            quantity=Decimal(str(quantity)),  # 確保轉換為 Decimal
            price=Decimal(str(price)),        # 確保轉換為 Decimal
            trade_group_id=trade_group_id
        )
        trade.save()
        
        logger.info(f"成功創建交易記錄 - Order ID: {trade.thirdparty_id}, "
                   f"Symbol: {symbol}, Side: {trade_side}, "
                   f"Quantity: {quantity}, Price: {price}")
        
        return trade
        
    except Strategy.DoesNotExist:
        logger.error(f"找不到策略 ID: {strategy_id}")
        raise
    except Exception as e:
        logger.error(f"創建交易記錄時發生錯誤: {str(e)}")
        raise

def convert_to_trade_array(response, trade_type_override=None, symbol='NA'):
    """
    将单个平仓响应对象转换为数组，并处理异常情况。

    :param response: 平仓响应对象。
    :param strategy_id: 使用的策略ID。
    :param trade_group_id: 交易组ID。
    :param trade_type_override: 覆盖交易类型（如果提供）。
    :return: 转换后的交易数组。
    """
    try:
        trade_type = trade_type_override if trade_type_override else response.get('type', 'EXIT')

        trade_data = {
            'orderId': response.get('orderId', '0'),
            'symbol': response.get('symbol', symbol),
            'side': response.get('side', 'NA'),
            'type': trade_type,
            'origQty': Decimal(response.get('origQty', '0')),
            'price': Decimal(response.get('price', '0'))
        }

        return [trade_data]

    except Exception as e:
        print(f"An error occurred: {e}")
        # 返回一个包含默认值的数组
        return [{
            'type': trade_type
        }]


def query_trades_by_group_id(trade_group_id):
    if not trade_group_id:
        return []

    trades = Trade.objects.filter(trade_group_id=trade_group_id).exclude(trade_type='MARKET')
    thirdparty_ids = [trade.thirdparty_id for trade in trades]
    return thirdparty_ids


def query_trade(trade_group_id, trade_type=None):
    """
    查詢交易記錄
    
    Args:
        trade_group_id: 交易組ID
        trade_type: 交易類型(可選)
        
    Returns:
        Trade: 符合條件的交易記錄,如果沒找到則返回 None
    """
    if not trade_group_id:
        return None

    try:
        # 根據是否有傳入 trade_type 來建立查詢條件
        if trade_type:
            trade = Trade.objects.get(
                trade_group_id=trade_group_id, 
                trade_type=trade_type
            )
        else:
            trade = Trade.objects.get(trade_group_id=trade_group_id)
            
        return trade
        
    except ObjectDoesNotExist:
        # 沒有找到符合條件的 Trade 對象
        return None
    except MultipleObjectsReturned:
        # 找到多個符合條件的 Trade 對象
        # 根據實際需求處理這種情況
        return None

def calculate_total_realized_pnl(response, order_list):
    total_realized_pnl = 0.0
    total_commission = 0.0

    for trade in response:
        if trade['orderId'] in order_list:
            total_realized_pnl += float(trade['realizedPnl'])
            total_commission += float(trade['commission'])

    # 扣除總手續費
    final_pnl = total_realized_pnl - total_commission
    return final_pnl


def update_account_balance(total_realized_pnl, strategy_id):
    if total_realized_pnl != 0:
        try:
            # 根据策略ID查找账户余额
            account_balance = AccountBalance.objects.get(strategy_id=strategy_id)

            # 累加 realized PnL 到账户余额
            account_balance.balance += Decimal(total_realized_pnl)
            account_balance.profit_loss = Decimal(total_realized_pnl)
            account_balance.save()

            return "Account balance updated successfully."
        except AccountBalance.DoesNotExist:
            return "Account balance not found for the given strategy ID."
        except Exception as e:
            return f"An error occurred: {e}"
    else:
        return "No update required as the total realized PnL is zero."


def update_trade_profit_loss(trade_group_id, total_realized_pnl, trade_type="EXIT"):
    try:
        # 使用 trade_group_id 和 trade_type 找到对应的交易
        trades = Trade.objects.filter(trade_group_id=trade_group_id, trade_type=trade_type)

        # 确保找到的交易只有一笔
        if trades.count() == 1:
            trade = trades.first()
            trade.profit_loss = total_realized_pnl
            trade.save()
            return "Trade profit/loss updated successfully."
        else:
            return "Error: No trade or multiple trades found for the given trade_group_id and trade_type 'EXIT'."
    except Exception as e:
        return f"An error occurred: {e}"


def get_main_account_info():
    try:
        # 尝试获取名称为 "main account" 的账户信息
        account = AccountInfo.objects.get(account_name="main account")
        return account
    except AccountInfo.DoesNotExist:
        # 如果没有找到该账户，返回 None 或适当的响应
        return None


def get_grid_position(strategy, grid_index):
    """
    查询是否存在特定策略和网格索引对应的且未开仓的网格位置。

    参数:
    - strategy: 策略实例
    - grid_index: 网格索引

    返回:
    - GridPosition实例，如果找到符合条件的记录；否则返回None。
    """
    try:
        return GridPosition.objects.get(
            strategy=strategy,
            grid_index=grid_index
        )
    except GridPosition.DoesNotExist:
        # 如果没有找到符合条件的记录
        return None


def get_total_quantity_for_strategy(strategy):
    """
    查詢與特定策略相關的所有開倉中(is_open=True)的網格倉位數量總和。

    參數:
    - strategy: 策略實例

    返回:
    - 開倉中倉位的累計數量
    """
    try:
        # 獲取所有與策略相關且is_open=True的GridPosition記錄
        grid_positions = GridPosition.objects.filter(
            strategy=strategy,
            is_open=True
        )

        # 累計所有quantity值
        total_quantity = sum(position.quantity for position in grid_positions)

        return total_quantity
    except Exception as e:
        # 如果查詢過程中發生錯誤
        logger.error(f"計算策略總倉位時發生錯誤: {e}")
        return 0  # 返回預設值


def close_positions_for_strategy(strategy):
    """
    关闭与指定策略相关的所有网格仓位，将它们的is_open字段设置为False。

    参数:
    - strategy: 策略实例

    返回:
    - 受影响的仓位数量。
    """
    try:
        # 使用事务来确保操作的原子性
        with transaction.atomic():
            # 更新与指定策略相关的所有GridPosition记录的is_open字段
            affected_rows = GridPosition.objects.filter(strategy=strategy, is_open=True).update(is_open=False)

        return affected_rows
    except Exception as e:
        # 如果操作过程中发生错误
        print(f"Error occurred: {e}")
        return 0  # 或返回其他合适的默认值

def create_new_grid_position(
        strategy,
        grid_index,
        quantity=0,
        entry_price=0,
        is_open=True
):
    """
    创建一个新的 GridPosition 记录。

    参数:
    - strategy: 策略实例
    - grid_index: 网格索引
    - quantity: 开仓数量，默认为0
    - entry_price: 开仓价格，默认为0
    - is_open: 是否開倉 默认为True

    返回:
    - 创建的 GridPosition 实例
    """
    new_position = GridPosition.objects.create(
        strategy=strategy,
        grid_index=grid_index,
        quantity=quantity,
        entry_price=entry_price,
        is_open=is_open  # 新创建的记录默认为开仓状态
        # 注意：其他字段如exit_price, stop_price可根据具体情况设置或保留默认值
    )
    return new_position

def execute_single_fok_order(
    client,
    symbol,
    side,
    quantity,
    price,
    quantity_precision,
    price_precision,
    max_retries=3,
    execution_timeout=20,
    base_price_adjustment=0.00005  # 基礎調整0.005%
):
    """
    執行單個FOK訂單
    
    手續費考慮：
    - 限價單(Maker): 0.02%
    - 市價單(Taker): 0.05%
    價格調整策略：
    - 初始調整：0.005%
    - 最大調整：0.015%（第三次重試）
    確保始終優於市價單手續費
    """
    formatted_quantity = format_decimal(quantity, quantity_precision)
    current_price = price
    
    for attempt in range(max_retries):
        # 每次重試增加0.005%
        price_adjustment = base_price_adjustment * (attempt + 1)  # 0.005%, 0.01%, 0.015%
        adjusted_price = current_price * (1 + price_adjustment) if side == 'BUY' else current_price * (1 - price_adjustment)
        formatted_price = format_decimal(adjusted_price, price_precision)
        
        logger.info(f"FOK訂單嘗試 {attempt + 1}/{max_retries}: "
                   f"數量={formatted_quantity}, "
                   f"價格={formatted_price} "
                   f"(調整={price_adjustment*100:.4f}%)")
        
        try:
            order = client.futures_create_order(
                symbol=symbol,
                side=side,
                type='LIMIT',
                timeInForce='FOK',
                quantity=formatted_quantity,
                price=formatted_price,
                priceProtect='TRUE'
            )
            
            # 檢查訂單狀態
            start_time = time.time()
            while time.time() - start_time < execution_timeout:
                order_status = client.futures_get_order(
                    symbol=symbol,
                    orderId=order['orderId']
                )
                
                if order_status['status'] == 'FILLED':
                    executed_qty = float(order_status['executedQty'])
                    avg_price = float(order_status['avgPrice'])
                    logger.info(f"FOK訂單成功: {executed_qty} @ {avg_price}")
                    return {
                        'success': True,
                        'order': order_status,
                        'executed_qty': executed_qty,
                        'price_adjustment': price_adjustment
                    }
                    
                elif order_status['status'] in ['EXPIRED', 'CANCELED']:
                    logger.warning(f"訂單未成交，調整價格重試")
                    break
                    
                time.sleep(0.5)
                
        except BinanceAPIException as e:
            logger.error(f"FOK訂單失敗 (嘗試 {attempt + 1}/{max_retries}): {e.message}")
            if attempt == max_retries - 1:
                break
            time.sleep(1)
            
    return {
        'success': False,
        'executed_qty': 0,
        'price_adjustment': price_adjustment if 'price_adjustment' in locals() else 0
    }

def execute_split_fok_orders(
    client, 
    symbol, 
    side, 
    total_quantity, 
    price,
    quantity_precision,
    price_precision,
    split_parts=3,
    max_workers=3,
    max_retries=3,
    execution_timeout=20,
    base_price_adjustment=0.00005  # 基礎調整0.005%
):
    """
    並行執行多個FOK訂單
    
    Args:
        client: Binance client
        symbol: 交易對
        side: 買賣方向
        total_quantity: 總數量
        price: 價格
        quantity_precision: 數量精度
        price_precision: 價格精度
        split_parts: 分割次數
        max_workers: 最大並行數
        max_retries: 每個訂單最大重試次數
        execution_timeout: 訂單超時時間
        base_price_adjustment: 基礎價格調整幅度
    """
    single_quantity = total_quantity / split_parts
    executed_orders = []
    total_executed = 0
    
    # 準備所有批次的參數
    batch_params = []
    for i in range(split_parts):
        remaining = total_quantity - total_executed
        if remaining <= 0:
            break
            
        current_quantity = remaining if i == split_parts - 1 else single_quantity
        batch_params.append({
            'client': client,
            'symbol': symbol,
            'side': side,
            'quantity': current_quantity,
            'price': price,
            'quantity_precision': quantity_precision,
            'price_precision': price_precision,
            'max_retries': max_retries,
            'execution_timeout': execution_timeout,
            'base_price_adjustment': base_price_adjustment
        })
    
    # 使用線程池並行執行
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_batch = {
            executor.submit(
                execute_single_fok_order,
                **params
            ): i for i, params in enumerate(batch_params)
        }
        
        # 收集結果
        max_adjustment = 0
        for future in concurrent.futures.as_completed(future_to_batch):
            batch_index = future_to_batch[future]
            try:
                result = future.result()
                if result['success']:
                    executed_orders.append(result['order'])
                    total_executed += result['executed_qty']
                    max_adjustment = max(max_adjustment, result['price_adjustment'])
                    logger.info(f"批次 {batch_index + 1} 執行成功")
                else:
                    logger.warning(f"批次 {batch_index + 1} 執行失敗")
            except Exception as e:
                logger.error(f"批次 {batch_index + 1} 執行出錯: {str(e)}")
    
    # 返回執行結果
    return {
        'success': total_executed > 0,
        'orders': executed_orders,
        'total_executed': total_executed,
        'remaining_qty': total_quantity - total_executed,
        'execution_count': len(executed_orders),
        'attempted_parts': split_parts,
        'max_price_adjustment': max_adjustment,
        'avg_price': sum(float(o['avgPrice']) * float(o['executedQty']) 
                        for o in executed_orders) / total_executed if total_executed > 0 else 0
    }

# 在類別定義前添加常數定義
class OrderStatus:
    NEW = 'NEW'                    # 新訂單
    PARTIALLY_FILLED = 'PARTIALLY_FILLED'  # 部分成交
    FILLED = 'FILLED'              # 完全成交
    CANCELED = 'CANCELED'          # 已取消
    REJECTED = 'REJECTED'          # 被拒絕
    EXPIRED = 'EXPIRED'            # 已過期
    PENDING_CANCEL = 'PENDING_CANCEL'  # 待取消

class OrderType:
    LIMIT = 'LIMIT'                # 限價單
    MARKET = 'MARKET'              # 市價單
    STOP = 'STOP'                  # 止損單
    STOP_MARKET = 'STOP_MARKET'    # 市價止損
    TAKE_PROFIT = 'TAKE_PROFIT'    # 止盈單
    TAKE_PROFIT_MARKET = 'TAKE_PROFIT_MARKET'  # 市價止盈
    TRAILING_STOP_MARKET = 'TRAILING_STOP_MARKET'  # 跟蹤止損

class OrderSide:
    BUY = 'BUY'                    # 買入
    SELL = 'SELL'                  # 賣出

class PositionSide:
    BOTH = 'BOTH'                  # 雙向持倉
    LONG = 'LONG'                  # 只做多
    SHORT = 'SHORT'                # 只做空

class BinanceWebsocketClient:
    def __init__(self, api_key, api_secret, callback=None, custom_logger=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.twm = None
        self.is_running = False
        self.reconnect_count = 0
        self.max_reconnects = 10
        self.reconnect_delay = 5
        self.last_receive_time = time.time()
        self._lock = threading.Lock()
        self.heartbeat_thread = None
        self.callback = callback if callback else lambda x: None
        self.logger = custom_logger if custom_logger else logger
        # 創建線程池
        self.thread_pool = ThreadPoolExecutor(
            max_workers=10,
            thread_name_prefix="WebsocketCallback"
        )

    def handle_socket_message(self, msg):
        """處理websocket訊息的回調函數"""
        try:
            self.last_receive_time = time.time()
            if msg['e'] == 'ORDER_TRADE_UPDATE':
                order = msg['o']
                # 使用 OrderStatus 類來檢查訂單狀態
                if order['X'] not in [OrderStatus.CANCELED, OrderStatus.NEW]:
                    self.logger.info(f"""
訂單更新詳細信息:
事件類型: {msg['e']}
事件時間: {msg['E']}
交易對: {order['s']}
客戶端訂單ID: {order['c']}
訂單方向: {order['S']}
訂單類型: {order['o']}
訂單價格: {order['p']}
訂單數量: {order['q']}
訂單狀態: {order['X']}
訂單ID: {order['i']}
最後成交數量: {order['l']}
已成交數量: {order['z']}
最後成交價格: {order['L']}
手續費資產: {order['N']}
手續費數量: {order['n']}
已實現盈虧: {order['rp']}
訂單時間: {order['T']}
成交時間: {order['t']}
""")
                # 檢查是否還在運行中
                if self.is_running and self.thread_pool:
                    self.thread_pool.submit(self.callback, msg)
                
        except Exception as e:
            self.logger.error(f"處理訊息時發生錯誤: {str(e)}")

    def start_websocket(self):
        """啟動websocket連接"""
        with self._lock:  # 使用線程鎖
            try:
                if self.twm and self.twm.is_alive():
                    self.stop_websocket()
                    time.sleep(1)  # 等待舊連接完全關閉

                self.twm = ThreadedWebsocketManager(
                    api_key=self.api_key,
                    api_secret=self.api_secret
                )
                self.twm.start()
                self.is_running = True
                
                # 訂閱用戶數據流
                self.twm.start_futures_socket(
                    callback=self.handle_socket_message
                )
                
                self.logger.info("Websocket連接已啟動")
                return True
                
            except Exception as e:
                self.logger.error(f"啟動Websocket時發生錯誤: {str(e)}")
                return False

    def stop_websocket(self):
        """停止websocket連接"""
        with self._lock:  # 使用線程鎖
            try:
                self.is_running = False
                
                # 先關閉 WebSocket 連接
                if self.twm:
                    self.twm.stop()
                    self.twm = None
                
                # 關閉線程池前等待所有任務完成
                if hasattr(self, 'thread_pool') and self.thread_pool:
                    self.thread_pool.shutdown(wait=True)
                    self.thread_pool = None
                    
                self.logger.info("Websocket連接已停止")
            except Exception as e:
                self.logger.error(f"停止Websocket時發生錯誤: {str(e)}")

    def __del__(self):
        """析構函數，確保資源被釋放"""
        try:
            self.stop_websocket()
        except Exception as e:
            if self.logger:  # 確保 logger 還存在
                self.logger.error(f"清理 WebSocket 客戶端時發生錯誤: {str(e)}")

    def _check_connection(self):
        """檢查連接狀態"""
        while self.is_running:
            try:
                time.sleep(30)  # 每30秒檢查一次
                if not self.twm or not self.twm.is_alive():
                    self.logger.warning("檢測到連接已斷開，嘗試重新連接...")
                    if self.start_websocket():
                        self.reconnect_count = 0
                    else:
                        self.reconnect_count += 1
                        if self.reconnect_count >= self.max_reconnects:
                            self.logger.error("達到最大重連次數，停止服務")
                            self.stop_websocket()
                            break
            except Exception as e:
                self.logger.error(f"連接檢查時發生錯誤: {str(e)}")

    def run(self):
        """運行主循環"""
        try:
            if self.start_websocket():
                # 啟動心跳檢查線程
                self.heartbeat_thread = threading.Thread(target=self._check_connection)
                self.heartbeat_thread.daemon = True
                self.heartbeat_thread.start()

                while self.is_running:
                    time.sleep(1)
                    # print("running")
                    
        except KeyboardInterrupt:
            self.logger.info("收到終止信號")
        finally:
            self.stop_websocket()

def get_grid_positions_by_strategy(strategy, ascending=True):
    """
    查詢特定策略的所有網格倉位。

    參數:
    - strategy: 策略實例
    - ascending: 是否按網格索引升序排列，預設True（由小到大）

    返回:
    - list[GridPosition]: 該策略的所有網格倉位列表，如果沒有則返回空列表。
    """
    try:
        # 根據 ascending 參數決定排序方式
        order_field = 'grid_index' if ascending else '-grid_index'
        
        positions = GridPosition.objects.filter(
            strategy=strategy
        ).order_by(order_field)
        
        return list(positions)
    except Exception as e:
        logger.error(f"查詢策略網格倉位時發生錯誤: {e}")
        return []

def update_grid_positions_price(strategy, levels: list[float]):
    """
    更新策略網格倉位的進出場價格
    
    Args:
        strategy: 策略實例
        levels: 網格價格陣列(包含上下界)
        
    Returns:
        bool: 更新是否成功
    """
    try:
        positions = get_grid_positions_by_strategy(strategy=strategy)
        positions_to_update = []
        
        for position in positions:
            grid_index = position.grid_index
            if grid_index < len(levels) - 1:
                position.entry_price = levels[grid_index]
                position.exit_price = levels[grid_index + 1]
                positions_to_update.append(position)
                
        # 使用 bulk_update 一次性更新所有記錄
        if positions_to_update:
            GridPosition.objects.bulk_update(
                positions_to_update, 
                ['entry_price', 'exit_price']
            )
                
        logger.info(f"策略 {strategy.strategy_id} 的網格倉位價格已更新")
        return True
        
    except Exception as e:
        logger.error(f"更新網格倉位價格時發生錯誤: {e}")
        return False

def generate_grid_levels(lower_bound: float, upper_bound: float, grids: int, symbol: str) -> list[float]:
    """
    生成網格交易的價格點位陣列
    
    Args:
        lower_bound: 最低價格
        upper_bound: 最高價格
        grids: 網格數量
        symbol: 交易對符號
        
    Returns:
        list[float]: 由低到高排序的價格點位陣列
    """
    # 包含上下界
    _grids = grids + 1
    tick_size = float(exchange_info_map[symbol]["tickSize"])
    price_precision = int(exchange_info_map[symbol]['pricePrecision'])

    if lower_bound >= upper_bound:
        raise ValueError("下界必須小於上界")
    if grids < 2:
        raise ValueError("網格數量必須大於等於2")
        
    grid_size = (upper_bound - lower_bound) / (_grids - 1)
    
    grid_levels = []
    for i in range(_grids):
        price = lower_bound + (grid_size * i)
        # 先根據 tick size 調整價格，再根據 price precision 進行四捨五入
        adjusted_price = round(round(price / tick_size) * tick_size, price_precision)
        grid_levels.append(adjusted_price)
    
    return grid_levels

def generate_trade_group_id():
    """
    生成交易群組ID
    格式: yyyyMMddHHmmss-{uuid最後12位}
    
    Returns:
        str: 交易群組ID
    """
    now = datetime.now()
    date_str = now.strftime('%Y%m%d%H%M%S')
    uuid_str = str(uuid.uuid4()).split('-')[-1]  # 取最後一段
    return f"{date_str}-{uuid_str}"

def get_grid_quantity(symbol, balance, leverage, leverage_rate, mark_price, min_notional=5.0, balance_rate=0.003):
    # balance_amount = min(float(balance.balance), float(balance.available_margin))
    balance_amount = float(balance.available_margin)
    quantity_by_balance = balance_amount * balance_rate * leverage * float(leverage_rate) / mark_price  # 根據餘額計算
    quantity_by_min_notional = int(min_notional / float(mark_price)) + 1
    quantity = max(quantity_by_min_notional, quantity_by_balance)
    # logger.info(f"{leverage} {leverage_rate} {mark_price}")
    return format_decimal_symbol_quantity(symbol, quantity)

def grid_v2_create_batch_payload(strategy, current_grid_index, mark_price, logger=logger):
    """
    生成網格交易的批次掛單資料，每批最多5個訂單
    https://developers.binance.com/docs/zh-CN/derivatives/usds-margined-futures/trade/rest-api/Place-Multiple-Orders

    Args:
        strategy: 策略實例
        current_grid_index: 當前網格索引
        mark_price: 當前標記價格
        
    Returns:
        list: 批次掛單資料陣列的列表，每個陣列最多包含5個訂單
    """
    try:
        # 檢查策略資訊
        symbol = strategy.symbol
        balance = get_balance_by_symbol(symbol)
        
        # 取得所有網格倉位並檢查
        positions = get_grid_positions_by_strategy(strategy=strategy)
        # logger.info(f"Raw positions query result: {positions}")
        
        if not positions:
            logger.error("No positions found for strategy")
            return []
            
        all_orders = []
        current_batch = []
        trade_group_id = generate_trade_group_id()
        
        logger.info(f"Number of positions found: {len(positions)}")
        
        for i, position in enumerate(positions):
            if position.grid_index == current_grid_index:
                continue
            
            # 根據網格位置決定交易方向
            side = 'BUY' if position.grid_index < current_grid_index else 'SELL'
            # 根據目前持倉方向和交易方向決定槓桿率
            # leverage_rate = float(strategy.leverage_rate if side == 'BUY' else strategy.short_leverage_rate)
            if balance.position_amount < 0:  # 目前持空單
                leverage_rate = float(strategy.short_leverage_rate if side == 'SELL' else 1.0)
            else:  # 目前持多單或無倉位
                leverage_rate = float(strategy.leverage_rate if side == 'BUY' else 1.0)
            order_price = position.entry_price if side == 'BUY' else position.exit_price
            
            # 使用 get_grid_quantity 計算數量
            quantity = get_grid_quantity(
                symbol=symbol,
                balance=balance,
                leverage=strategy.leverage,
                leverage_rate=leverage_rate,
                mark_price=float(order_price)  # 根據交易方向使用不同價格
            )
            
            # 基本訂單資訊
            order = {
                'symbol': strategy.symbol,
                'type': 'LIMIT',
                'timeInForce': 'GTC',
                'quantity': quantity,
                'newClientOrderId': f"{trade_group_id}_{position.grid_index}",
            }
            
            # 設定價格和方向
            order.update({
                'price': format_decimal_symbol_price(symbol, order_price),
                'side': side
            })
            
            current_batch.append(order)
            # logger.info(f"Added order to current batch: {order} (position {i+1})")
            
            # 當前批次達到5個訂單時，將其加入all_orders並重置current_batch
            if len(current_batch) == 5:
                all_orders.append(current_batch)
                current_batch = []
        
        # 處理剩餘的訂單
        if current_batch:
            all_orders.append(current_batch)
        
        logger.info(f"Total batches created: {len(all_orders)}, "
                   f"Total orders: {sum(len(batch) for batch in all_orders)}")
        return all_orders
        
    except Exception as e:
        logger.error(f"生成批次掛單資料時發生錯誤: {str(e)}")
        logger.error(f"Error traceback: ", exc_info=True)
        return []

def get_position_info(client, symbol=None):
    """
    獲取合約持倉資訊。若不指定 symbol，則返回所有持倉
    
    Args:
        client: Binance client
        symbol: 交易對，預設 None (返回所有持倉)
        
    Returns:
        如果指定 symbol: 返回單一幣種的持倉資訊 dict 或 None
        如果未指定 symbol: 返回所有持倉資訊的 list
    """
    try:
        positions = client.futures_position_information()
        
        # 過濾出有持倉量的倉位（positionAmt 不為 0）
        active_positions = [
            {
                'symbol': pos['symbol'],
                'position_amount': float(pos['positionAmt']),
                'entry_price': float(pos['entryPrice']),
                'unrealized_pnl': float(pos['unRealizedProfit']),
                'leverage': float(pos['leverage']),
                'mark_price': float(pos['markPrice']),
                'liquidation_price': float(pos['liquidationPrice']),
                'isolated': pos['isolated'],
                # 計算持倉價值和保證金
                'position_value': abs(float(pos['positionAmt']) * float(pos['entryPrice'])),
                'margin': abs(float(pos['positionAmt']) * float(pos['entryPrice'])) / float(pos['leverage'])
            }
            for pos in positions
            if float(pos['positionAmt']) != 0
        ]
        
        if symbol:
            # 如果指定了 symbol，返回該幣種的持倉資訊
            for pos in active_positions:
                if pos['symbol'] == symbol:
                    return pos
            return None
        else:
            # 如果未指定 symbol，返回所有持倉資訊
            return active_positions
            
    except Exception as e:
        logger.error(f"獲取持倉資訊時發生錯誤: {str(e)}")
        return None

def update_all_future_positions(client):
    """
    取得所有合約持倉的摘要資訊並更新資料庫
    
    Args:
        client: Binance client
        
    Returns:
        dict: 需要平倉的 symbol 對應的訂單資訊，例如 {"ONDOUSDT": {...平倉訂單...}}
    """
    positions = get_position_info(client)
    close_orders_map = {}
    
    if not positions:
        logger.info("目前沒有任何合約持倉")
        return close_orders_map
    
    total_pnl = 0
    total_margin = 0
    balances_to_update = []
    
    logger.info("\n=== 合約持倉摘要 ===")
    
    for pos in positions:
        total_pnl += pos['unrealized_pnl']
        total_margin += pos['margin']
        symbol = pos['symbol']
        strategy = Strategy.objects.get(symbol=symbol, status='ACTIVE')
        balance = AccountBalance.objects.get(strategy=strategy)
        
        if balance:
            margin = pos['margin']
            current_balance = float(balance.balance)
            available_margin = float(balance.available_margin)
            denominator = max(current_balance, available_margin)
            hold_rate = margin/denominator if denominator > 0 else 0
            position_amount = float(pos['position_amount'])
            
            # 根據持倉方向判斷使用的持倉率限制
            is_short = position_amount < 0
            max_hold_rate = strategy.short_hold_rate if is_short else strategy.hold_rate
            
            # 檢查是否超過策略設定的持倉率
            if hold_rate > max_hold_rate:
                # 計算需要平倉的數量（根據策略設定的縮減率）
                close_quantity = abs(position_amount) * float(strategy.hold_reduce_rate)
                
                # 決定平倉方向
                close_side = 'SELL' if position_amount > 0 else 'BUY'
                
                # 創建平倉訂單
                close_order = {
                    'symbol': symbol,
                    'side': close_side,
                    'type': 'MARKET',
                    'quantity': format_decimal_symbol_quantity(symbol, close_quantity),
                    'reduceOnly': 'true'  # 確保這是平倉訂單
                }
                
                # 將訂單加入 map
                close_orders_map[symbol] = close_order
            
            logger.info(f"""
{pos['symbol']}:
已投入保證金: {pos['margin']:.8f} USDT
剩餘資金: {balance.balance:.8f} USDT
持倉數量: {pos['position_amount']} ({"空倉" if float(pos['position_amount']) < 0 else "多倉"})
持倉均價: {pos['entry_price']}
持倉價值: {pos['position_value']:.8f} USDT
持倉上限: {strategy.short_hold_rate if float(pos['position_amount']) < 0 else strategy.hold_rate}
持倉比例 {hold_rate:.8} {"(超過限制)" if hold_rate > (strategy.short_hold_rate if float(pos['position_amount']) < 0 else strategy.hold_rate) else ""}
未實現盈虧: {pos['unrealized_pnl']}
槓桿倍數: {pos['leverage']}x
標記價格: {pos['mark_price']}
強平價格: {pos['liquidation_price']}
保證金模式: {"逐倉" if pos['isolated'] else "全倉"}
------------------------""")
            
            # 更新 balance 資料
            balance.used_margin = float(pos['margin'])
            balance.unrealized_pnl = float(pos['unrealized_pnl'])
            balance.position_value = float(pos['position_value'])
            balance.position_amount = float(pos['position_amount'])
            balances_to_update.append(balance)
    
    # 使用 bulk_update 一次性更新所有 balance
    if balances_to_update:
        AccountBalance.objects.bulk_update(
            balances_to_update, 
            ['used_margin', 'unrealized_pnl', 'position_value', 
             'position_amount']
        )
        
    logger.info(f"""
=== 總計 ===
總未實現盈虧: {total_pnl:.2f} USDT
總投入保證金: {total_margin:.2f} USDT
投資報酬率: {(total_pnl/total_margin*100):.2f}% (未實現盈虧/總保證金)
""")

    return close_orders_map

def get_strategy_by_symbol(symbol):
    return Strategy.objects.get(symbol=symbol, status='ACTIVE')

def get_balance_by_symbol(symbol):
    """
    通過交易對符號查詢對應的帳戶餘額
    
    Args:
        symbol: 交易對符號
        
    Returns:
        Decimal: 帳戶餘額，如果找不到則返回 None
    """
    try:
        strategy = Strategy.objects.get(symbol=symbol, status='ACTIVE')
        account_balance = AccountBalance.objects.get(strategy=strategy)
        return account_balance
    except (Strategy.DoesNotExist, AccountBalance.DoesNotExist):
        logger.warning(f"找不到 {symbol} 對應的活躍策略帳戶餘額")
        return None

def get_current_price(client, symbol):
    """
    獲取指定合約的當前價格
    
    Args:
        client: Binance client
        symbol: 交易對符號 (例如: 'BTCUSDT')
        
    Returns:
        dict: {
            'symbol': 交易對符號,
            'mark_price': 標記價格,
            'index_price': 指數價格,
            'estimated_settle_price': 預估結算價格,
            'last_funding_rate': 最後一次資金費率,
            'next_funding_time': 下次資金費率時間,
            'timestamp': 時間戳
        }
        發生錯誤時返回 None
    """
    try:
        price_info = client.futures_mark_price(symbol=symbol)
        
        # 轉換數據類型並整理返回格式
        return {
            'symbol': price_info['symbol'],
            'mark_price': float(price_info['markPrice']),
            'index_price': float(price_info['indexPrice']),
            'estimated_settle_price': float(price_info['estimatedSettlePrice']),
            'last_funding_rate': float(price_info['lastFundingRate']),
            'next_funding_time': int(price_info['nextFundingTime']),
            'timestamp': int(price_info['time'])
        }
        
    except BinanceAPIException as e:
        logger.error(f"獲取 {symbol} 價格時發生 Binance API 錯誤: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"獲取 {symbol} 價格時發生未預期錯誤: {str(e)}")
        return None

# 使用字典存儲每個 symbol 的鎖
_symbol_locks = defaultdict(threading.Lock)

def grid_v2_lab(client, passphrase, symbol):
    """
    網格交易實驗方法，使用基於 symbol 的同步鎖
    
    Args:
        client: Binance client
        passphrase: 策略密碼
        symbol: 交易對符號
    """
    # 獲取該 symbol 的專屬鎖
    with _symbol_locks[symbol]:
        try:
            logger.info(f"開始執行網格交易實驗 - Symbol: {symbol}")
            
            # 取消所有掛單，最多重試3次
            max_retries = 3
            retry_delay = 0.1
            
            for attempt in range(max_retries):
                try:
                    client.futures_cancel_all_open_orders(symbol=symbol)
                    logger.info(f"成功取消 {symbol} 所有掛單")
                    break
                    
                except Exception as e:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"取消掛單失敗 (嘗試 {attempt + 1}/{max_retries}): {str(e)}")
                        logger.info(f"等待 {wait_time} 秒後重試...")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"取消掛單最終失敗: {str(e)}")
                        raise

            price = get_current_price(client, symbol)
            mark_price = price['mark_price']
            upper_bound = mark_price * (1 + 0.025)
            lower_bound = mark_price * (1 - 0.025)
            levels = generate_grid_levels(lower_bound, upper_bound, 10, symbol)
            logger.info(levels)
            strategy = find_strategy_by_passphrase(passphrase)
            leverage = strategy.leverage
            # client.futures_change_leverage(symbol=symbol, leverage=leverage)
            update_grid_positions_price(strategy, levels)
            batch_payloads = grid_v2_create_batch_payload(strategy=strategy, current_grid_index=5, mark_price=mark_price)
            # print(f'batch_payloads {batch_payloads}')
            # 分批發送訂單
            for batch in batch_payloads:
                logger.info(f"Sending batch order: {json.dumps(batch)}")
                response = client.futures_place_batch_order(batchOrders=json.dumps(batch))
                print(response)

        except Exception as e:
            logger.error(f"網格交易實驗執行失敗 - Symbol: {symbol}, Error: {str(e)}")
            logger.error("錯誤詳情:", exc_info=True)
            raise

def update_balance_and_pnl_by_custom_order_id(
    client,
    symbol: str,
    trade_group_id: str,
    thirdparty_id: str,
    strategy_id: str,
    trade_type: str = "EXIT",
    update_profit_loss: bool = True,
    use_api: bool = True
) -> None:
    """
    透過自定義訂單ID更新策略餘額和已實現盈虧
    
    Args:
        symbol: 交易幣種
        trade_group_id: 交易組ID 
        strategy_id: 策略ID
        trade_type: 交易類型 (預設為 "EXIT")
        update_profit_loss: 是否更新已實現盈虧 (預設為 True)
        use_api: 是否使用API獲取成交資訊 (預設為 True)
    """
    try:
        # 查詢相關訂單
        trade = query_trade(trade_group_id)
        if not trade or trade_group_id == "NA":
            logger.warning(f"找不到交易組 {trade_group_id} 的訂單")
            return

        if use_api:
            # 使用API查詢成交記錄
            try:
                trades = client.futures_account_trades(
                    symbol=symbol,
                    orderId=trade.thirdparty_id
                )
                # 計算已實現盈虧
                total_realized_pnl = calculate_total_realized_pnl(trades, [trade.thirdparty_id])
            except Exception as e:
                logger.error(f"查詢訂單 {trade.thirdparty_id} 的交易記錄時發生錯誤: {str(e)}")
                return
        else:
            max_attempts = 3
            base_delay = 1  # 100毫秒
            
            for attempt in range(max_attempts):
                try:
                    with transaction.atomic():
                        executions = OrderExecution.objects.select_for_update(nowait=True).filter(
                            order_id=thirdparty_id,
                            symbol=symbol
                        ).order_by('execution_time')
                        
                        if executions:
                            total_realized_pnl = sum(execution.realized_pnl - execution.commission for execution in executions)
                            break
                        else:
                            if attempt <= max_attempts - 1:
                                delay = base_delay * (2 ** attempt)  # 指數退避：0.1s, 0.2s, 0.4s
                                logger.info(f"等待 OrderExecution 記錄，重試 {attempt + 1}/{max_attempts}，延遲 {delay}s")
                                time.sleep(delay)
                            else:
                                logger.warning(f"找不到交易組 {thirdparty_id} 的成交記錄")
                                return
                except DatabaseError as e:
                    if attempt < max_attempts - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.info(f"資料庫訪問衝突，重試 {attempt + 1}/{max_attempts}，延遲 {delay}s")
                        time.sleep(delay)
                    else:
                        raise

        logger.info(f"總已實現盈虧: {total_realized_pnl}")

        # 根據參數決定是否更新已實現盈虧
        if update_profit_loss:
            # 更新策略餘額
            update_result = update_account_balance(total_realized_pnl, strategy_id)
            logger.info(f"更新帳戶餘額結果: {update_result}")
            update_result = update_trade_profit_loss(
                trade_group_id, 
                total_realized_pnl, 
                trade_type
            )
            logger.info(f"更新交易盈虧結果: {update_result}")

    except Exception as e:
        logger.error(f"更新餘額和盈虧時發生錯誤: {str(e)}")
        logger.error("錯誤詳情:", exc_info=True)

def create_order_execution(
    strategy: 'Strategy',
    order: dict,
    execution_type: str
) -> 'OrderExecution':
    """
    創建訂單執行記錄
    
    Args:
        strategy: Strategy 模型實例
        order: Binance WebSocket 訂單資訊
        execution_type: 執行類型 (PARTIAL 或 FULL)
    
    Returns:
        OrderExecution: 創建的訂單執行記錄實例
    """
    try:
        execution = OrderExecution.objects.create(
            strategy=strategy,
            binance_execution_id=str(order['t']),  # 成交ID
            execution_type=execution_type,
            symbol=order['s'],
            order_id=str(order['i']),
            client_order_id=order['c'],
            side=order['S'],
            price=Decimal(str(order['L'])),  # 最後成交價格
            quantity=Decimal(str(order['l'])),  # 最後成交數量
            commission=Decimal(str(order['n'])),
            commission_asset=order['N'],
            realized_pnl=Decimal(str(order['rp'])),
            execution_time=datetime.fromtimestamp(order['T'] / 1000.0)
        )
        
        logger.info(
            f"成功記錄訂單執行 - Order ID: {order['i']}, "
            f"執行類型: {execution_type}, "
            f"成交數量: {order['l']}, 成交價格: {order['L']}"
        )
        
        return execution
        
    except Exception as e:
        logger.error(f"創建訂單執行記錄時發生錯誤: {str(e)}")
        logger.error("錯誤詳情:", exc_info=True)
        raise

def reduce_leverage(strategy: 'Strategy', side: str = 'LONG') -> None:
    """
    降低策略的槓桿率
    
    Args:
        strategy: Strategy 模型實例
        side: 交易方向，預設為 'LONG'，可選 'LONG' 或 'SHORT'
    """
    try:
        if side == 'LONG':
            # 計算多倉新的槓桿率
            new_leverage_rate = float(strategy.leverage_rate) * float(strategy.reduce_rate)
            # 更新策略的多倉槓桿率
            strategy.leverage_rate = Decimal(str(new_leverage_rate))
        else:
            # 計算空倉新的槓桿率
            new_leverage_rate = float(strategy.short_leverage_rate) * float(strategy.reduce_rate)
            # 更新策略的空倉槓桿率
            strategy.short_leverage_rate = Decimal(str(new_leverage_rate))
            
        strategy.save()
        
        logger.info(f"""
槓桿率調整:
策略ID: {strategy.strategy_id}
交易對: {strategy.symbol}
交易方向: {'多倉' if side == 'LONG' else '空倉'}
調整後槓桿率: {new_leverage_rate}
------------------------""")
        
    except Exception as e:
        logger.error(f"調整槓桿率時發生錯誤: {str(e)}")
        logger.error("錯誤詳情:", exc_info=True)


def recover_leverage(strategy: 'Strategy') -> None:
    """
    恢復策略的多空槓桿率,但不超過1
    
    Args:
        strategy: Strategy 模型實例
    """
    try:
        # 計算新的多倉槓桿率
        new_long_leverage_rate = float(strategy.leverage_rate) * (1.0 + float(strategy.recover_rate))
        # 確保不超過上限1
        new_long_leverage_rate = min(new_long_leverage_rate, 1.0)
        
        # 計算新的空倉槓桿率
        new_short_leverage_rate = float(strategy.short_leverage_rate) * (1.0 + float(strategy.recover_rate))
        # 確保不超過上限1
        new_short_leverage_rate = min(new_short_leverage_rate, 1.0)
        
        # 更新策略的多空槓桿率
        strategy.leverage_rate = Decimal(str(new_long_leverage_rate))
        strategy.short_leverage_rate = Decimal(str(new_short_leverage_rate))
        strategy.save()
        
        logger.info(f"""
槓桿率恢復:
策略ID: {strategy.strategy_id}
交易對: {strategy.symbol}
調整後多倉槓桿率: {strategy.leverage_rate}
調整後空倉槓桿率: {strategy.short_leverage_rate}
------------------------""")
        
    except Exception as e:
        logger.error(f"恢復槓桿率時發生錯誤: {str(e)}")
        logger.error("錯誤詳情:", exc_info=True)


def risk_control(client, symbol: str, close_order: dict) -> bool:
    """
    執行風險控制平倉操作
    
    Args:
        client: Binance client
        symbol: 交易對符號
        close_order: 平倉訂單資訊
        
    Returns:
        bool: 平倉是否成功
    """
    try:
        # 優先使用傳入的 newClientOrderId，若沒有則生成新的
        trade_group_id = generate_trade_group_id()
        close_order['newClientOrderId'] = trade_group_id
        
        # 執行平倉訂單
        logger.warning(f"""
開始執行風險控制平倉:
交易對: {symbol}
平倉方向: {close_order['side']}
平倉數量: {close_order['quantity']}
訂單類型: {close_order['type']}
交易組ID: {trade_group_id}
------------------------""")
        
        # 將訂單包裝成批次訂單格式
        batch_payload = [close_order]
        
        # 執行批次訂單
        response = client.futures_place_batch_order(
            batchOrders=json.dumps(batch_payload)
        )
        
        # 如果訂單成功執行，創建交易記錄
        if response:
            strategy = get_strategy_by_symbol(symbol)
            
            # 創建交易記錄
            Trade.objects.create(
                strategy_id=strategy.strategy_id,
                trade_group_id=trade_group_id+"_r",
                thirdparty_id=response[0]['orderId'],
                symbol=symbol,
                trade_side=close_order['side'],
                quantity=close_order['quantity'],
                trade_type="RISK_CONTROL"  # 標記為風險控制交易
            )
            
            logger.info(f"風險控制平倉訂單執行成功: {response}")
            
            # 根據平倉方向決定要調整的槓桿率
            # 當平倉方向為 SELL 時表示平掉多倉，所以要調整多倉槓桿率
            # 當平倉方向為 BUY 時表示平掉空倉，所以要調整空倉槓桿率
            leverage_side = 'LONG' if close_order['side'] == 'SELL' else 'SHORT'
            reduce_leverage(strategy, leverage_side)
            return True
            
    except Exception as e:
        logger.error(f"執行風險控制平倉時發生錯誤: {str(e)}")
        logger.error("錯誤詳情:", exc_info=True)
        return False
        
    return False


def recover_all_active_strategy_leverage():
    """每天午夜執行恢復所有活躍策略的槓桿率"""
    try:
        # 直接使用 filter 獲取 QuerySet
        active_strategies = Strategy.objects.filter(status='ACTIVE')

        logger.info(f"""
=== 開始執行槓桿率恢復排程 ===
活躍策略數量: {len(active_strategies)}
------------------------""")

        for strategy in active_strategies:
            try:
                recover_leverage(strategy)
            except Exception as e:
                logger.error(f"恢復策略 {strategy.strategy_id} 槓桿率時發生錯誤: {str(e)}")
                continue

        logger.info("=== 槓桿率恢復排程執行完成 ===")

    except Exception as e:
        logger.error(f"執行槓桿率恢復排程時發生錯誤: {str(e)}")
        logger.error("錯誤詳情:", exc_info=True)

def update_balance_from_execution(strategy_id: int, realized_pnl: float, commission: float) -> None:
    """
    根據成交記錄更新策略餘額
    
    Args:
        strategy_id: 策略ID
        realized_pnl: 已實現盈虧
        commission: 手續費
    """
    try:
        # 查找對應的帳戶餘額記錄
        account_balance = AccountBalance.objects.get(strategy_id=strategy_id)
        
        # 轉換為 Decimal 並保持原始精度
        realized_pnl_decimal = Decimal(str(realized_pnl))
        commission_decimal = Decimal(str(commission))
        
        # 計算淨收益時不進行捨入
        net_profit = realized_pnl_decimal - commission_decimal
        
        # 更新餘額時也保持原始精度
        account_balance.balance += net_profit
        account_balance.profit_loss = net_profit
        account_balance.save()
        
        logger.info(f"已更新策略 {strategy_id} 的餘額，淨收益: {net_profit}")
        
    except AccountBalance.DoesNotExist:
        logger.error(f"找不到策略 {strategy_id} 的帳戶餘額記錄")
    except Exception as e:
        logger.error(f"更新餘額時發生錯誤: {str(e)}")

def create_trade_from_ws_order(order: dict, strategy, trade_type: str) -> None:
    """
    從 WebSocket 訂單消息創建交易記錄
    
    Args:
        order: WebSocket 訂單消息
        strategy: 策略對象
        trade_type: 交易類型
    """
    try:
        trade_group_id = order.get('c', 'NA')  # 從訂單中獲取 clientOrderId
        
        Trade.objects.create(
            strategy=strategy,
            trade_group_id=trade_group_id,
            thirdparty_id=order['i'],  # orderId
            symbol=order['s'],         # symbol
            trade_side=order['S'],     # side
            trade_type=trade_type,
            quantity=Decimal(str(order['l'])),  # 最後成交數量
            price=Decimal(str(order['L'])),     # 最後成交價格
            profit_loss=Decimal(str(order.get('rp', '0'))),  # 已實現盈虧
            cumulative_profit_loss=Decimal('0')  # 初始化為0
        )
        
        logger.info(f"成功創建交易記錄 - Order ID: {order['i']}, Symbol: {order['s']}")
        
    except Exception as e:
        logger.error(f"創建交易記錄時發生錯誤: {str(e)}")
        logger.error("錯誤詳情:", exc_info=True)

def create_balance_history_snapshot():
    """
    為所有活躍策略創建餘額歷史快照
    """
    try:
        # 獲取所有活躍策略
        active_strategies = Strategy.objects.filter(status='ACTIVE')
        snapshots = []
        
        for strategy in active_strategies:
            try:
                # 獲取當前餘額記錄
                balance = AccountBalance.objects.get(strategy=strategy)
                
                # 創建歷史記錄，為可能為空的欄位設置預設值
                snapshot = AccountBalanceHistory(
                    strategy=strategy,
                    balance=balance.balance or Decimal('0'),
                    equity=balance.equity or Decimal('1.0'),
                    available_margin=balance.available_margin or Decimal('0'),
                    used_margin=balance.used_margin or Decimal('0'),
                    unrealized_pnl=balance.unrealized_pnl or Decimal('0'),
                    position_value=balance.position_value,  # 允許為 null
                    position_amount=balance.position_amount  # 允許為 null
                )
                snapshots.append(snapshot)
                
            except AccountBalance.DoesNotExist:
                logger.warning(f"找不到策略 {strategy.strategy_id} 的餘額記錄")
                continue
            except Exception as e:
                logger.error(f"處理策略 {strategy.strategy_id} 的餘額快照時發生錯誤: {str(e)}")
                continue
        
        # 批量創建歷史記錄
        if snapshots:
            AccountBalanceHistory.objects.bulk_create(snapshots)
            logger.info(f"成功創建 {len(snapshots)} 筆餘額歷史記錄")
            
    except Exception as e:
        logger.error(f"創建餘額歷史快照時發生錯誤: {str(e)}")

def generate_grid_positions(strategy_id: int, grid_count: int) -> bool:
    """
    生成指定策略的網格倉位記錄
    
    Args:
        strategy_id: 策略ID
        grid_count: 網格數量
        
    Returns:
        bool: 是否成功生成記錄
    """
    try:
        # 獲取策略實例
        strategy = Strategy.objects.get(strategy_id=strategy_id)
        
        # 檢查是否已存在網格倉位
        existing_positions = GridPosition.objects.filter(strategy=strategy)
        if existing_positions.exists():
            logger.warning(f"策略 {strategy_id} 已存在網格倉位記錄")
            return False
            
        # 準備批量創建的資料
        positions_to_create = []
        for i in range(grid_count):
            positions_to_create.append(
                GridPosition(
                    strategy=strategy,
                    grid_index=i,
                    quantity=0,
                    entry_price=0,
                    exit_price=0,
                    is_open=False,
                    trade_group_id=''
                )
            )
            
        # 批量創建記錄
        GridPosition.objects.bulk_create(positions_to_create)
        
        logger.info(f"成功為策略 {strategy_id} 生成 {grid_count} 筆網格倉位記錄")
        return True
        
    except Strategy.DoesNotExist:
        logger.error(f"找不到策略 {strategy_id}")
        return False
    except Exception as e:
        logger.error(f"生成網格倉位記錄時發生錯誤: {str(e)}")
        return False

def get_all_future_open_order(client):
    """
    獲取所有合約的未完成掛單，以簡潔的單行格式顯示
    
    Args:
        client: Binance client
        
    Returns:
        list[dict]: 掛單摘要資訊列表，例如：
            [
                {
                    'SOLUSDT': {
                        'long_orders': 4,
                        'short_orders': 5,
                        'reduce_only_orders': 2,
                        'total_orders': 11
                    }
                },
                ...
            ]
    """
    try:
        # 獲取所有未完成的掛單
        all_open_orders = client.futures_get_open_orders()
        summary_list = []
        
        if not all_open_orders:
            logger.info("目前沒有任何合約掛單")
            return summary_list
            
        # 按幣種分組掛單
        orders_by_symbol = {}
        for order in all_open_orders:
            symbol = order['symbol']
            if symbol not in orders_by_symbol:
                orders_by_symbol[symbol] = []
            orders_by_symbol[symbol].append(order)
            
        logger.info("\n=== 合約掛單摘要 ===")
        
        for symbol, orders in orders_by_symbol.items():
            logger.info(f"\n{symbol}:")
            
            # 統計變數初始化
            long_orders = 0
            short_orders = 0
            reduce_only_orders = 0
            
            for order in orders:
                # 格式化時間
                order_time = datetime.fromtimestamp(order['time']/1000).strftime('%H:%M:%S')
                
                # 格式化價格資訊
                price_info = order.get('price', 'N/A')
                if order.get('stopPrice', 'N/A') != 'N/A':
                    price_info = f"觸發價:{order['stopPrice']}"
                
                # 統計多空單和平倉單
                if order['side'] == 'BUY':
                    if not order['reduceOnly']:
                        long_orders += 1
                elif order['side'] == 'SELL':
                    if not order['reduceOnly']:
                        short_orders += 1
                if order['reduceOnly']:
                    reduce_only_orders += 1
                
                # 單行格式輸出
                logger.info(
                    f"[{order_time}] {order['type']} {order['side']} "
                    f"數量:{order['origQty']} {price_info} "
                    f"{'平倉' if order['reduceOnly'] else '開倉'} "
                    f"ID:{order['orderId']}"
                )
            
            # 印出該幣種的統計資訊
            logger.info(
                f"--- {symbol} 統計: "
                f"多單:{long_orders}筆, "
                f"空單:{short_orders}筆, "
                f"平倉單:{reduce_only_orders}筆, "
                f"總計:{len(orders)}筆 ---"
            )
            
            # 將統計資訊加入摘要列表
            summary_list.append({
                symbol: {
                    'long_orders': long_orders,
                    'short_orders': short_orders,
                    'reduce_only_orders': reduce_only_orders,
                    'total_orders': len(orders)
                }
            })
                
        logger.info(f"\n總計掛單數量: {len(all_open_orders)}")
        return summary_list
        
    except BinanceAPIException as e:
        logger.error(f"獲取掛單資訊時發生 Binance API 錯誤: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"獲取掛單資訊時發生未預期錯誤: {str(e)}")
        logger.error("錯誤詳情:", exc_info=True)
        return []

def calculate_order_quantity(client, symbol, strategy):
    """
    計算訂單數量
    
    Args:
        client: Binance client
        symbol: 交易對符號
        strategy: 策略實例
        
    Returns:
        str: 格式化後的訂單數量
    """
    try:
        balance = get_balance_by_symbol(symbol)
        price = get_current_price(client, symbol)
        mark_price = price['mark_price']
        
        quantity = get_grid_quantity(
            symbol=symbol,
            balance=balance,
            leverage=strategy.leverage,
            leverage_rate=strategy.leverage_rate,
            mark_price=mark_price
        )
        
        return quantity
        
    except Exception as e:
        logger.error(f"計算訂單數量時發生錯誤: {str(e)}")
        raise

def grid_v2_lab_2(client, passphrase, symbol, price_step_rate=0.005, is_callback=False, executed_side=None, executed_price=None, is_reset=False, use_lock=True):
    """
    網格交易 2.0
    
    Args:
        client: Binance client
        passphrase: 策略密碼
        symbol: 交易對符號
        price_step_rate: 價格步長比率，預設 0.005 (0.5%)
        is_callback: 是否為回調模式，預設 False
        executed_side: 已執行的交易方向，預設 None
        executed_price: 已執行的價格，預設 None
        is_reset: 是否重置網格，預設 False
        use_lock: 是否使用鎖，預設 True
    """
    # 如果使用鎖，則用 with 語句
    if use_lock:
        with _symbol_locks[symbol]:
            return _grid_v2_lab_2_impl(
                client, passphrase, symbol, price_step_rate,
                is_callback, executed_side, executed_price, is_reset
            )
    # 如果不使用鎖，直接執行實作
    else:
        return _grid_v2_lab_2_impl(
            client, passphrase, symbol, price_step_rate,
            is_callback, executed_side, executed_price, is_reset
        )

def _grid_v2_lab_2_impl(client, passphrase, symbol, price_step_rate, is_callback, executed_side, executed_price, is_reset):
    """實際的網格交易邏輯實作"""
    try:
        strategy = find_strategy_by_passphrase(passphrase)
        balance = get_balance_by_symbol(symbol)
        
        # 根據目前持倉方向決定槓桿率
        position_amount = float(balance.position_amount)
        
        # 使用 exchange_info_map 獲取交易對資訊
        symbol_info = exchange_info_map.get(symbol)
        if not symbol_info:
            raise ValueError(f"找不到交易對資訊: {symbol}")
        
        # 獲取交易對資訊
        tick_size = float(symbol_info['tickSize'])
        price_precision = int(symbol_info['pricePrecision'])
        quantity_precision = int(symbol_info['quantityPrecision'])
        min_notional = float(symbol_info['minNotional'])

        logger.info(f"""
交易對資訊:
Symbol: {symbol}
Tick Size: {tick_size}
Price Precision: {price_precision}
Quantity Precision: {quantity_precision}
Leverage: {strategy.leverage}
Position Amount: {position_amount}
------------------------""")
                
        # 如果是重置模式，先取消所有掛單
        if is_reset:
            try:
                logger.info(f"重置模式：開始取消所有掛單 - Symbol: {symbol}")
                client.futures_cancel_all_open_orders(symbol=symbol)
                logger.info("已取消所有掛單")
                current_orders = []
            except Exception as e:
                logger.error(f"取消掛單時發生錯誤: {str(e)}")
                raise
        else:
            current_orders = client.futures_get_open_orders(symbol=symbol)
        
        # 如果沒有提供成交價格，則獲取當前市價
        if executed_price is None:
            current_price = get_current_price(client, symbol)
            base_price = current_price['mark_price']
        else:
            base_price = float(executed_price)
            
        trade_group_id = generate_trade_group_id()

        # 在計算數量時根據持倉方向和交易方向決定槓桿率
        def get_order_quantity(side, price):
            if position_amount < 0:  # 目前持空單
                leverage_rate = float(strategy.short_leverage_rate if side == 'SELL' else 1.0)
            else:  # 目前持多單或無倉位
                leverage_rate = float(strategy.leverage_rate if side == 'BUY' else 1.0)
            
            return get_grid_quantity(
                symbol=symbol,
                balance=balance,
                leverage=strategy.leverage,
                leverage_rate=leverage_rate,
                mark_price=price,
                min_notional=min_notional
            )

        if not current_orders or is_reset:
            logger.info(f"""
開始建立完整網格:
交易對: {symbol}
基準價格: {base_price}
價格間隔: {price_step_rate*100}%
Tick Size: {tick_size}
重置模式: {'是' if is_reset else '否'}
------------------------""")
            new_orders = []
            
            # 生成5個買單
            for i in range(5):
                raw_price = base_price * (1 - price_step_rate) ** (i + 1)
                price = math.floor(raw_price / tick_size) * tick_size
                quantity = get_order_quantity('BUY', price)
                new_orders.append({
                    'symbol': symbol,
                    'side': 'BUY',
                    'type': 'LIMIT',
                    'timeInForce': 'GTC',
                    'price': format_decimal_symbol_price(symbol, price),
                    'quantity': quantity,
                    'newClientOrderId': f"{trade_group_id}_B{i+1}"
                })
            
            # 生成5個賣單
            for i in range(5):
                raw_price = base_price * (1 + price_step_rate) ** (i + 1)
                price = math.ceil(raw_price / tick_size) * tick_size
                quantity = get_order_quantity('SELL', price)
                new_orders.append({
                    'symbol': symbol,
                    'side': 'SELL',
                    'type': 'LIMIT',
                    'timeInForce': 'GTC',
                    'price': format_decimal_symbol_price(symbol, price),
                    'quantity': quantity,
                    'newClientOrderId': f"{trade_group_id}_S{i+1}"
                })
            
            # 批次發送訂單（每次最多5個）
            for i in range(0, len(new_orders), 5):
                batch = new_orders[i:i+5]
                try:
                    response = client.futures_place_batch_order(
                        batchOrders=json.dumps(batch)
                    )
                    logger.info(f"""
批次下單結果 ({i//5 + 1}/2):
訂單數量: {len(batch)}
交易組ID: {trade_group_id}
響應內容: {json.dumps(response, indent=2)}
symbol: {batch[0]['symbol']}
------------------------""")
                except Exception as e:
                    logger.error(f"批次下單失敗: {str(e)}")
                    return

        elif is_callback and executed_side:
            # 處理訂單成交後的邏輯
            buy_orders = [o for o in current_orders if o['side'] == 'BUY']
            sell_orders = [o for o in current_orders if o['side'] == 'SELL']
            new_orders = []
            
            if executed_side == 'BUY':
                logger.info(f"處理買單成交後的邏輯 - 成交價格: {base_price}")
                
                # 1. 取消最遠的賣單
                if sell_orders:
                    furthest_sell = max(sell_orders, key=lambda x: float(x['price']))
                    try:
                        client.futures_cancel_order(
                            symbol=symbol,
                            orderId=furthest_sell['orderId']
                        )
                        logger.info(f"已取消最遠賣單，價格: {furthest_sell['price']}")
                    except Exception as e:
                        logger.error(f"取消賣單失敗: {str(e)}")
                        return
                
                # 2. 準備新的賣單（比成交價高0.5%）
                raw_price = base_price * (1 + price_step_rate)
                new_near_sell_price = math.ceil(raw_price / tick_size) * tick_size
                quantity = get_order_quantity('SELL', new_near_sell_price)
                new_orders.append({
                    'symbol': symbol,
                    'side': 'SELL',
                    'type': 'LIMIT',
                    'timeInForce': 'GTC',
                    'price': format_decimal_symbol_price(symbol, new_near_sell_price),
                    'quantity': quantity,
                    'newClientOrderId': f"{trade_group_id}_S1"
                })
                
                # 3. 準備新的買單（比最低買單再低0.5%）
                if buy_orders:
                    lowest_buy = min(buy_orders, key=lambda x: float(x['price']))
                    raw_price = float(lowest_buy['price']) * (1 - price_step_rate)
                else:
                    raw_price = base_price * (1 - price_step_rate * 5)
                    
                new_far_buy_price = math.floor(raw_price / tick_size) * tick_size
                quantity = get_order_quantity('BUY', new_far_buy_price)
                new_orders.append({
                    'symbol': symbol,
                    'side': 'BUY',
                    'type': 'LIMIT',
                    'timeInForce': 'GTC',
                    'price': format_decimal_symbol_price(symbol, new_far_buy_price),
                    'quantity': quantity,
                    'newClientOrderId': f"{trade_group_id}_B5"
                })
                
            elif executed_side == 'SELL':
                logger.info("處理賣單成交後的邏輯")
                
                # 1. 取消最遠的買單
                if buy_orders:
                    furthest_buy = min(buy_orders, key=lambda x: float(x['price']))
                    try:
                        client.futures_cancel_order(
                            symbol=symbol,
                            orderId=furthest_buy['orderId']
                        )
                        logger.info(f"已取消最遠買單，價格: {furthest_buy['price']}")
                    except Exception as e:
                        logger.error(f"取消買單失敗: {str(e)}")
                        return
                
                # 2. 準備新的買單（比成交價低0.5%）
                raw_price = base_price * (1 - price_step_rate)
                new_near_buy_price = math.floor(raw_price / tick_size) * tick_size
                quantity = get_order_quantity('BUY', new_near_buy_price)
                new_orders.append({
                    'symbol': symbol,
                    'side': 'BUY',
                    'type': 'LIMIT',
                    'timeInForce': 'GTC',
                    'price': format_decimal_symbol_price(symbol, new_near_buy_price),
                    'quantity': quantity,
                    'newClientOrderId': f"{trade_group_id}_B1"
                })
                
                # 3. 準備新的賣單（比最高賣單再高0.5%）
                if sell_orders:
                    highest_sell = max(sell_orders, key=lambda x: float(x['price']))
                    raw_price = float(highest_sell['price']) * (1 + price_step_rate)
                else:
                    raw_price = base_price * (1 + price_step_rate * 5)
                    
                new_far_sell_price = math.ceil(raw_price / tick_size) * tick_size
                quantity = get_order_quantity('SELL', new_far_sell_price)
                new_orders.append({
                    'symbol': symbol,
                    'side': 'SELL',
                    'type': 'LIMIT',
                    'timeInForce': 'GTC',
                    'price': format_decimal_symbol_price(symbol, new_far_sell_price),
                    'quantity': quantity,
                    'newClientOrderId': f"{trade_group_id}_S5"
                })
            
            # 批次下新單
            if new_orders:
                try:
                    response = client.futures_place_batch_order(
                        batchOrders=json.dumps(new_orders)
                    )
                    logger.info(f"""
批次下單結果:
訂單數量: {len(new_orders)}
交易組ID: {trade_group_id}
響應內容: {json.dumps(response, indent=2)}
------------------------""")
                except Exception as e:
                    logger.error(f"批次下單失敗: {str(e)}")
                    return

    except Exception as e:
        logger.error(f"網格交易實驗 2.0 執行失敗 - Symbol: {symbol}, Error: {str(e)}")
        logger.error("錯誤詳情:", exc_info=True)
        raise

def check_and_reset_grid_orders(client, symbol):
    """
    檢查並重置網格掛單
    
    Args:
        client: Binance client
        symbol: 交易對符號
        
    Returns:
        bool: 是否成功執行
    """
    try:
        # 嘗試獲取鎖，如果無法立即獲取則返回
        if not _symbol_locks[symbol].acquire(blocking=False):
            logger.info(f"{symbol} 正在執行其他操作，跳過檢查")
            return False
            
        try:
            current_orders = client.futures_get_open_orders(symbol=symbol)
            
            if len(current_orders) != 10:
                logger.info(f"檢測到 {symbol} 掛單數量錯誤 ({len(current_orders)}/10)，執行重置")
                
                # 查找對應的策略
                strategy = get_strategy_by_symbol(symbol)
                logger.info(strategy)
                if strategy:
                    grid_v2_lab_2(
                        client=client,
                        passphrase=strategy.passphrase,
                        symbol=symbol,
                        is_reset=True,
                        use_lock=False
                    )
                    return True
                else:
                    logger.error(f"找不到 {symbol} 對應的策略")
                    return False
                    
            return True
            
        finally:
            _symbol_locks[symbol].release()
            
    except Exception as e:
        logger.error(f"檢查 {symbol} 網格掛單時發生錯誤: {str(e)}")
        return False

def get_active_grid_v2_symbols() -> list:
    """
    獲取所有狀態為 ACTIVE 且策略類型為 grid_v2 的交易對符號
    
    Returns:
        list: 符合條件的交易對符號列表，例如 ['BTCUSDT', 'ETHUSDT']
    """
    try:
        symbols = Strategy.objects.filter(
            strategy_type='grid_v2',
            status='ACTIVE'
        ).values_list('symbol', flat=True)
        
        return list(symbols)
        
    except Exception as e:
        logger.error(f"獲取ACTIVE grid_v2 交易對時發生錯誤: {str(e)}")
        return []