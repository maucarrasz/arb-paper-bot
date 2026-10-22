"""Variante D_msb11: mesa de 11 agentes (modelo del reel @msbintel, 22-oct).

Arquitectura del reel: 1 escaner elige monedas, 4 analistas construyen el
caso (tecnico, macro, on-chain, sentimiento), 3 validadores (toro/oso/neutral)
debaten, 1 risk manager con veto, 2 auditores post-trade, y el TAMANO lo fijan
reglas fijas, nunca la IA. Ningun agente opera solo: la orden solo sale si
toda la cadena aprueba. Implementado determinista (sin LLM, gratis).

Cadena por candidato:
  escaner -> [an_tecnico, an_macro, an_onchain, an_sentimiento] -> validadores
  -> risk_manager -> sizing fijo -> ejecucion. Al cerrar: auditor1 (causa) +
  auditor2 (leccion) escriben en torneo_audit.csv.
"""
import csv
import os
import time

import time as _time

from . import news
from .reto100 import rsi, UNIVERSE

SLIP = 0.0005
TIME_STOP_MS = 96 * 3600 * 1000
RSI_OUT = 55.0
MAX_OPEN = 2


def now_iso():
    return _time.strftime("%Y-%m-%d %H:%M:%S UTC", _time.gmtime())


def _t():
    from . import torneo  # import perezoso (evita circular)
    return torneo

AUDIT_CSV = "torneo_audit.csv"
ATR_PCT_MAX = 0.04


def escaner(sym, cs):
    """1 escaner: RSI(14) 4h de la ultima vela cerrada."""
    r = rsi([x["close"] for x in cs], 14)
    return r


def an_tecnico(sym, cs):
    """Analista 1 (graficos): sobreventa RSI<30 = +1; tendencia EMA."""
    closes = [x["close"] for x in cs]
    r = rsi(closes, 14)
    if r is None:
        return 0, "sin datos"
    e20 = sum(closes[-20:]) / 20
    e50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else e20
    if r < 30 and e20 >= e50 * 0.97:
        return 1, f"RSI {r:.0f} sobrevendido, tendencia no rota"
    if r > 70:
        return -1, f"RSI {r:.0f} sobrecomprado"
    return 0, f"RSI {r:.0f} neutral"


def an_macro(regimen_ok):
    """Analista 2 (economia/regimen): BTC sobre SMA200 diaria."""
    return (1, "regimen alcista BTC>SMA200d") if regimen_ok else (-1, "regimen bajista")


def an_onchain(sym, net):
    """Analista 3 (on-chain): posicionamiento neto top-10 Hyperliquid."""
    coin = sym.replace("USDT", "")
    u = net.get(coin, 0.0)
    if u > 0:
        return 1, f"smart money net long ${u:,.0f}"
    if u < 0:
        return -1, f"smart money net short ${u:,.0f}"
    return 0, "smart money sin posicion"


def an_sentimiento(sym, headlines):
    """Analista 4 (animo online): score RSS del simbolo."""
    ctx = news.context(sym.replace("USDT", ""), headlines)
    s = ctx["score"]
    if s >= 2:
        return 1, f"noticias {ctx['label']}"
    if s <= -2:
        return -1, f"noticias {ctx['label']}"
    return 0, ctx["label"]


def validadores(votos):
    """3 validadores: toro (suma de votos), oso (conteo de negativos),
    neutral (veto si toro no supera a oso por 2)."""
    toro = sum(v for v, _ in votos)
    oso = sum(1 for v, _ in votos if v < 0)
    razones = "; ".join(f"{r}" for _, r in votos)
    if toro >= 2 and toro - oso >= 2:
        return True, f"TORO {toro} vs OSO {oso}: {razones}"
    return False, f"rechazado TORO {toro} vs OSO {oso}: {razones}"


def risk_manager(cs, px, n_open):
    """Risk manager con veto: volatilidad caotica o libro lleno."""
    if n_open >= MAX_OPEN:
        return False, "libro lleno"
    a = _t().ag_analista_atr(cs)
    if not a or a / px > ATR_PCT_MAX:
        return False, "ATR%>4: mercado caotico"
    return True, a


