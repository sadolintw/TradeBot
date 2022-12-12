from telegram.ext import *
import telegram
import os
from flask import Flask, request
from flask import redirect

app = Flask(__name__)

@app.route("/")
def hello():
    return "Flask on port 5000."

@app.route("/goto/<path:url>", methods=['GET'])
def _goto(url):
    return redirect(url)

@app.route("/telegram", methods=['GET', 'POST'])
def _telegram():
    if request.method == 'POST':
        telegram_bot_access_token = os.environ['TELEGRAM_BOT_ACCESS_TOKEN']
        telegram_bot_chat_id = os.environ['TELEGRAM_BOT_CHAT_ID']
        bot = telegram.Bot(token=telegram_bot_access_token)
        bot.send_message(chat_id=telegram_bot_chat_id, text=str(request.json))
    return 'ok'

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)