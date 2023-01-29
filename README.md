<h1 align="center">TradeBot (Beta)</h1>
---

# Intro

create Binance ETHUSDTPERP order by Trading View Alert

# Prerequisite

## Env var

- **BINANCE_APIKEY**

- **BINANCE_SECRETKEY**

- **TRADINGVIEW_PASSPHASE**

- **TELEGRAM_BOT_ACCESS_TOKEN**

- **TELEGRAM_BOT_CHAT_ID**

# Alert Format
```json
{
  "entry": "{{strategy.order.price}}",
  "message": "{{strategy.order.alert_message}}",
  "order": "{{strategy.order.action}}",
  "passphrase": "<your passphrase>",
  "position_size": "{{strategy.position_size}}",
  "strategy": {
    "long": {
      "stopLoss": "1.5",
      "takeProfit": "20",
      "times": "5"
    },
    "name": "<your trading view strategy name>",
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

message format:

```
{"type": "long_entry"|"long_exit"|"short_entry"|"short_exit", "lev": tostring(int_num), "sl": tostring(float_num)}
```
