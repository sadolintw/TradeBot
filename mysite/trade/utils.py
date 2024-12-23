import os
import time  # 新增 time 模組
import uuid
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from binance.exceptions import BinanceAPIException  # 新增 BinanceAPIException
from .models import AccountInfo, Strategy, AccountBalance, Trade, GridPosition
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import threading
from binance import ThreadedWebsocketManager


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

def format_decimal(value, digit):
    format_string = "{:." + str(digit) + "f}"
    return format_string.format(value)


def strategy_list():
    strategies = Strategy.objects.all()
    print('list strategies')
    for strategy in strategies:
        print(strategy)


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


def query_trade(trade_group_id, trade_type):
    if not trade_group_id:
        return None

    try:
        trade = Trade.objects.get(trade_group_id=trade_group_id, trade_type=trade_type)
        return trade
    except ObjectDoesNotExist:
        # 没有找到符合条件的Trade对象
        return None
    except MultipleObjectsReturned:
        # 找到多于一个符合条件的Trade对象，这取决于你的数据模型是否允许这种情况
        # 根据实际需求处理这种情况，比如记录日志、抛出异常或其他操作
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
        self.callback = callback if callback else lambda x: None  # 預設空函數
        self.logger = custom_logger if custom_logger else logger  # 使用 utils 中定義的 logger

    def handle_socket_message(self, msg):
        """處理websocket訊息的回調函數"""
        try:
            self.last_receive_time = time.time()
            if msg['e'] == 'ORDER_TRADE_UPDATE':
                order = msg['o']
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
訂單時間: {order['T']}
成交時間: {order['t']}
""")
                # 執行自定義回調函數
                self.callback(msg)
                
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
                if self.twm:
                    self.twm.stop()
                    self.twm = None
                self.logger.info("Websocket連接已停止")
            except Exception as e:
                self.logger.error(f"停止Websocket時發生錯誤: {str(e)}")

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

def generate_grid_levels(lower_bound: float, upper_bound: float, grids: int, digit: int = 3) -> list[float]:
    """
    生成網格交易的價格點位陣列
    
    Args:
        lower_bound: 最低價格
        upper_bound: 最高價格
        grids: 網格數量
        digit: 小數點後位數，預設為3
        
    Returns:
        list[float]: 由低到高排序的價格點位陣列
    """
    # 包含上下界
    _grids = grids + 1
    # 確保輸入參數有效
    if lower_bound >= upper_bound:
        raise ValueError("下界必須小於上界")
    if grids < 2:
        raise ValueError("網格數量必須大於等於2")
    if digit < 0:
        raise ValueError("小數點位數不能為負數")
        
    # 計算每個網格的價格間距
    grid_size = (upper_bound - lower_bound) / (_grids - 1)
    
    # 生成所有價格點位並四捨五入到指定小數位
    grid_levels = [round(lower_bound + (grid_size * i), digit) for i in range(_grids)]
    
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

def grid_v2_create_batch_payload(strategy, current_grid_index, logger=logger):
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
        logger.info(f"Strategy info - ID: {strategy.strategy_id}, Symbol: {strategy.symbol}")
        
        # 取得所有網格倉位並檢查
        positions = get_grid_positions_by_strategy(strategy=strategy)
        logger.info(f"Raw positions query result: {positions}")
        
        if not positions:
            logger.error("No positions found for strategy")
            return []
            
        all_orders = []
        current_batch = []
        trade_group_id = generate_trade_group_id()
        
        logger.info(f"Number of positions found: {len(positions)}")
        
        for position in positions:
            logger.info(f"Processing position: {position.__dict__}")
            
            if position.grid_index == current_grid_index:
                logger.info(f"Skipping current grid index {current_grid_index}")
                continue
            
            # 基本訂單資訊
            order = {
                'symbol': strategy.symbol,
                'type': 'LIMIT',
                'timeInForce': 'GTC',
                'quantity': str(position.quantity),
                'newClientOrderId': f"{trade_group_id}_{position.grid_index}"
            }
            
            # 根據網格索引添加買賣方向和價格
            if position.grid_index < current_grid_index:
                logger.info(f"Creating BUY order for grid {position.grid_index}")
                order.update({
                    'price': str(position.entry_price),
                    'side': 'BUY'
                })
            else:
                logger.info(f"Creating SELL order for grid {position.grid_index}")
                order.update({
                    'price': str(position.exit_price),
                    'side': 'SELL'
                })
            
            current_batch.append(order)
            logger.info(f"Added order to current batch: {order}")
            
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