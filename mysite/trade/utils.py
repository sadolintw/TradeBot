import os
from decimal import Decimal
from django.db import transaction

from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned

from .models import AccountInfo, Strategy, AccountBalance, Trade, GridPosition
import logging
from logging.handlers import TimedRotatingFileHandler
import datetime


def get_monthly_rotating_logger(logger_name, log_dir='logs'):
    logger = logging.getLogger(logger_name)

    # 检查是否已经添加了处理器
    if not logger.handlers:
        print("logger not found")
        logger.setLevel(logging.INFO)

        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        current_month = datetime.datetime.now().strftime("%Y-%m")
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

    for trade in response:
        if trade['orderId'] in order_list:
            total_realized_pnl += float(trade['realizedPnl'])

    return total_realized_pnl


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
    查询与特定策略相关的所有网格位置的数量（quantity）值的总和。

    参数:
    - strategy: 策略实例

    返回:
    - 累计的quantity值。
    """
    try:
        # 获取所有与策略相关的GridPosition记录
        grid_positions = GridPosition.objects.filter(strategy=strategy)

        # 累计所有quantity值
        total_quantity = sum(position.quantity for position in grid_positions)

        return total_quantity
    except Exception as e:
        # 如果查询过程中发生错误
        print(f"Error occurred: {e}")
        return 0  # 或返回其他合适的默认值


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
