"""Torneo evolutivo paper (pedido de Mauricio, 22-oct-2026 22:27 UTC).

Varias VARIANTES de la arquitectura multiagente (cada agente = UNA funcion con
UNA responsabilidad: escaner -> analista -> esceptico -> riesgo -> ejecucion)
compiten con USD 500 ficticios cada una. Meta: USD 1000 (2x). Muerte: equity
< USD 250, o 30 dias sin ser rentable. Cuando muere una variante se documenta
el aprendizaje y solo UNA queda viva al final de la generacion; la siguiente
generacion nace de los aprendizajes.

GENERACION 1:
- A_mr4h:   superviviente del torneo anterior (mean reversion RSI14 4h, filtro
            regimen BTC>SMA200d). Control con backtest: IS -4.3%, OOS +4.6%
            (evidencia debil).
- B_mr4h_sm: A + veto smart money: no entra si los top-10 de Hyperliquid estan
            net short en ese activo. Sin historico -> validacion solo en vivo.
- C_smfollow: sigue al smart money: long en el activo con mayor posicion neta
            long de los top-10 (si hay alguna positiva y regimen ok). Salida:
            flip a net short, stop 2xATR, o 96h. Sin historico -> solo en vivo.

Honestidad: B y C son experimentos en tiempo real (fuera de muestra por
construccion, pero sin pasado medible). No hay promesa de rentabilidad; la
probabilidad de 2x rapido es baja (ver Monte Carlo en README).
"""
import csv
import json
import os
import time

from . import data, smartmoney
from .reto100 import rsi, atr, UNIVERSE, FEE, SLIP

START = 500.0
TARGET = 1000.0
DEATH = 250.0
MAX_DAYS_UNPROFITABLE = 30
RISK_PCT = 0.0075
NOTIONAL_CAP = 0.30
MAX_OPEN = 2
RSI_IN, RSI_OUT = 30.0, 55.0
STOP_ATR = 2.0
TIME_STOP_MS = 96 * 3600 * 1000
MIN_NOTIONAL = 5.0
BORN_DAY = "2026-10-22"

STATUS_JSON = "torneo_status.json"
EQUITY_CSV = "torneo_equity.csv"
TRADES_CSV = "torneo_trades.csv"

VARIANTS = ["A_mr4h", "B_mr4h_sm", "C_smfollow"]


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def load_book(v):
    path = f"torneo_{v}_state.json"
    if os.path.exists(path):
        return json.load(open(path))
    return {"variant": v, "cash": START, "positions": [], "status": "alive",
            "peak": START, "last_candle": 0, "trades": 0, "wins": 0,
            "losses": 0, "born": now_iso(), "died": None, "reason": None}


def save_book(b):
    b["updated"] = now_iso()
    json.dump(b, open(f"torneo_{b['variant']}_state.json", "w"), indent=2)


def log_trade(b, p, exit_px, motive):
    proceeds = p["qty"] * exit_px * (1 - FEE)
    pnl = proceeds - p["cost"]
    b["cash"] += proceeds
    b["trades"] += 1
    b["wins" if pnl > 0 else "losses"] += 1
    new = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["variant", "symbol", "open_ts", "close_ts", "entry_px",
                        "exit_px", "qty", "pnl_usd", "exit_reason"])
        w.writerow([b["variant"], p["symbol"], p["open_ts"], now_iso(),
                    round(p["entry_px"], 6), round(exit_px, 6),
                    round(p["qty"], 6), round(pnl, 4), motive])
    return pnl


def regime_ok(ts_ms):
    ks = data.fetch_klines("BTCUSDT", "1d", 210)
    closed = [c for c in ks if c["close_time"] < ts_ms]
    if len(closed) < 200:
        return False
    closes = [c["close"] for c in closed]
    return closes[-1] > sum(closes[-200:]) / 200


# ---------- agentes (cada uno UNA cosa) ----------
def ag_escaner_mr(sym, cs):
    """Escaner MR: rsi de la ultima vela cerrada < umbral."""
    closes = [x["close"] for x in cs]
    r = rsi(closes, 14)
    return r is not None and r < RSI_IN


def ag_analista_atr(cs):
    """Analista: ATR(14) de las ultimas 31 velas."""
    return atr(cs[-31:], 14)


