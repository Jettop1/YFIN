import json
import os
import pandas as pd
import requests
import yfinance as yf

ASSETS = ["EURUSD=X", "USDJPY=X", "GBPUSD=X", "AUDUSD=X", "USDCHF=X", "USDSGD=X"]

# --- Controlli -------------------------------------------------------------
# Ogni riga è un controllo separato, fatto su tutte le coppie di ASSETS.
#   tf:       durata della candela in minuti (15, 60, 240 = 4h...)
#   max_age:  un incrocio viene notificato solo se la sua candela si è chiusa
#             da meno di X minuti (evita avvisi vecchi). Ogni incrocio viene
#             comunque notificato UNA volta sola.
# cron-job.org deve chiamare ogni 15 minuti: espressione "5,20,35,50 * * * *"
PROFILES = [
    {"tf": 240, "max_age": 240},                    # 4 ore: tutti gli incroci
    {"tf": 15, "max_age": 60, "only_nypm": True},   # 15 min: solo da fine NY PM a fine Asia  # [NYPM-STAR]
]
# ---------------------------------------------------------------------------

# --- [NYPM-STAR] Kill zone, in ora di NEW YORK (giuste anche con i cambi d'ora)  # [NYPM-STAR]
NYPM_END = "16:00"   # fine kill zone New York PM  # [NYPM-STAR]
ASIA_END = "00:00"   # fine kill zone asiatica     # [NYPM-STAR]

TZ_FOREX = "America/New_York"  # le candele forex da 4h partono da 17:00 di New York
TZ_LOCAL = "Europe/Rome"
STATE_FILE = "state.json"      # ricorda l'ultimo incrocio già notificato
WARMUP = 30                    # candele iniziali scartate (le EMA non sono ancora stabili)
TG_LIMIT = 3900                # lunghezza massima di un messaggio Telegram (con margine)


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{os.environ['TG_TOKEN']}/sendMessage",
        data={"chat_id": os.environ["TG_CHAT_ID"], "text": msg},
        timeout=30,
    )


def label(tf):
    return f"{tf // 60}h" if tf % 60 == 0 else f"{tf}m"


def tv_link(asset, tf):
    """Link al grafico TradingView della coppia, sul timeframe del controllo.
    Usa la fonte FX_IDC (ICE Data Services), la stessa da cui Yahoo prende
    i cambi '=X', così il grafico mostra gli stessi prezzi dello script.
    Simbolo Yahoo 'EURUSD=X' -> simbolo TradingView 'FX_IDC:EURUSD'."""
    symbol = f"FX_IDC:{asset[:-2]}" if asset.endswith("=X") else asset
    return f"https://www.tradingview.com/chart/?symbol={symbol}&interval={tf}"


def download(asset, tf):
    if tf < 60:
        data = yf.download(asset, period="5d", interval="5m", progress=False)
    else:
        data = yf.download(asset, period="1mo", interval="1h", progress=False)
    return data["Close"].squeeze()


def closed_candles(close_raw, now, tf):
    """Raggruppa le chiusure in candele da `tf` minuti e tiene solo quelle
    già chiuse (esclude la candela ancora in formazione)."""
    close_raw = close_raw.dropna()
    close_raw.index = close_raw.index.tz_convert(TZ_FOREX)
    offset = "1h" if tf == 240 else "0min"
    candles = close_raw.resample(f"{tf}min", offset=offset).last().dropna()
    end = candles.index + pd.Timedelta(minutes=tf)
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


def nypm_star(end, tf):  # [NYPM-STAR]
    """True se la candela che si chiude a `end` cade tra la fine di NY PM  # [NYPM-STAR]
    e la fine della kill zone asiatica (orari di New York)."""  # [NYPM-STAR]
    start = (end - pd.Timedelta(minutes=tf)).tz_convert(TZ_FOREX)  # [NYPM-STAR]
    h0, m0 = map(int, NYPM_END.split(":"))  # [NYPM-STAR]
    h1, m1 = map(int, ASIA_END.split(":"))  # [NYPM-STAR]
    win_len = (h1 * 60 + m1 - (h0 * 60 + m0)) % 1440  # durata finestra in minuti  # [NYPM-STAR]
    s = (start.hour * 60 + start.minute - (h0 * 60 + m0)) % 1440  # inizio candela da fine NY PM  # [NYPM-STAR]
    return s < win_len or s + tf > 1440  # la candela tocca la finestra  # [NYPM-STAR]


def check_asset(asset, raw, now, state, prof):
    """Ritorna (riga di report, messaggio da inviare o None).
    Aggiorna `state` quando un incrocio viene notificato."""
    tf = prof["tf"]
    close, ends = closed_candles(raw, now, tf)
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    age_min = (now - ends[-1]).total_seconds() / 60
    line = (f"{asset} [{label(tf)}]: EMA9={ema9.iloc[-1]:.5f} EMA21={ema21.iloc[-1]:.5f} "
            f"({'sopra' if ema9.iloc[-1] > ema21.iloc[-1] else 'sotto'}), "
            f"ultima candela chiusa {age_min:.0f} min fa")

    cross = last_cross(ema9, ema21, ends)
    message = None
    if cross:
        direction, end = cross
        start = (end - pd.Timedelta(minutes=tf)).tz_convert(TZ_LOCAL)
        cross_age = (now - end).total_seconds() / 60
        arrow = "🟢 EMA 9 incrocia SOPRA EMA 21" if direction == "su" else "🔴 EMA 9 incrocia SOTTO EMA 21"
        line += (f"\nultimo incrocio: {'al rialzo' if direction == 'su' else 'al ribasso'}, "
                 f"candela delle {start.strftime('%H:%M')} (ora italiana, {start.strftime('%d/%m')}), "
                 f"{cross_age:.0f} min fa")
        # chiave nello stato: per il 4h resta il solo nome della coppia (compatibile con lo stato già salvato)
        state_key = asset if tf == 240 else f"{asset}@{label(tf)}"
        key = end.tz_convert("UTC").isoformat()
        notify = cross_age <= prof["max_age"] and state.get(state_key) != key
        if prof.get("only_nypm"):  # [NYPM-STAR]
            in_window = nypm_star(end, tf)  # [NYPM-STAR]
            arrow += "  ⭐ dopo NY PM"  # [NYPM-STAR]
            line += "  ⭐ dopo NY PM" if in_window else "  (fuori finestra NY PM-Asia)"  # [NYPM-STAR]
            notify = notify and in_window  # [NYPM-STAR]
        if notify:
            message = (f"{arrow}\n{asset} ({label(tf)}), candela delle {start.strftime('%H:%M')}\n"
                       f"{tv_link(asset, tf)}")
            state[state_key] = key
    line += f"\n{tv_link(asset, tf)}"
    return line, message


def send_report(header, lines):
    """Invia il report di test, diviso in più messaggi se troppo lungo."""
    chunk = header
    for line in lines:
        if len(chunk) + len(line) + 2 > TG_LIMIT:
            send(chunk)
            chunk = ""
        chunk += ("\n\n" if chunk else "") + line
    if chunk:
        send(chunk)


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
    cache = {}
    for a in ASSETS:
        for prof in PROFILES:
            tf = prof["tf"]
            src = "5m" if tf < 60 else "1h"  # stessi dati scaricati una volta sola
            if (a, src) not in cache:
                cache[(a, src)] = download(a, tf)
            line, message = check_asset(a, cache[(a, src)], now, state, prof)
            print(line)
            report.append(line)
            if message:
                send(message)

    if state != old_state:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    if test:
        send_report("✅ Test EMA alert funzionante", report)


if __name__ == "__main__":
    main()
