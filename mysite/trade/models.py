from django.db import models
import time
from django.utils import timezone

class AccountInfo(models.Model):
    account_id = models.AutoField(primary_key=True)
    account_name = models.CharField(max_length=255)
    api_key = models.CharField(max_length=255, blank=True, null=True)
    api_secret = models.CharField(max_length=255, blank=True, null=True)
    other_info = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'account_info'

    def __str__(self):
        return (f"Account ID: {self.account_id}, Name: {self.account_name}, API Key: {self.api_key}, "
                f"API Secret: {'*' * len(self.api_secret) if self.api_secret else 'None'}, "
                f"Other Info: {self.other_info}")

class Strategy(models.Model):
    strategy_id = models.AutoField(primary_key=True)
    account = models.ForeignKey(AccountInfo, on_delete=models.CASCADE, related_name='strategies')
    strategy_name = models.CharField(max_length=255)
    strategy_type = models.CharField(max_length=255)
    initial_capital = models.DecimalField(max_digits=10, decimal_places=2)
    risk_parameters = models.TextField(blank=True, null=True)
    entry_criteria = models.TextField(blank=True, null=True)
    exit_criteria = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=50, blank=True, null=True)
    passphrase = models.CharField(max_length=36, blank=True, null=True)
    trade_group_id = models.CharField(max_length=36, blank=True, null=True)
    leverage = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    symbol = models.TextField(blank=True, null=True)
    leverage_rate = models.DecimalField(max_digits=10, decimal_places=2)
    reduce_rate = models.DecimalField(max_digits=10, decimal_places=2)
    hold_rate = models.DecimalField(max_digits=10, decimal_places=2)
    recover_rate = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = 'strategies'

    def __str__(self):
        return (f"Strategy ID: {self.strategy_id}, Name: {self.strategy_name}, Type: {self.strategy_type}, "
                f"Initial Capital: {self.initial_capital}, Risk Parameters: {self.risk_parameters}, "
                f"Entry Criteria: {self.entry_criteria}, Exit Criteria: {self.exit_criteria}, "
                f"Status: {self.status}, Passphrase: {self.passphrase}, "
                f"Trade Group ID: {self.trade_group_id}, "
                f"Leverage: {self.leverage}, Symbol: {self.symbol}, "
                f"Created At: {self.created_at}, Updated At: {self.updated_at}")