def ag_esceptico_sm(sym, net):
    """Esceptico smart money: veta si top-10 net short en el activo."""
    coin = sym.replace("USDT", "")
    return net.get(coin, 0.0) >= 0.0


def ag_riesgo(eq, a, px):
    """Riesgo: qty por riesgo fijo con tope de notional."""
    qty = min(RISK_PCT * eq / (STOP_ATR * a), NOTIONAL_CAP * eq / px)
    return qty if qty * px >= MIN_NOTIONAL else None


def ag_ejecucion(b, sym, px, a, candle_ms):
    """Ejecucion simulada: fill con slippage + fee."""
    qty = ag_riesgo(equity_of(b, {}), a, px)
    if qty is None:
        return False
    fill = px * (1 + SLIP)
    cost = qty * fill * (1 + FEE)
    if cost > b["cash"]:
        return False
    b["cash"] -= cost
    b["positions"].append({"symbol": sym, "qty": qty, "entry_px": fill,
                           "cost": cost, "stop": fill - STOP_ATR * a,
                           "open_ms": candle_ms, "open_ts": now_iso()})
    print(f"[torneo:{b['variant']}] ABRIR {sym} @ {fill:.2f} notional ${qty*fill:.2f}")
    return True


def equity_of(b, marks):
    return b["cash"] + sum(p["qty"] * marks.get(p["symbol"], p["entry_px"])
                           for p in b["positions"])


def manage_exits(b, k4, ts_ms, extra_exit=None):
    for p in list(b["positions"]):
        cs = k4.get(p["symbol"])
        if not cs:
            continue
        c = cs[-1]
        closes = [x["close"] for x in cs]
        r = rsi(closes, 14)
        exit_px, motive = None, None
        if c["low"] <= p["stop"]:
            exit_px, motive = p["stop"] * (1 - SLIP), "stop"
        elif extra_exit and extra_exit(p, r, c):
            exit_px, motive = c["close"] * (1 - SLIP), extra_exit(p, r, c)
        elif r is not None and r > RSI_OUT and b["variant"] != "C_smfollow":
            exit_px, motive = c["close"] * (1 - SLIP), "rsi_salida"
        elif c["close_time"] - p["open_ms"] > TIME_STOP_MS:
            exit_px, motive = c["close"] * (1 - SLIP), "tiempo"
        if exit_px is not None:
            b["positions"].remove(p)
            pnl = log_trade(b, p, exit_px, motive if isinstance(motive, str) else "senal")
            print(f"[torneo:{b['variant']}] CIERRE {p['symbol']} ({motive}) pnl ${pnl:.2f}")


def entries_mr(b, k4, net, use_sm):
    if len(b["positions"]) >= MAX_OPEN:
        return
    for sym in UNIVERSE:
        if len(b["positions"]) >= MAX_OPEN:
            break
        if any(p["symbol"] == sym for p in b["positions"]):
            continue
        cs = k4.get(sym)
        if not cs or len(cs) < 31:
            continue
        if not ag_escaner_mr(sym, cs):          # escaner
            continue
        if use_sm and not ag_esceptico_sm(sym, net):  # esceptico smart money
            print(f"[torneo:{b['variant']}] {sym} VETO_SM (top10 net short)")
            continue
        a = ag_analista_atr(cs)                  # analista
        if not a:
            continue
        ag_ejecucion(b, sym, cs[-1]["close"], a, cs[-1]["close_time"])  # riesgo+ejecucion


def entries_smfollow(b, k4, net, ts_ms):
    if len(b["positions"]) >= MAX_OPEN:
        return
    cands = sorted(((c, u) for c, u in net.items() if u > 0 and f"{c}USDT" in UNIVERSE),
                   key=lambda kv: -kv[1])
    for coin, usd in cands:
        if len(b["positions"]) >= MAX_OPEN:
            break
        sym = f"{coin}USDT"
        if any(p["symbol"] == sym for p in b["positions"]):
            continue
        cs = k4.get(sym)
        if not cs or len(cs) < 31:
            continue
        a = ag_analista_atr(cs)
        if not a:
            continue
        print(f"[torneo:{b['variant']}] smart money net long {coin}: ${usd:,.0f}")
        ag_ejecucion(b, sym, cs[-1]["close"], a, cs[-1]["close_time"])