def auditor1(p, exit_px, motive, pnl):
    """Auditor 1 (causa): que paso vs que se esperaba."""
    if motive == "stop":
        return "stop tocado: entrada mala o volatilidad subestimada"
    if motive == "rsi_salida" and pnl > 0:
        return "plan cumplido: sobreventa revertida"
    if motive == "tiempo":
        return "la tesis tardo demasiado: momentum insuficiente"
    return f"salida {motive}"


def auditor2(b, p, motive, pnl, contexto):
    """Auditor 2 (leccion evolutiva): que aprende la proxima generacion."""
    if pnl < 0 and "net short" in contexto:
        return "LECCION: no comprar sobrevendido cuando smart money esta short"
    if pnl < 0 and "regimen bajista" in contexto:
        return "LECCION: regimen bajista + sobreventa = trampa"
    if pnl > 0:
        return "LECCION: cadena completa alineada funciona; repetir condiciones"
    return "LECCION: revisar timing de entrada"


def log_audit(b, p, exit_px, motive, pnl, contexto):
    a1 = auditor1(p, exit_px, motive, pnl)
    a2 = auditor2(b, p, motive, pnl, contexto)
    new = not os.path.exists(AUDIT_CSV)
    with open(AUDIT_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "variant", "symbol", "pnl_usd", "exit_reason",
                        "auditor1_causa", "auditor2_leccion"])
        w.writerow([now_iso(), b["variant"], p["symbol"], round(pnl, 4),
                    motive, a1, a2])


def manage_exits_d(b, k4, net):
    for p in list(b["positions"]):
        cs = k4.get(p["symbol"])
        if not cs:
            continue
        c = cs[-1]
        r = rsi([x["close"] for x in cs], 14)
        exit_px, motive = None, None
        if c["low"] <= p["stop"]:
            exit_px, motive = p["stop"] * (1 - SLIP), "stop"
        elif r is not None and r > p.get("rsi_out", RSI_OUT):
            exit_px, motive = c["close"] * (1 - SLIP), "rsi_salida"
        elif c["close_time"] - p["open_ms"] > p.get("time_stop_h", 96) * 3600 * 1000:
            exit_px, motive = c["close"] * (1 - SLIP), "tiempo"
        if exit_px is not None:
            b["positions"].remove(p)
            pnl = _t().log_trade(b, p, exit_px, motive)
            log_audit(b, p, exit_px, motive, pnl, p.get("contexto", ""))
            print(f"[torneo:D_msb11] CIERRE {p['symbol']} ({motive}) pnl ${pnl:.2f}")


def entries_d(b, k4, net, headlines, regimen_ok, prm=None):
    prm = prm or {}
    for sym in UNIVERSE:
        if len(b["positions"]) >= MAX_OPEN:
            break
        if any(p["symbol"] == sym for p in b["positions"]):
            continue
        cs = k4.get(sym)
        if not cs or len(cs) < 55:
            continue
        r = escaner(sym, cs)
        if r is None or r >= 40:
            continue
        votos = [an_tecnico(sym, cs), an_macro(regimen_ok)]
        if prm.get("sm_analyst", True):
            votos.append(an_onchain(sym, net))
        else:
            votos.append(an_tecnico(sym, cs[:-12] if len(cs) > 67 else cs))  # momentum reciente como 3er analista
        votos.append(an_sentimiento(sym, headlines))
        ok, veredicto = validadores(votos)
        if not ok:
            print(f"[torneo:D_msb11] {sym} {veredicto[:110]}")
            continue
        a = _t().ag_analista_atr(cs)
        ok2, _ = risk_manager(cs, cs[-1]["close"], len(b["positions"]))
        if not ok2 or not a:
            print(f"[torneo:D_msb11] {sym} VETO_RIESGO")
            continue
        if _t().ag_ejecucion(b, sym, cs[-1]["close"], a, cs[-1]["close_time"], prm):
            b["positions"][-1]["contexto"] = veredicto
