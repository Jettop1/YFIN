import os
import requests
import yfinance as yf

ASSETS = ["BTC-USD", "ETH-USD"]
INTERVAL = "5m"
PERIOD = "1d"


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{os.environ['TG_TOKEN']}/sendMessage",
        data={"chat_id": os.environ["TG_CHAT_ID"], "text": msg},
        timeout=30,
    )


for a in ASSETS:
    close = yf.download(a, period=PERIOD, interval=INTERVAL,
                        progress=False)["Close"].squeeze()
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    # ultima candela chiusa = -2 (la -1 è ancora in formazione)
    prev = ema9.iloc[-3] - ema21.iloc[-3]
    last = ema9.iloc[-2] - ema21.iloc[-2]
    if prev <= 0 < last:
        send(f"🟢 {a}: EMA 9 incrocia sopra EMA 21 ({INTERVAL})")
    elif prev >= 0 > last:
        send(f"🔴 {a}: EMA 9 incrocia sotto EMA 21 ({INTERVAL})")