class Trade(models.Model):
    trade_id = models.AutoField(primary_key=True)
    thirdparty_id = models.BigIntegerField()
    strategy = models.ForeignKey(Strategy, on_delete=models.CASCADE)
    symbol = models.CharField(max_length=20)
    trade_side = models.CharField(max_length=4)
    trade_type = models.CharField(max_length=20)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    price = models.DecimalField(max_digits=20, decimal_places=8)
    profit_loss = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cumulative_profit_loss = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    trade_group_id = models.CharField(max_length=36, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'trades'

    @property
    def created_at_timestamp(self):
        return int(time.mktime(self.created_at.timetuple()))

    def __str__(self):
        return (f"Trade ID: {self.trade_id}, Strategy ID: {self.strategy_id}, Symbol: {self.symbol}, "
                f"Trade Side: {self.trade_side}, Trade Type: {self.trade_type}, Quantity: {self.quantity}, "
                f"Price: {self.price}, Profit/Loss: {self.profit_loss}, "
                f"Cumulative Profit/Loss: {self.cumulative_profit_loss}, "
                f"Trade Group ID: {self.trade_group_id}, "
                f"Created At: {self.created_at}, Updated At: {self.updated_at}")

class AccountBalance(models.Model):
    balance_id = models.AutoField(primary_key=True)
    strategy = models.ForeignKey(Strategy, on_delete=models.CASCADE)
    balance = models.DecimalField(max_digits=20, decimal_places=8)
    equity = models.DecimalField(max_digits=10, decimal_places=2)
    available_margin = models.DecimalField(max_digits=10, decimal_places=2)
    used_margin = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    unrealized_pnl = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    position_value = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    position_amount = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    profit_loss = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'account_balance'

    def __str__(self):
        return (f"Balance ID: {self.balance_id}, Strategy ID: {self.strategy_id}, Balance: {self.balance}, "
                f"Equity: {self.equity}, Available Margin: {self.available_margin}, Used Margin: {self.used_margin}, "
                f"Profit/Loss: {self.profit_loss}, Timestamp: {self.timestamp}, "
                f"Created At: {self.created_at}, Updated At: {self.updated_at}")


class GridPosition(models.Model):
    position_id = models.AutoField(primary_key=True)
    strategy = models.ForeignKey(Strategy, on_delete=models.CASCADE, related_name='grid_positions')
    grid_index = models.IntegerField()
    quantity = models.DecimalField(max_digits=10, decimal_places=4)
    entry_price = models.DecimalField(max_digits=20, decimal_places=8)
    exit_price = models.DecimalField(max_digits=20, decimal_places=8, blank=True, null=True)
    stop_price = models.DecimalField(max_digits=20, decimal_places=8, blank=True, null=True)
    is_open = models.BooleanField(default=True)
    trade_group_id = models.CharField(max_length=36, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'grid_positions'

    def __str__(self):
        return (f"Grid Position ID: {self.position_id}, Strategy ID: {self.strategy.strategy_id}, "
                f"Level Index: {self.grid_index}, Quantity: {self.quantity}, "
                f"Entry Price: {self.entry_price}, Exit Price: {self.exit_price}, "
                f"Stop Price: {self.stop_price}, Is Open: {self.is_open}, "
                f"Trade Group ID: {self.trade_group_id}, "
                f"Created At: {self.created_at}, Updated At: {self.updated_at}")

class OrderExecution(models.Model):
    """訂單執行記錄"""
    
    class ExecutionType(models.TextChoices):
        PARTIAL = 'PARTIAL', '部分成交'
        FULL = 'FULL', '完全成交'
    
    # 關聯欄位
    strategy = models.ForeignKey('Strategy', on_delete=models.PROTECT, related_name='order_executions')
    
    # 執行資訊
    execution_id = models.BigAutoField(primary_key=True)
    binance_execution_id = models.CharField(max_length=50, unique=True)  # 幣安成交ID
    execution_type = models.CharField(max_length=10, choices=ExecutionType.choices)
    symbol = models.CharField(max_length=20)
    order_id = models.CharField(max_length=50)  # Binance訂單ID
    client_order_id = models.CharField(max_length=50)  # 客戶端訂單ID
    side = models.CharField(max_length=10)  # BUY/SELL
    price = models.DecimalField(max_digits=20, decimal_places=8)
    quantity = models.DecimalField(max_digits=20, decimal_places=8)  # 本次成交數量
    commission = models.DecimalField(max_digits=20, decimal_places=8)  # 手續費
    commission_asset = models.CharField(max_length=10)  # 手續費資產
    realized_pnl = models.DecimalField(max_digits=20, decimal_places=8)  # 已實現盈虧
    
    # 時間戳記與備註
    execution_time = models.DateTimeField()  # 成交時間
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'order_executions'
        ordering = ['-execution_time']
        indexes = [
            models.Index(fields=['strategy', 'symbol', 'execution_time']),
            models.Index(fields=['order_id']),
            models.Index(fields=['client_order_id'])
        ]

    def __str__(self):
        return f"{self.symbol} {self.execution_type} - {self.quantity}@{self.price}"

class AccountBalanceHistory(models.Model):
    """帳戶餘額歷史記錄"""
    history_id = models.BigAutoField(primary_key=True)
    strategy = models.ForeignKey('Strategy', on_delete=models.PROTECT)
    balance = models.DecimalField(max_digits=20, decimal_places=8)
    equity = models.DecimalField(max_digits=20, decimal_places=8)
    available_margin = models.DecimalField(max_digits=20, decimal_places=8)
    used_margin = models.DecimalField(max_digits=20, decimal_places=8)
    unrealized_pnl = models.DecimalField(max_digits=20, decimal_places=8)
    position_value = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    position_amount = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    snapshot_time = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'account_balance_history'
        ordering = ['-snapshot_time']
        indexes = [
            models.Index(fields=['strategy', 'snapshot_time']),
        ]
