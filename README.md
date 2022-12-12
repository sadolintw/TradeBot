# Intro

create Binance ETHUSDTPERP order by Trading View Signal

# Signal
```json
{
  "entry": "{{strategy.order.price}}",
  "order": "{{strategy.order.action}}",
  "passphase": "<your passphase>",
  "position_size": "{{strategy.position_size}}",
  "strategy": {
    "long": {
      "stopLoss": "1.5",
      "takeProfit": "35",
      "times": "5"
    },
    "name": "<your trading view strategy>",
    "short": {
      "stopLoss": "1.5",
      "takeProfit": "10",
      "times": "5"
    }
  },
  "ticker": "ETHUSDT",
  "unit": "{{strategy.order.contracts}}"
}
```