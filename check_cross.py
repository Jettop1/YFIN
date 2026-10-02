import os
import pandas as pd
import requests
import yfinance as yf

ASSETS = ["EURUSD=X"]

# --- Impostazioni del timeframe -------------------------------------------
# TIMEFRAME_MIN: durata della candela in minuti (5, 15, 30, 60, 240 = 4h...)
# WINDOW_MIN:    notifica solo se la candela si è chiusa da meno di X minuti.
#                Va tenuto vicino all'intervallo del cron in ema.yml:
#                   5 min  -> TIMEFRAME_MIN=5,   WINDOW_MIN=5,  cron "*/5 * * * *"
#                   15 min -> TIMEFRAME_MIN=15,  WINDOW_MIN=15, cron "*/15 * * * *"
#                   4 ore  -> TIMEFRAME_MIN=240, WINDOW_MIN=60, cron "5 * * * *"
TIMEFRAME_MIN = 5
WINDOW_MIN = 5
# ---------------------------------------------------------------------------

TZ_FOREX = "America/New_York"  # le candele forex da 4h partono da 17:00 di New York


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{os.environ['TG_TOKEN']}/sendMessage",
        data={"chat_id": os.environ["TG_CHAT_ID"], "text": msg},
        timeout=30,
    )


def label():
    if TIMEFRAME_MIN % 60 == 0:
        return f"{TIMEFRAME_MIN // 60}h"
    return f"{TIMEFRAME_MIN}m"


def download(asset):
    if TIMEFRAME_MIN < 60:
        data = yf.download(asset, period="5d", interval="5m", progress=False)
    else:
        data = yf.download(asset, period="1mo", interval="1h", progress=False)
    return data["Close"].squeeze()


def closed_candles(close_raw, now):
    """Raggruppa le chiusure in candele da TIMEFRAME_MIN minuti e tiene solo
    quelle già chiuse (esclude la candela ancora in formazione)."""
    close_raw = close_raw.dropna()
    close_raw.index = close_raw.index.tz_convert(TZ_FOREX)
    offset = "1h" if TIMEFRAME_MIN == 240 else "0min"
    candles = close_raw.resample(f"{TIMEFRAME_MIN}min", offset=offset).last().dropna()
    end = candles.index + pd.Timedelta(minutes=TIMEFRAME_MIN)
    return candles[end <= now], end[end <= now]


TEST = os.environ.get("TEST") == "true"
report = []
now = pd.Timestamp.now(tz="UTC")

if __name__ == "__main__":
    for a in ASSETS:
        close, ends = closed_candles(download(a), now)
        ema9 = close.ewm(span=9, adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        prev = ema9.iloc[-2] - ema21.iloc[-2]
        last = ema9.iloc[-1] - ema21.iloc[-1]
        age_min = (now - ends[-1]).total_seconds() / 60
        line = (f"{a}: EMA9={ema9.iloc[-1]:.5f} EMA21={ema21.iloc[-1]:.5f} "
                f"({'sopra' if last > 0 else 'sotto'}), "
                f"ultima candela {label()} chiusa {age_min:.0f} min fa")
        print(line)
        report.append(line)
        if age_min <= WINDOW_MIN:
            if prev <= 0 < last:
                send(f"🟢 {a}: EMA 9 incrocia sopra EMA 21 ({label()})")
            elif prev >= 0 > last:
                send(f"🔴 {a}: EMA 9 incrocia sotto EMA 21 ({label()})")

    if TEST:
        send("✅ Test EMA alert funzionante\n" + "\n".join(report))