def smfollow_flip_exit(net):
    def f(p, r, c):
        coin = p["symbol"].replace("USDT", "")
        if net.get(coin, 0.0) < 0:
            return "flip_sm"
        return None
    return f


def check_survival(b, eq, ts_ms):
    if b["status"] != "alive":
        return
    if eq < DEATH:
        for p in list(b["positions"]):
            b["positions"].remove(p)
            log_trade(b, p, p["entry_px"], "muerte")
        b.update(status="dead", died=now_iso(),
                 reason=f"equity ${eq:.2f} < ${DEATH} (eliminada)")
        print(f"[torneo:{b['variant']}] MUERTE: {b['reason']}")
    elif eq >= TARGET:
        b.update(status="objetivo", died=now_iso(),
                 reason=f"reto cumplido: equity ${eq:.2f} >= ${TARGET}")
        print(f"[torneo:{b['variant']}] OBJETIVO: {b['reason']}")
    else:
        days = (ts_ms - time.mktime(time.strptime(BORN_DAY, "%Y-%m-%d")) * 1000) / 86400000
        if days >= MAX_DAYS_UNPROFITABLE and eq < START:
            b.update(status="dead", died=now_iso(),
                     reason=f"{MAX_DAYS_UNPROFITABLE}d sin ser rentable (${eq:.2f} < ${START})")
            print(f"[torneo:{b['variant']}] MUERTE: {b['reason']}")


def step(ts_ms):
    ts = now_iso()
    try:
        net = smartmoney.snapshot()
        smartmoney.log_snapshot(ts, net)
    except Exception as e:
        net = {}
        print(f"[torneo] smartmoney error: {e}")
    k4 = {}
    for sym in UNIVERSE:
        try:
            ks = data.fetch_klines(sym, "4h", 220)
            k4[sym] = [c for c in ks if c["close_time"] < ts_ms]
        except Exception as e:
            print(f"[torneo] {sym}: error de datos: {e}")
    if not k4.get("BTCUSDT"):
        return
    newest = k4["BTCUSDT"][-1]["open_time"]
    try:
        reg = regime_ok(ts_ms)
    except Exception as e:
        reg = False
        print(f"[torneo] regimen error: {e}")
    marks = {s: cs[-1]["close"] for s, cs in k4.items() if cs}
    status = {}
    for v in VARIANTS:
        b = load_book(v)
        if b["status"] != "alive":
            status[v] = {"equity": round(equity_of(b, marks), 2), "status": b["status"],
                         "reason": b.get("reason"), "trades": b["trades"]}
            save_book(b)
            continue
        if newest > b.get("last_candle", 0):
            b["last_candle"] = newest
            extra = smfollow_flip_exit(net) if v == "C_smfollow" else None
            manage_exits(b, k4, ts_ms, extra_exit=extra)
            eq0 = equity_of(b, marks)
            if reg and eq0 >= DEATH:
                if v in ("A_mr4h", "B_mr4h_sm"):
                    entries_mr(b, k4, net, use_sm=(v == "B_mr4h_sm"))
                elif v == "C_smfollow":
                    entries_smfollow(b, k4, net, ts_ms)
        eq = equity_of(b, marks)
        b["peak"] = max(b.get("peak", START), eq)
        check_survival(b, eq, ts_ms)
        status[v] = {"equity": round(eq, 2), "status": b["status"],
                     "reason": b.get("reason"), "trades": b["trades"],
                     "open": len(b["positions"])}
        save_book(b)
        print(f"[torneo:{v}] equity ${eq:.2f} | {b['status']} | trades {b['trades']}")
        new = not os.path.exists(EQUITY_CSV)
        with open(EQUITY_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["ts", "variant", "equity", "status"])
            w.writerow([ts, v, round(eq, 2), b["status"]])
    json.dump({"ts": ts, "generation": 1, "start": START, "target": TARGET,
               "death": DEATH, "variants": status},
              open(STATUS_JSON, "w"), indent=2)


if __name__ == "__main__":
    step(int(time.time() * 1000))
