import os
import requests
import yfinance as yf

ASSETS = ["EUR/USD"]
INTERVAL = "5m"
PERIOD = "1d"


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{os.environ['TG_TOKEN']}/sendMessage",
        data={"chat_id": os.environ["TG_CHAT_ID"], "text": msg},
        timeout=30,
    )


TEST = os.environ.get("TEST") == "true"
report = []

for a in ASSETS:
    close = yf.download(a, period=PERIOD, interval=INTERVAL,
                        progress=False)["Close"].squeeze()
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    # ultima candela chiusa = -2 (la -1 è ancora in formazione)
    prev = ema9.iloc[-3] - ema21.iloc[-3]
    last = ema9.iloc[-2] - ema21.iloc[-2]
    line = (f"{a}: EMA9={ema9.iloc[-2]:.5f} EMA21={ema21.iloc[-2]:.5f} "
            f"({'sopra' if last > 0 else 'sotto'})")
    print(line)
    report.append(line)
    if prev <= 0 < last:
        send(f"🟢 {a}: EMA 9 incrocia sopra EMA 21 ({INTERVAL})")
    elif prev >= 0 > last:
        send(f"🔴 {a}: EMA 9 incrocia sotto EMA 21 ({INTERVAL})")

if TEST:
    send("✅ Test EMA alert funzionante\n" + "\n".join(report))
