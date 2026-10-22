"""Reto $100 -> $1000 (paper): libro paralelo con seleccion evolutiva.

Sobreviviente actual de la investigacion walk-forward (ver README seccion
"Reto 100"): Mean Reversion 4h.
  - Universo: 12 pares USDT liquidos.
  - Regimen: BTC cierre diario > SMA200 diaria (dia completado anterior).
  - Entrada: RSI(14) 4h < 30 en vela 4h cerrada. Max 2 posiciones.
  - Riesgo: 0.75% del equity por operacion; notional max 30% del equity.
  - Salida: RSI(14) > 55, o stop = entrada - 2*ATR(14) 4h, o 96h de tiempo.
  - Costos: 0.10% comision + 0.05% deslizamiento por lado.
Reglas de supervivencia (seleccion evolutiva en paper):
  - equity < $50  -> el libro MUERE (se detiene; se reemplaza por variante).
  - equity >= $1000 -> reto cumplido (objetivo declarado, no promesa).
Backtest honesto (590d, IS hasta 2026-04-25, OOS despues): IS -4.3%,
OOS +4.6% (18 trades, Sharpe 1.98, DD 1.7%). Muestra pequena: evidencia
debil, por eso corre solo en paper y puede morir.
"""
import csv
import json
import os
import time

from . import data

STATE = "reto100_state.json"
EQUITY_CSV = "reto100_equity.csv"
TRADES_CSV = "reto100_trades.csv"

UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
            "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "TRXUSDT", "DOTUSDT"]
FEE = 0.001
SLIP = 0.0005
RISK_PCT = 0.0075
NOTIONAL_CAP = 0.30
MAX_OPEN = 2
RSI_N = 14
RSI_IN = 30.0
RSI_OUT = 55.0
STOP_ATR = 2.0
TIME_STOP_MS = 96 * 3600 * 1000
MIN_NOTIONAL = 5.0
START = 100.0
DEATH = 50.0
TARGET = 1000.0


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def rsi(closes, n=14):
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


def atr(candles, n=14):
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


def append_csv(path, fields, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"cash": START, "positions": [], "status": "alive",
            "peak_equity": START, "last_candle": 0, "trades": 0,
            "wins": 0, "losses": 0, "born": now_iso(), "died": None,
            "death_reason": None}


def save_state(st):
    st["updated"] = now_iso()
    json.dump(st, open(STATE, "w"), indent=2)


def regime_ok(ts_ms):
    """BTC cierre diario > SMA200 diaria (ultima vela diaria cerrada)."""
    ks = data.fetch_klines("BTCUSDT", "1d", 210)
    closed = [c for c in ks if c["close_time"] < ts_ms]
    if len(closed) < 200:
        return False
    closes = [c["close"] for c in closed]
    sma200 = sum(closes[-200:]) / 200
    return closes[-1] > sma200


