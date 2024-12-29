import os
import json
import time  # 新增 time 模組
import uuid
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from binance.exceptions import BinanceAPIException  # 新增 BinanceAPIException
from .models import AccountInfo, Strategy, AccountBalance, Trade, GridPosition, OrderExecution
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import threading
from binance import ThreadedWebsocketManager
from django.db.utils import DatabaseError



def get_monthly_rotating_logger(logger_name, log_dir='logs'):
    logger = logging.getLogger(logger_name)

    # 检查是否已经添加了处理器
    if not logger.handlers:
        print("logger not found")
        logger.setLevel(logging.INFO)

        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        current_month = datetime.now().strftime("%Y-%m")
        log_filename = f'{log_dir}/my_trade_system_{current_month}.log'

        handler = TimedRotatingFileHandler(log_filename, when='MIDNIGHT', interval=1, backupCount=0)
        handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] [%(funcName)s] [%(lineno)d] - %(message)s'))

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] [%(funcName)s] [%(lineno)d] - %(message)s'))

        logger.addHandler(handler)
        logger.addHandler(console_handler)
    else:
        print('logger found ')

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

def grid_v2_create_batch_payload(strategy, current_grid_index, mark_price, logger=logger):
    """
    生成網格交易的批次掛單資料，每批最多5個訂單
    https://developers.binance.com/docs/zh-CN/derivatives/usds-margined-futures/trade/rest-api/Place-Multiple-Orders

    Args:
        strategy: 策略實例
        current_grid_index: 當前網格索引
        
    Returns:
        list: 批次掛單資料陣列的列表，每個陣列最多包含5個訂單
    """
    try:
        # 檢查策略資訊
        symbol = strategy.symbol
        leverage = strategy.leverage
        leverage_rate = strategy.leverage_rate
        balance = get_balance_by_symbol(symbol)
        
        # 將所有數值轉換為 float 進行計算
        quantity_by_balance = float(balance.balance) * 0.015 * leverage * float(leverage_rate) / mark_price  # 根據餘額計算
        quantity_by_min_notional = int(5 / mark_price + 1)  # 最小名義價值5U
        quantity = max(quantity_by_balance, quantity_by_min_notional)
        # logger.info(f"Strategy info - ID: {strategy.strategy_id}, Symbol: {symbol}")
        
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
            
            # 基本訂單資訊
            order = {
                'symbol': strategy.symbol,
                'type': 'LIMIT',
                'timeInForce': 'GTC',
                'quantity': format_decimal_symbol_quantity(symbol, quantity),
                'newClientOrderId': f"{trade_group_id}_{position.grid_index}",
            }
            
            # 單向持倉模式下:
            # - 低於當前網格掛買單，成交後形成多倉
            # - 高於當前網格掛賣單，成交後形成空倉
            if position.grid_index < current_grid_index:
                # logger.info(f"Creating BUY order at grid {position.grid_index}")
                order.update({
                    'price': format_decimal_symbol_price(symbol, position.entry_price),
                    'side': 'BUY'
                })
            else:
                # logger.info(f"Creating SELL order at grid {position.grid_index}")
                order.update({
                    'price': format_decimal_symbol_price(symbol, position.entry_price),
                    'side': 'SELL'
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
            hold_rate = margin/current_balance
            position_amount = float(pos['position_amount'])
            
            # 檢查是否超過策略設定的 hold_rate
            if hold_rate > strategy.hold_rate:
                # 計算需要平倉的數量（一半持倉）
                close_quantity = abs(position_amount) / 2
                
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
持倉數量: {pos['position_amount']} ({"多倉" if pos['position_amount'] > 0 else "空倉"})
持倉均價: {pos['entry_price']}
持倉價值: {pos['position_value']:.8f} USDT
持倉比例 {hold_rate:.8} {"(超過限制)" if hold_rate > strategy.hold_rate else ""}
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

def grid_v2_lab(client, passphrase, symbol):
    client.futures_cancel_all_open_orders(symbol=symbol)
    price = get_current_price(client, symbol)
    mark_price = price['mark_price']
    upper_bound = mark_price * (1 + 0.025)
    lower_bound = mark_price * (1 - 0.025)
    levels = generate_grid_levels(lower_bound, upper_bound, 10, symbol)
    logger.info(levels)
    strategy = find_strategy_by_passphrase(passphrase)
    leverage = strategy.leverage
    client.futures_change_leverage(symbol=symbol, leverage=leverage)
    update_grid_positions_price(strategy, levels)
    batch_payloads = grid_v2_create_batch_payload(strategy=strategy, current_grid_index=5, mark_price=mark_price)
    # print(batch_payloads)
    # 分批發送訂單
    for batch in batch_payloads:
        logger.info(f"Sending batch order: {json.dumps(batch)}")
        response = client.futures_place_batch_order(batchOrders=json.dumps(batch))
        print(response)

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

def reduce_leverage(strategy: 'Strategy') -> None:
    """
    降低策略的槓桿率
    
    Args:
        strategy: Strategy 模型實例
    """
    try:
        # 計算新的槓桿率
        new_leverage_rate = float(strategy.leverage_rate) * float(strategy.reduce_rate)
        
        # 更新策略的槓桿率
        strategy.leverage_rate = Decimal(str(new_leverage_rate))
        strategy.save()
        
        logger.info(f"""
槓桿率調整:
策略ID: {strategy.strategy_id}
交易對: {strategy.symbol}
調整後槓桿率: {strategy.leverage_rate}
------------------------""")
        
    except Exception as e:
        logger.error(f"調整槓桿率時發生錯誤: {str(e)}")
        logger.error("錯誤詳情:", exc_info=True)


def recover_leverage(strategy: 'Strategy') -> None:
    """
    恢復策略的槓桿率,但不超過1
    
    Args:
        strategy: Strategy 模型實例
    """
    try:
        # 計算新的槓桿率
        new_leverage_rate = float(strategy.leverage_rate) * (1.0 + float(strategy.recover_rate))
        
        # 確保不超過上限1
        new_leverage_rate = min(new_leverage_rate, 1.0)
        
        # 更新策略的槓桿率
        strategy.leverage_rate = Decimal(str(new_leverage_rate))
        strategy.save()
        
        logger.info(f"""
槓桿率恢復:
策略ID: {strategy.strategy_id}
交易對: {strategy.symbol}
調整後槓桿率: {strategy.leverage_rate}
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
        # 生成交易群組ID
        trade_group_id = generate_trade_group_id()
        close_order['newClientOrderId'] = trade_group_id
        
        # 執行平倉訂單
        logger.warning(f"""
開始執行風險控制平倉:
交易對: {symbol}
平倉方向: {close_order['side']}
平倉數量: {close_order['quantity']}
訂單類型: {close_order['type']}
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
            
            logger.info(f"風險控制平倉訂單執行成功: {response}")
            
            # 降低槓桿率
            reduce_leverage(strategy)
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