

# from binance import Client
#
# import os
#
# api_key = os.environ['BINANCE_APIKEY']
# api_secret = os.environ['BINANCE_SECRETKEY']
#
# client = Client(api_key, api_secret)

# print(client.futures_account_balance())
# print(client.get_account())
# print(client.get_margin_account())
# print(client.futures_account())
# print(client.futures_create_order(symbol='ETHUSDT', side='BUY', type='LIMIT'))
# print(client.create_test_order(
#     symbol='ETHUSDT',
#     side='BUY',
#     type='LIMIT',
#     quantity=1,
#     timeInForce='GTC',
#     price=1600
# ))
# print(client.create_test_order(
#     symbol='ETHUSDT',
#     side='BUY',
#     type='MARKET',
#     quantity=0.01,
#     # timeInForce='GTC',
#     # price=1600
# ))
# print(
#     client.futures_order_book(symbol='ETHUSDT')
# )
# print(client.futures_get_open_orders(symbol='ETHUSDT'))
# print(client.futures_account_balance())

# positions = client.futures_account()['positions']
# target = None
# for position in positions:
#     if position['symbol'] == 'ETHUSDT':
#         target = position
#         break
# print(float(position['initialMargin']) > 0)

# print(json.dumps(client.futures_account()['positions']))

from telegram.ext import *
import telegram
import os
telegram_bot_access_token = os.environ['TELEGRAM_BOT_ACCESS_TOKEN']
telegram_bot_chat_id = os.environ['TELEGRAM_BOT_CHAT_ID']
bot = telegram.Bot(token=telegram_bot_access_token)
bot.send_message(chat_id=telegram_bot_chat_id, text="""hi""")