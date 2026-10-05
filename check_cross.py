import json
import os
import pandas as pd
import requests
import yfinance as yf

ASSETS = ["EURUSD=X", "USDJPY=X", "GBPUSD=X", "AUDUSD=X", "USDCHF=X", "USDSGD=X"]

# --- Impostazioni del timeframe -------------------------------------------
# TIMEFRAME_MIN: durata della candela in minuti (5, 15, 30, 60, 240 = 4h...)
# MAX_AGE_MIN:   un incrocio viene notificato solo se la sua candela si è
#                chiusa da meno di X minuti (evita avvisi vecchi/inutili).
#                Ogni incrocio viene comunque notificato UNA volta sola.
#                   5 min  -> TIMEFRAME_MIN=5,   MAX_AGE_MIN=30,  cron "*/5 * * * *"
#                   15 min -> TIMEFRAME_MIN=15,  MAX_AGE_MIN=60,  cron "*/15 * * * *"
#                   4 ore  -> TIMEFRAME_MIN=240, MAX_AGE_MIN=240, cron "*/15 * * * *"
TIMEFRAME_MIN = 240
MAX_AGE_MIN = 240
# ---------------------------------------------------------------------------

TZ_FOREX = "America/New_York"  # le candele forex da 4h partono da 17:00 di New York
TZ_LOCAL = "Europe/Rome"
STATE_FILE = "state.json"      # ricorda l'ultimo incrocio già notificato
WARMUP = 30                    # candele iniziali scartate (le EMA non sono ancora stabili)


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


def tv_link(asset):
    """Link al grafico TradingView della coppia, già sul timeframe impostato.
    Usa la fonte FX_IDC (ICE Data Services), la stessa da cui Yahoo prende
    i cambi '=X', così il grafico mostra gli stessi prezzi dello script.
    Simbolo Yahoo 'EURUSD=X' -> simbolo TradingView 'FX_IDC:EURUSD'."""
    symbol = f"FX_IDC:{asset[:-2]}" if asset.endswith("=X") else asset
    return f"https://www.tradingview.com/chart/?symbol={symbol}&interval={TIMEFRAME_MIN}"


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


def last_cross(ema9, ema21, ends):
    """Ultimo incrocio (in qualsiasi direzione) tra le candele chiuse.
    Ritorna (direzione, istante di chiusura della candela) oppure None."""
    above = (ema9 > ema21).iloc[WARMUP:]
    changed = above != above.shift(1)
    changed.iloc[0] = False
    if not changed.any():
        return None
    pos = changed.to_numpy().nonzero()[0][-1]
    return ("su" if above.iloc[pos] else "giu"), ends[WARMUP:][pos]


def check_asset(asset, raw, now, state):
    """Ritorna (riga di report, messaggio da inviare o None).
    Aggiorna `state` quando un incrocio viene notificato."""
    close, ends = closed_candles(raw, now)
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    age_min = (now - ends[-1]).total_seconds() / 60
    line = (f"{asset}: EMA9={ema9.iloc[-1]:.5f} EMA21={ema21.iloc[-1]:.5f} "
            f"({'sopra' if ema9.iloc[-1] > ema21.iloc[-1] else 'sotto'}), "
            f"ultima candela {label()} chiusa {age_min:.0f} min fa")

    cross = last_cross(ema9, ema21, ends)
    message = None
    if cross:
        direction, end = cross
        start = (end - pd.Timedelta(minutes=TIMEFRAME_MIN)).tz_convert(TZ_LOCAL)
        cross_age = (now - end).total_seconds() / 60
        arrow = "🟢 EMA 9 incrocia SOPRA EMA 21" if direction == "su" else "🔴 EMA 9 incrocia SOTTO EMA 21"
        line += (f"\nultimo incrocio: {'al rialzo' if direction == 'su' else 'al ribasso'}, "
                 f"candela delle {start.strftime('%H:%M')} (ora italiana, {start.strftime('%d/%m')}), "
                 f"{cross_age:.0f} min fa")
        key = end.tz_convert("UTC").isoformat()
        if cross_age <= MAX_AGE_MIN and state.get(asset) != key:
            message = (f"{arrow}\n{asset} ({label()}), candela delle {start.strftime('%H:%M')}\n"
                       f"{tv_link(asset)}")
            state[asset] = key
    line += f"\n{tv_link(asset)}"
    return line, message


def main():
    test = os.environ.get("TEST") == "true"
    now = pd.Timestamp.now(tz="UTC")
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    old_state = dict(state)

    report = []
    for a in ASSETS:
        line, message = check_asset(a, download(a), now, state)
        print(line)
        report.append(line)
        if message:
            send(message)

    if state != old_state:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    if test:
        send("✅ Test EMA alert funzionante\n" + "\n".join(report))


if __name__ == "__main__":
    main()
