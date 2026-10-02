import os
import pandas as pd
import requests
import yfinance as yf

ASSETS = ["EURUSD=X"]
HOURS = 4             # timeframe delle candele, in ore
WINDOW_MIN = 60       # notifica solo se la candela si è chiusa da meno di X minuti
TZ_FOREX = "America/New_York"  # le candele forex da 4h partono da 17:00 di New York


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{os.environ['TG_TOKEN']}/sendMessage",
        data={"chat_id": os.environ["TG_CHAT_ID"], "text": msg},
        timeout=30,
    )


def closed_candles(close_1h, now):
    """Raggruppa le chiusure orarie in candele da HOURS ore e tiene solo
    quelle già chiuse (esclude la candela ancora in formazione)."""
    close_1h = close_1h.dropna()
    close_1h.index = close_1h.index.tz_convert(TZ_FOREX)
    candles = close_1h.resample(f"{HOURS}h", offset="1h").last().dropna()
    end = candles.index + pd.Timedelta(hours=HOURS)
    return candles[end <= now], end[end <= now]


TEST = os.environ.get("TEST") == "true"
report = []
now = pd.Timestamp.now(tz="UTC")

if __name__ == "__main__":
    for a in ASSETS:
        data = yf.download(a, period="1mo", interval="1h", progress=False)
        close, ends = closed_candles(data["Close"].squeeze(), now)
        ema9 = close.ewm(span=9, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        prev = ema9.iloc[-2] - ema21.iloc[-2]
        last = ema9.iloc[-1] - ema21.iloc[-1]
        age_min = (now - ends[-1]).total_seconds() / 60
        line = (f"{a}: EMA9={ema9.iloc[-1]:.5f} EMA21={ema21.iloc[-1]:.5f} "
                f"({'sopra' if last > 0 else 'sotto'}), "
                f"ultima candela {HOURS}h chiusa {age_min:.0f} min fa")
        print(line)
        report.append(line)
        if age_min <= WINDOW_MIN:
            if prev <= 0 < last:
                send(f"🟢 {a}: EMA 9 incrocia sopra EMA 21 ({HOURS}h)")
            elif prev >= 0 > last:
                send(f"🔴 {a}: EMA 9 incrocia sotto EMA 21 ({HOURS}h)")

    if TEST:
        send("✅ Test EMA alert funzionante\n" + "\n".join(report))
