from decimal import Decimal

from .models import AccountInfo, Strategy, AccountBalance, Trade

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

def create_trades_from_binance(binance_trades, strategy_id):
    """
    從Binance交易數據創建Trade實例。

    :param binance_trades: 來自Binance的交易數據列表。
    :param strategy_id: 使用的策略ID。
    """
    # 獲取策略實例
    strategy_instance = Strategy.objects.get(strategy_id=strategy_id)

    # 創建Trade實例
    for trade_data in binance_trades:
        trade = Trade(
            thirdparty_id=trade_data['orderId'],
            strategy=strategy_instance,
            symbol=trade_data['symbol'],
            trade_type=trade_data['side'],
            quantity=Decimal(trade_data['origQty']),
            price=Decimal(trade_data['price']),
            # 其他需要的字段...
        )
        trade.save()

        print(f"Trade with order ID {trade.thirdparty_id} created successfully.")

def get_main_account_info():
    try:
        # 尝试获取名称为 "main account" 的账户信息
        account = AccountInfo.objects.get(account_name="main account")
        return account
    except AccountInfo.DoesNotExist:
        # 如果没有找到该账户，返回 None 或适当的响应
        return None
