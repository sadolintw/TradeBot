import json
from binance import Client
from datetime import datetime
import os

api_key = os.environ['BINANCE_APIKEY']
api_secret = os.environ['BINANCE_SECRETKEY']

client = Client(api_key, api_secret)

# change leverage
# client.futures_change_leverage(symbol="ETHUSDT", leverage=5)

# ============================================

# cancel order
# client.futures_cancel_order(symbol="ETHUSDT")

# client.futures_cancel_all_open_orders(symbol="ETHUSDT")

# lastPrice=client.futures_ticker(symbol='ETHUSDT')['lastPrice']
# print(lastPrice)
#
# client.futures_create_order(
#     symbol="ETHUSDT",
#     closePosition="True",
#     type="STOP_MARKET",
#     side="SELL",
#     stopPrice=lastPrice
# )

# client.futures_position_information(symbol="ETHUSDT")

# client.futures_get_order(symbol="ETHUSDT")

# prev_update_time = 0
#
# def check_position(symbol):
#     positions = client.futures_account()['positions']
#     target = None
#     for position in positions:
#         if position['symbol'] == symbol:
#             global quantity
#             global opposite_side
#             global prev_update_time
#             target = position
#             print('position', position)
#             print('has initial margin', float(position['initialMargin']) > 0)
#             print('leverage', position['leverage'])
#             quantity = position['positionAmt']
#             opposite_side = 'SELL' if float(quantity) > 0 else 'BUY'
#             prev_update_time = int(position['updateTime'])
#             print('quantity', quantity)
#             print('opposite_side', opposite_side)
#             return True
#     return False
#
# def close_position(symbol, side, quantity):
#     precision = 3
#     print('close_position', symbol, side, round(float(quantity),precision))
#     response = client.futures_create_order(
#         symbol=symbol,
#         type="MARKET",
#         side=side,
#         quantity=round(abs(float(quantity)),precision),
#         reduceOnly='True'
#     )
#     print(response)
#
# check_position(symbol="ETHUSDT")
# # close_position(symbol="ETHUSDT", side=opposite_side, quantity=quantity)
#
# now = datetime.now()
#
# timestamp = datetime.timestamp(now)*1000
# print("timestamp =", timestamp, "update time =", prev_update_time, (timestamp - prev_update_time)/1000/60)

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

# test telegram
from telegram.ext import *
# import telegram
# import os
# telegram_bot_access_token = os.environ['TELEGRAM_BOT_ACCESS_TOKEN']
# telegram_bot_chat_id = os.environ['TELEGRAM_BOT_CHAT_ID']
# bot = telegram.Bot(token=telegram_bot_access_token)
# bot.send_message(chat_id=telegram_bot_chat_id, text="""hi""")

# signal = json.loads('{"passphase":"cd51dccb-1413-46e3-aa06-ba40a5668d9f","strategy":{"name":"My Super Trend VTest '
#                     '1H","long":{"times":"5","stopLoss":"1.5","takeProfit":"20"},"short":{"times":"5",'
#                     '"stopLoss":"1.5","takeProfit":"10"}},"order":"sell","unit":"3.637","ticker":"ETHUSDT",'
#                     '"position_size":"0","entry":"1477.28"}')
#
# signal_position_size = round(float(signal['position_size']), 2)
# print(signal_position_size)
# print(signal_position_size==0)
# print(round(float(signal_position_size), 3))
# print(datetime.timestamp(datetime.now()) * 1000)

print(True and True)