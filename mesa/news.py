"""Bot 2 (contexto): lector de noticias gratis por RSS publico, sin claves ni LLM.
CoinTelegraph y Decrypt (RSS publicos). Clasificacion por lexicon simple:
limitacion honesta - no 'entiende' noticias, solo cuenta palabras clave en titulares.
Si los feeds fallan, la mesa opera y registra 'sin datos de noticias'."""
import re
import time
import xml.etree.ElementTree as ET

import requests

FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]
ASSET_KW = {
    "BTCUSDT": [r"\bbitcoin\b", r"\bbtc\b"],
    "ETHUSDT": [r"\bethereum\b", r"\beth\b", r"\bether\b"],
    "SOLUSDT": [r"\bsolana\b", r"\bsol\b"],
}
BULL = ["surge", "rally", "soar", "jump", "gain", "bullish", "record", "breakout",
        "approve", "approval", "adoption", "inflow", "all-time high", "ath", "boost",
        "rebound", "recover", "pump", "climb"]
BEAR = ["crash", "plunge", "drop", "fall", "bearish", "hack", "hacked", "ban",
        "lawsuit", "selloff", "sell-off", "outflow", "fear", "collapse", "liquidat",
        "fraud", "arrest", "probe", "crackdown", "dump", "sink"]


def fetch_headlines(max_age_h=48):
    """Titulares recientes de los feeds RSS. Devuelve lista de (ts, titulo, feed)."""
    out = []
    now = time.time()
    for url in FEEDS:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "mesa-paper-bot/1.0"})
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                ts = now  # si no parsea, se asume reciente
                try:
                    from email.utils import parsedate_to_datetime
                    ts = parsedate_to_datetime(pub).timestamp()
                except Exception:
                    pass
                if title and now - ts <= max_age_h * 3600:
                    out.append((ts, title, url.split("/")[2]))
        except Exception:
            continue  # feed caido: se registra aguas arriba
    return out


def score_symbol(symbol, headlines):
    """Puntaje lexico para un simbolo: +1 por palabra alcista, -1 bajista, en
    titulares que mencionan el activo. Devuelve (score, titulares_usados)."""
    kws = ASSET_KW.get(symbol, [])
    score, used = 0, []
    for ts, title, feed in headlines:
        low = title.lower()
        if kws and not any(re.search(k, low) for k in kws):
            continue
        s = sum(1 for w in BULL if w in low) - sum(1 for w in BEAR if w in low)
        if s != 0:
            used.append(title)
        score += max(-2, min(2, s))
    return score, used


def context(symbol, headlines):
    """Etiqueta determinista: alcista / bajista / mixto-neutral / sin noticias."""
    score, used = score_symbol(symbol, headlines)
    if not used:
        return {"score": 0, "label": "sin noticias del activo", "headlines": []}
    label = "alcista" if score >= 2 else ("bajista" if score <= -2 else "mixto-neutral")
    return {"score": score, "label": label, "headlines": used[:5]}
