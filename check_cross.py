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
#                   4 ore -> TIMEFRAME_MIN=240, MAX_AGE_MIN=240, cron "5 * * * *"
TIMEFRAME_MIN = 240
MAX_AGE_MIN = 240
# ---------------------------------------------------------------------------

# --- [NYPM-STAR] Stellina sugli incroci del 4h ------------------------------  # [NYPM-STAR]
# Quando c'è un incrocio sul 4h, lo script guarda il 15 minuti e cerca         # [NYPM-STAR]
# l'ultimo incrocio a 15m nella STESSA direzione (nelle STAR_LOOKBACK_H ore    # [NYPM-STAR]
# prima della chiusura della candela 4h). Se quell'incrocio a 15m è avvenuto   # [NYPM-STAR]
# tra la fine della kill zone NY PM e la fine della kill zone asiatica,        # [NYPM-STAR]
# il messaggio del 4h ha la stellina. Orari in ora di NEW YORK.                # [NYPM-STAR]
NYPM_END = "16:00"      # fine kill zone New York PM                           # [NYPM-STAR]
ASIA_END = "00:00"      # fine kill zone asiatica                              # [NYPM-STAR]
STAR_TF_MIN = 15        # timeframe su cui si verifica la kill zone            # [NYPM-STAR]
STAR_LOOKBACK_H = 24    # quanto indietro cercare l'incrocio a 15m             # [NYPM-STAR]

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


def label(tf=None):
    tf = tf or TIMEFRAME_MIN
    return f"{tf // 60}h" if tf % 60 == 0 else f"{tf}m"


def tv_link(asset):
    """Link al grafico TradingView della coppia, già sul timeframe impostato.
    Usa la fonte FX_IDC (ICE Data Services), la stessa da cui Yahoo prende
    i cambi '=X', così il grafico mostra gli stessi prezzi dello script.
    Simbolo Yahoo 'EURUSD=X' -> simbolo TradingView 'FX_IDC:EURUSD'."""
    symbol = f"FX_IDC:{asset[:-2]}" if asset.endswith("=X") else asset
    return f"https://www.tradingview.com/chart/?symbol={symbol}&interval={TIMEFRAME_MIN}"


def download(asset, tf=None):
    tf = tf or TIMEFRAME_MIN
    if tf < 60:
        data = yf.download(asset, period="5d", interval="5m", progress=False)
    else:
        data = yf.download(asset, period="1mo", interval="1h", progress=False)
    return data["Close"].squeeze()


def closed_candles(close_raw, now, tf=None):
    """Raggruppa le chiusure in candele da `tf` minuti e tiene solo quelle
    già chiuse (esclude la candela ancora in formazione)."""
    tf = tf or TIMEFRAME_MIN
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


def in_nypm_window(end, tf):  # [NYPM-STAR]
    """True se la candela da `tf` minuti che si chiude a `end` cade tra  # [NYPM-STAR]
    la fine di NY PM e la fine della kill zone asiatica (ora di New York)."""  # [NYPM-STAR]
    start = (end - pd.Timedelta(minutes=tf)).tz_convert(TZ_FOREX)  # [NYPM-STAR]
    h0, m0 = map(int, NYPM_END.split(":"))  # [NYPM-STAR]
    h1, m1 = map(int, ASIA_END.split(":"))  # [NYPM-STAR]
    win_len = (h1 * 60 + m1 - (h0 * 60 + m0)) % 1440  # durata finestra in minuti  # [NYPM-STAR]
    s = (start.hour * 60 + start.minute - (h0 * 60 + m0)) % 1440  # inizio candela da fine NY PM  # [NYPM-STAR]
    return s < win_len or s + tf > 1440  # la candela tocca la finestra  # [NYPM-STAR]


def star_check(raw15, direction, end4h):  # [NYPM-STAR]
    """Cerca sul 15m l'ultimo incrocio nella stessa direzione dell'incrocio 4h,  # [NYPM-STAR]
    entro STAR_LOOKBACK_H ore prima della chiusura della candela 4h.  # [NYPM-STAR]
    Ritorna (stellina sì/no, chiusura della candela 15m dell'incrocio o None);  # [NYPM-STAR]
    stellina = None se i dati a 15m non coprono quel periodo."""  # [NYPM-STAR]
    if raw15.dropna().index.min() > end4h - pd.Timedelta(hours=STAR_LOOKBACK_H + 8):  # [NYPM-STAR]
        return None, None  # dati 15m troppo recenti per questo incrocio  # [NYPM-STAR]
    c, ends = closed_candles(raw15, end4h, STAR_TF_MIN)  # [NYPM-STAR]
    above = (c.ewm(span=9, adjust=False).mean() > c.ewm(span=21, adjust=False).mean()).iloc[WARMUP:]  # [NYPM-STAR]
    ends = ends[WARMUP:]  # [NYPM-STAR]
    turned = (above != above.shift(1)) & (above == (direction == "su"))  # [NYPM-STAR]
    turned.iloc[0] = False  # [NYPM-STAR]
    hits = [e for e, t in zip(ends, turned.to_numpy()) if t and e > end4h - pd.Timedelta(hours=STAR_LOOKBACK_H)]  # [NYPM-STAR]
    if not hits:  # [NYPM-STAR]
        return False, None  # [NYPM-STAR]
    return in_nypm_window(hits[-1], STAR_TF_MIN), hits[-1]  # [NYPM-STAR]


def star_text(raw15, direction, end):  # [NYPM-STAR]
    """Testo da aggiungere al messaggio: stellina e orario dell'incrocio a 15m."""  # [NYPM-STAR]
    star, e15 = star_check(raw15, direction, end)  # [NYPM-STAR]
    if star is None:  # [NYPM-STAR]
        return None  # [NYPM-STAR]
    if not star:  # [NYPM-STAR]
        return ""  # [NYPM-STAR]
    t15 = (e15 - pd.Timedelta(minutes=STAR_TF_MIN)).tz_convert(TZ_LOCAL).strftime("%H:%M")  # [NYPM-STAR]
    return f"  ⭐ dopo NY PM (15m alle {t15})"  # [NYPM-STAR]


def check_asset(asset, raw, now, state, get_raw15=None):
    """Ritorna (riga di report, messaggio da inviare o None).
    Aggiorna `state` quando un incrocio viene notificato.
    `get_raw15` scarica i dati a 15m solo se servono (per la stellina)."""
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
        notify = cross_age <= MAX_AGE_MIN and state.get(asset) != key
        if get_raw15 and (notify or os.environ.get("TEST") == "true"):  # [NYPM-STAR]
            star = star_text(get_raw15(), direction, end)  # [NYPM-STAR]
            arrow += star or ""  # [NYPM-STAR]
            line += star if star else ("  (15m: dati non disponibili, incrocio troppo vecchio)" if star is None else "  (15m: nessuna stellina)")  # [NYPM-STAR]
        if notify:
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
        get_raw15 = None
        get_raw15 = lambda a=a: download(a, STAR_TF_MIN)  # [NYPM-STAR]
        line, message = check_asset(a, download(a), now, state, get_raw15)
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