def step(ts_ms):
    st = load_state()
    if st["status"] != "alive":
        print(f"[reto100] estado={st['status']} ({st.get('death_reason')}); sin operar")
        return
    k4 = {}
    for sym in UNIVERSE:
        try:
            ks = data.fetch_klines(sym, "4h", 220)
            k4[sym] = [c for c in ks if c["close_time"] < ts_ms]
        except Exception as e:
            print(f"[reto100] {sym}: error de datos: {e}")
    if not k4.get("BTCUSDT"):
        print("[reto100] sin datos BTC; se omite ciclo")
        return
    newest = k4["BTCUSDT"][-1]["open_time"]
    if newest <= st.get("last_candle", 0):
        print("[reto100] sin vela 4h nueva; se omite ciclo")
        save_state(st)
        return
    st["last_candle"] = newest

    # 1) Salidas con la ultima vela 4h cerrada
    for p in list(st["positions"]):
        cs = k4.get(p["symbol"])
        if not cs:
            continue
        c = cs[-1]
        closes = [x["close"] for x in cs]
        r = rsi(closes, RSI_N)
        exit_px, motive = None, None
        if c["low"] <= p["stop"]:
            exit_px, motive = p["stop"] * (1 - SLIP), "stop"
        elif r is not None and r > RSI_OUT:
            exit_px, motive = c["close"] * (1 - SLIP), "rsi_salida"
        elif c["close_time"] - p["open_ms"] > TIME_STOP_MS:
            exit_px, motive = c["close"] * (1 - SLIP), "tiempo"
        if exit_px is not None:
            proceeds = p["qty"] * exit_px * (1 - FEE)
            pnl = proceeds - p["cost"]
            st["cash"] += proceeds
            st["positions"].remove(p)
            st["trades"] += 1
            st["wins" if pnl > 0 else "losses"] += 1
            append_csv(TRADES_CSV,
                       ["symbol", "open_ts", "close_ts", "entry_px", "exit_px",
                        "qty", "pnl_usd", "exit_reason"],
                       {"symbol": p["symbol"], "open_ts": p["open_ts"],
                        "close_ts": now_iso(), "entry_px": round(p["entry_px"], 6),
                        "exit_px": round(exit_px, 6), "qty": round(p["qty"], 6),
                        "pnl_usd": round(pnl, 4), "exit_reason": motive})
            print(f"[reto100] CIERRE {p['symbol']} ({motive}) pnl ${pnl:.2f}")

    # 2) Equity marcado a mercado
    marks = {s: cs[-1]["close"] for s, cs in k4.items() if cs}
    eq = st["cash"] + sum(p["qty"] * marks.get(p["symbol"], p["entry_px"])
                          for p in st["positions"])
    st["peak_equity"] = max(st.get("peak_equity", START), eq)

    # 3) Supervivencia
    if eq < DEATH:
        for p in list(st["positions"]):
            mk = marks.get(p["symbol"], p["entry_px"]) * (1 - SLIP)
            proceeds = p["qty"] * mk * (1 - FEE)
            st["cash"] += proceeds
            st["positions"].remove(p)
            st["trades"] += 1
            append_csv(TRADES_CSV,
                       ["symbol", "open_ts", "close_ts", "entry_px", "exit_px",
                        "qty", "pnl_usd", "exit_reason"],
                       {"symbol": p["symbol"], "open_ts": p["open_ts"],
                        "close_ts": now_iso(), "entry_px": round(p["entry_px"], 6),
                        "exit_px": round(mk, 6), "qty": round(p["qty"], 6),
                        "pnl_usd": round(proceeds - p["cost"], 4),
                        "exit_reason": "muerte"})
        eq = st["cash"]
        st["status"] = "dead"
        st["died"] = now_iso()
        st["death_reason"] = f"equity ${eq:.2f} < ${DEATH}"
        print(f"[reto100] MUERTE: {st['death_reason']}")
    elif eq >= TARGET:
        st["status"] = "objetivo"
        st["died"] = now_iso()
        st["death_reason"] = f"reto cumplido: equity ${eq:.2f} >= ${TARGET}"
        print(f"[reto100] OBJETIVO: {st['death_reason']}")

    # 4) Entradas (solo si sigue vivo y hay regimen)
    if st["status"] == "alive" and len(st["positions"]) < MAX_OPEN:
        try:
            ok = regime_ok(ts_ms)
        except Exception as e:
            ok = False
            print(f"[reto100] regimen: error de datos: {e}")
        if ok:
            for sym in UNIVERSE:
                if len(st["positions"]) >= MAX_OPEN:
                    break
                if any(p["symbol"] == sym for p in st["positions"]):
                    continue
                cs = k4.get(sym)
                if not cs or len(cs) < 30:
                    continue
                c = cs[-1]
                closes = [x["close"] for x in cs]
                r = rsi(closes, RSI_N)
                if r is None or r >= RSI_IN:
                    continue
                a = atr(cs[-31:], 14)
                if not a:
                    continue
                risk = RISK_PCT * eq
                qty = min(risk / (STOP_ATR * a), NOTIONAL_CAP * eq / c["close"])
                fill = c["close"] * (1 + SLIP)
                cost = qty * fill * (1 + FEE)
                if cost > st["cash"] or qty * fill < MIN_NOTIONAL:
                    continue
                st["cash"] -= cost
                st["positions"].append({
                    "symbol": sym, "qty": qty, "entry_px": fill, "cost": cost,
                    "stop": fill - STOP_ATR * a, "open_ms": c["close_time"],
                    "open_ts": now_iso()})
                print(f"[reto100] ABRIR {sym} @ {fill:.2f} rsi {r:.1f} "
                      f"notional ${qty * fill:.2f}")
                eq = st["cash"] + sum(p["qty"] * marks.get(p["symbol"], p["entry_px"])
                                      for p in st["positions"])

    append_csv(EQUITY_CSV, ["ts", "equity", "cash", "open_positions", "status"],
               {"ts": now_iso(), "equity": round(eq, 2), "cash": round(st["cash"], 2),
                "open_positions": len(st["positions"]), "status": st["status"]})
    save_state(st)
    print(f"[reto100] equity ${eq:.2f} | cash ${st['cash']:.2f} | "
          f"abiertas {len(st['positions'])} | {st['status']}")


if __name__ == "__main__":
    step(int(time.time() * 1000))
