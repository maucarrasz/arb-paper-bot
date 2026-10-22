"""Datos publicos: velas (klines) de Binance data-api (gratis, sin clave)
e indicadores deterministas (SMA, EMA, RSI/ATR de Wilder)."""
import time
import requests

from . import config

S = requests.Session()


def fetch_klines(symbol, interval=config.INTERVAL, limit=config.KLINES_LIMIT,
                 start_time=None, end_time=None):
    """Devuelve lista de dicts con velas cerradas/abiertas ordenadas por tiempo."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time is not None:
        params["startTime"] = int(start_time)
    if end_time is not None:
        params["endTime"] = int(end_time)
    r = S.get(config.KLINE_URL, params=params, timeout=15)
    r.raise_for_status()
    out = []
    for k in r.json():
        out.append({
            "open_time": int(k[0]), "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
            "close_time": int(k[6]),
        })
    return out


def fetch_history(symbol, interval, days, end_ms=None):
    """Descarga historico paginado (1000 velas por llamada) para backtest."""
    ms = 24 * 3600 * 1000
    end = end_ms if end_ms is not None else int(time.time() * 1000)
    start = end - days * ms
    out = []
    cursor = start
    while cursor < end:
        batch = fetch_klines(symbol, interval, 1000, start_time=cursor, end_time=end)
        if not batch:
            break
        out.extend(batch)
        cursor = batch[-1]["open_time"] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.25)  # amable con la API publica
    # dedup por open_time
    seen, uniq = set(), []
    for c in out:
        if c["open_time"] not in seen:
            seen.add(c["open_time"])
            uniq.append(c)
    return uniq


def resample(candles, factor):
    """Agrupa 'factor' velas consecutivas (p.ej. 4 velas 1h -> 1 vela 4h)."""
    out = []
    for i in range(0, len(candles) - factor + 1, factor):
        g = candles[i:i + factor]
        out.append({
            "open_time": g[0]["open_time"], "open": g[0]["open"],
            "high": max(c["high"] for c in g), "low": min(c["low"] for c in g),
            "close": g[-1]["close"], "volume": sum(c["volume"] for c in g),
            "close_time": g[-1]["close_time"],
        })
    return out


def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema_series(vals, n):
    if len(vals) < n:
        return []
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    out = [e]
    for v in vals[n:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def ema(vals, n):
    s = ema_series(vals, n)
    return s[-1] if s else None


def rsi(closes, n=config.RSI_N):
    """RSI de Wilder sobre los ultimos n cambios."""
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:n]) / n
    al = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def atr(candles, n=config.ATR_N):
    """ATR de Wilder."""
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for i in range(n, len(trs)):
        a = (a * (n - 1) + trs[i]) / n
    return a
