"""Los 6 roles de la mesa, deterministicos e interpretables.
Cada uno devuelve su decision Y sus razones (quedan en el CSV de senales)."""
import time
from dataclasses import dataclass, field

from . import config, data


@dataclass
class Signal:
    symbol: str
    vol_ratio: float = 0.0
    ret_3h: float = 0.0
    rsi: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    trend_1h: bool = False
    trend_4h: bool = False
    news_score: int = 0
    news_label: str = "-"
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    rr: float = 0.0
    candidate: bool = False
    analyst_ok: bool = False
    analyst_reasons: list = field(default_factory=list)
    skeptic_decision: str = "-"      # COMPRAR / ESPERAR / PASAR
    skeptic_reasons: list = field(default_factory=list)
    risk_ok: bool = False
    risk_reasons: list = field(default_factory=list)
    qty: float = 0.0
    notional: float = 0.0
    final: str = "SIN_CANDIDATO"     # ABRIR / VETO / SIN_CANDIDATO


def scan(symbol, candles, now_ts=None):
    """Bot 1 - Scanner: detecta vela cerrada con volumen anormal + momentum."""
    closed = [c for c in candles if now_ts is None or c["close_time"] < now_ts]
    if len(closed) < 60:
        return Signal(symbol=symbol)
    closes = [c["close"] for c in closed]
    vols = [c["volume"] for c in closed]
    v_sma = data.sma(vols[:-1], 20) or 0.0
    vol_ratio = (closed[-1]["volume"] / v_sma) if v_sma else 0.0
    ret_3h = closes[-1] / closes[-4] - 1.0
    sig = Signal(symbol=symbol, vol_ratio=vol_ratio, ret_3h=ret_3h)
    sig.candidate = vol_ratio >= config.VOL_RATIO_MIN and ret_3h >= config.RET_3H_MIN
    return sig


def analyze(sig, candles, candles_4h):
    """Bot 3 - Analista: propone entrada/stop/objetivo con filtros tecnicos."""
    closed = candles
    closes = [c["close"] for c in closed]
    c4 = [c["close"] for c in candles_4h]
    sig.rsi = data.rsi(closes) or 0.0
    sig.atr = data.atr(closed) or 0.0
    px = closes[-1]
    sig.atr_pct = sig.atr / px if px else 0.0
    e20, e50 = data.ema(closes, config.EMA_FAST), data.ema(closes, config.EMA_SLOW)
    f20, f50 = data.ema(c4, config.EMA_FAST), data.ema(c4, config.EMA_SLOW)
    sig.trend_1h = bool(e20 and e50 and e20 > e50)
    sig.trend_4h = bool(f20 and f50 and f20 > f50)
    ok = True
    if not sig.trend_1h:
        ok = False; sig.analyst_reasons.append("sin tendencia 1h (EMA20<=EMA50)")
    if not sig.trend_4h:
        ok = False; sig.analyst_reasons.append("sin tendencia 4h")
    if sig.rsi > config.RSI_LONG_MAX:
        ok = False; sig.analyst_reasons.append(f"RSI {sig.rsi:.0f} sobreextendido")
    if sig.atr_pct > config.ATR_PCT_MAX:
        ok = False; sig.analyst_reasons.append(f"ATR {sig.atr_pct*100:.1f}% caotico")
    if sig.atr <= 0:
        ok = False; sig.analyst_reasons.append("ATR nulo")
    sig.entry = px
    sig.stop = px - config.STOP_ATR * sig.atr
    sig.target = px + config.TARGET_ATR * sig.atr
    sig.rr = (sig.target - px) / (px - sig.stop) if px > sig.stop else 0.0
    if sig.rr < config.MIN_RR - 1e-9:  # epsilon: 2.25/1.5 puede dar 1.4999... en float
        ok = False; sig.analyst_reasons.append(f"R:R {sig.rr:.2f} < {config.MIN_RR}")
    sig.analyst_ok = ok
    if ok:
        sig.analyst_reasons.append(
            f"plan: entrada {px:.2f}, stop {sig.stop:.2f} (-{config.STOP_ATR}*ATR), "
            f"objetivo {sig.target:.2f} (+{config.TARGET_ATR}*ATR), R:R {sig.rr:.2f}")
    return sig


def skeptical(sig, news_ctx, open_symbols, cooldown_active, daily_cap_hit):
    """Bot 4 - Esceptico: rebate el plan; decide COMPRAR / ESPERAR / PASAR."""
    sig.news_score = news_ctx.get("score", 0)
    sig.news_label = news_ctx.get("label", "-")
    veto = []
    if not sig.analyst_ok:
        veto.append("analista no valida: " + "; ".join(sig.analyst_reasons))
    if sig.news_score <= config.NEWS_VETO_SCORE:
        veto.append(f"noticias muy negativas ({sig.news_label}, score {sig.news_score})")
    if sig.symbol in open_symbols:
        veto.append("ya hay posicion abierta en este activo")
    if cooldown_active:
        veto.append("en cooldown por perdidas recientes")
    if daily_cap_hit:
        veto.append("limite de perdida diaria alcanzado")
    if abs(sig.ret_3h) > 0.08:
        veto.append(f"movimiento {sig.ret_3h*100:.1f}% ya muy extendido (perseguir = mal precio)")
    sig.skeptic_reasons = veto
    if veto:
        sig.skeptic_decision = "PASAR"
    elif sig.vol_ratio >= 3.0 and sig.trend_4h:
        sig.skeptic_decision = "COMPRAR"
    else:
        sig.skeptic_decision = "COMPRAR"  # plan valido y sin objeciones
    return sig


def size_up(sig, equity, n_open):
    """Bot 5 - Riesgo: sizing por riesgo fijo con topes."""
    if n_open >= config.MAX_OPEN:
        sig.risk_reasons.append(f"max {config.MAX_OPEN} posiciones abiertas")
        return sig
    risk_usd = equity * config.RISK_PCT
    dist = sig.entry - sig.stop
    if dist <= 0:
        sig.risk_reasons.append("distancia al stop nula")
        return sig
    qty_risk = risk_usd / dist
    qty_cap = (equity * config.NOTIONAL_CAP) / sig.entry
    sig.qty = min(qty_risk, qty_cap)
    sig.notional = sig.qty * sig.entry
    if sig.notional < 20:
        sig.risk_reasons.append("tamano resultante < $20, no vale la comision")
        return sig
    sig.risk_ok = True
    sig.risk_reasons.append(
        f"riesgo ${risk_usd:.2f} ({config.RISK_PCT*100:.2f}% equity), "
        f"qty {sig.qty:.6f}, notional ${sig.notional:.2f}")
    return sig
