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

from . import data, news, smartmoney, variante_d
from .reto100 import rsi, atr, UNIVERSE, FEE, SLIP
from . import evolucion as evo

START = 500.0
TARGET = 1000.0
# Ventana fija del torneo: born GEN1 (2026-10-22 22:28:59 UTC) + 7 dias.
# Los relevos comparten este cierre (el torneo NO se vuelve infinito).
CLOSE_ISO = "2026-10-29 22:28:59 UTC"
# Muerte por inactividad-horas + perdida diaria/total (FTMO) + relevo
# automatico con hipotesis: mesa/evolucion.py (reglas usuario 23-oct).
RISK_PCT = 0.0075
NOTIONAL_CAP = 0.30
MAX_OPEN = 2
RSI_IN, RSI_OUT = 30.0, 55.0
STOP_ATR = 2.0
TIME_STOP_MS = 96 * 3600 * 1000
MIN_NOTIONAL = 5.0
BORN_DAY = "2026-10-22"

STATUS_JSON = "torneo_status.json"
SPAWNED_JSON = "torneo_spawned.json"
EQUITY_CSV = "torneo_equity.csv"
TRADES_CSV = "torneo_trades.csv"

# Tabla de variantes GEN1 (10): cada fila es UNA arquitectura con parametros
# propios. mode: mr (mean reversion), smfollow (seguir smart money),
# msb11 (mesa 11 agentes del reel). tf: timeframe de senal.
PARAMS = {
 "A_mr4h":      {"mode":"mr","tf":"4h","rsi_in":30,"rsi_out":55,"stop_atr":2.0,"risk":0.0075,"cap":0.30,"sm_veto":False,"time_stop_h":96},
 "B_mr4h_sm":   {"mode":"mr","tf":"4h","rsi_in":30,"rsi_out":55,"stop_atr":2.0,"risk":0.0075,"cap":0.30,"sm_veto":True,"time_stop_h":96},
 "C_smfollow":  {"mode":"smfollow","tf":"4h","rsi_in":30,"rsi_out":55,"stop_atr":2.0,"risk":0.0075,"cap":0.30,"sm_veto":False,"time_stop_h":96},
 "D_msb11":    {"mode":"msb11","tf":"4h","rsi_in":30,"rsi_out":55,"stop_atr":2.0,"risk":0.0075,"cap":0.30,"sm_veto":False,"time_stop_h":96,"sm_analyst":True},
 "E_mr1h":     {"mode":"mr","tf":"1h","rsi_in":25,"rsi_out":55,"stop_atr":2.0,"risk":0.0075,"cap":0.30,"sm_veto":False,"time_stop_h":24},
 "F_mr4h_deep":{"mode":"mr","tf":"4h","rsi_in":25,"rsi_out":55,"stop_atr":3.0,"risk":0.0075,"cap":0.30,"sm_veto":False,"time_stop_h":120},
 "G_msb11_nosm":{"mode":"msb11","tf":"4h","rsi_in":30,"rsi_out":55,"stop_atr":2.0,"risk":0.0075,"cap":0.30,"sm_veto":False,"time_stop_h":96,"sm_analyst":False},
 "H_smfollow_mr":{"mode":"smfollow","tf":"4h","rsi_in":40,"rsi_out":55,"stop_atr":2.0,"risk":0.0075,"cap":0.30,"sm_veto":False,"time_stop_h":96,"need_dip":True},
 "I_mr4h_aggro":{"mode":"mr","tf":"4h","rsi_in":30,"rsi_out":55,"stop_atr":2.0,"risk":0.02,"cap":0.50,"sm_veto":False,"time_stop_h":96},
 "J_msb11_1h": {"mode":"msb11","tf":"1h","rsi_in":30,"rsi_out":55,"stop_atr":2.0,"risk":0.0075,"cap":0.30,"sm_veto":False,"time_stop_h":48,"sm_analyst":True},
}
VARIANTS = list(PARAMS)
ORIGINAL_VARIANTS = list(PARAMS)  # raices de linaje (nunca incluye relevos)


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def load_book(v):
    path = f"torneo_{v}_state.json"
    if os.path.exists(path):
        return json.load(open(path))
    return {"variant": v, "cash": START, "positions": [], "status": "alive",
            "peak": START, "last_candle": 0, "trades": 0, "wins": 0,
            "losses": 0, "born": now_iso(), "died": None, "reason": None,
            "generation": 1}


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
def ag_escaner_mr(sym, cs, rsi_in=RSI_IN):
    """Escaner MR: rsi de la ultima vela cerrada < umbral."""
    closes = [x["close"] for x in cs]
    r = rsi(closes, 14)
    return r is not None and r < rsi_in


def ag_analista_atr(cs):
    """Analista: ATR(14) de las ultimas 31 velas."""
    return atr(cs[-31:], 14)


def ag_esceptico_sm(sym, net):
    """Esceptico smart money: veta si top-10 net short en el activo."""
    coin = sym.replace("USDT", "")
    return net.get(coin, 0.0) >= 0.0


def ag_riesgo(eq, a, px, risk=RISK_PCT, cap=NOTIONAL_CAP, stop_atr=STOP_ATR):
    """Riesgo: qty por riesgo fijo con tope de notional."""
    qty = min(risk * eq / (stop_atr * a), cap * eq / px)
    return qty if qty * px >= MIN_NOTIONAL else None


def ag_ejecucion(b, sym, px, a, candle_ms, p=None):
    """Ejecucion simulada: fill con slippage + fee."""
    p = p or {}
    qty = ag_riesgo(equity_of(b, {}), a, px, risk=p.get("risk", RISK_PCT),
                    cap=p.get("cap", NOTIONAL_CAP), stop_atr=p.get("stop_atr", STOP_ATR))
    if qty is None:
        return False
    fill = px * (1 + SLIP)
    cost = qty * fill * (1 + FEE)
    if cost > b["cash"]:
        return False
    b["cash"] -= cost
    b["positions"].append({"symbol": sym, "qty": qty, "entry_px": fill,
                           "cost": cost, "stop": fill - p.get("stop_atr", STOP_ATR) * a,
                           "open_ms": candle_ms, "open_ts": now_iso(),
                           "time_stop_h": p.get("time_stop_h", 96),
                           "rsi_out": p.get("rsi_out", RSI_OUT)})
    evo.touch(b, candle_ms)
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
        elif r is not None and r > p.get("rsi_out", RSI_OUT) and not b["variant"].startswith("C_"):
            exit_px, motive = c["close"] * (1 - SLIP), "rsi_salida"
        elif c["close_time"] - p["open_ms"] > p.get("time_stop_h", 96) * 3600 * 1000:
            exit_px, motive = c["close"] * (1 - SLIP), "tiempo"
        if exit_px is not None:
            b["positions"].remove(p)
            pnl = log_trade(b, p, exit_px, motive if isinstance(motive, str) else "senal")
            evo.touch(b, c["close_time"])
            print(f"[torneo:{b['variant']}] CIERRE {p['symbol']} ({motive}) pnl ${pnl:.2f}")


def entries_mr(b, kx, net, p):
    if len(b["positions"]) >= MAX_OPEN:
        return
    for sym in UNIVERSE:
        if len(b["positions"]) >= MAX_OPEN:
            break
        if any(p_["symbol"] == sym for p_ in b["positions"]):
            continue
        cs = kx.get(sym)
        if not cs or len(cs) < 31:
            continue
        if not ag_escaner_mr(sym, cs, p["rsi_in"]):     # escaner
            continue
        if p.get("sm_veto") and not ag_esceptico_sm(sym, net):  # esceptico smart money
            print(f"[torneo:{b['variant']}] {sym} VETO_SM (top10 net short)")
            continue
        a = ag_analista_atr(cs)                  # analista
        if not a:
            continue
        ag_ejecucion(b, sym, cs[-1]["close"], a, cs[-1]["close_time"], p)  # riesgo+ejecucion


def entries_smfollow(b, kx, net, p):
    if len(b["positions"]) >= MAX_OPEN:
        return
    cands = sorted(((c, u) for c, u in net.items() if u > 0 and f"{c}USDT" in UNIVERSE),
                   key=lambda kv: -kv[1])
    for coin, usd in cands:
        if len(b["positions"]) >= MAX_OPEN:
            break
        sym = f"{coin}USDT"
        if any(p_["symbol"] == sym for p_ in b["positions"]):
            continue
        cs = kx.get(sym)
        if not cs or len(cs) < 31:
            continue
        if p.get("need_dip"):
            closes = [x["close"] for x in cs]
            r = rsi(closes, 14)
            if r is None or r >= p["rsi_in"]:
                continue
        a = ag_analista_atr(cs)
        if not a:
            continue
        print(f"[torneo:{b['variant']}] smart money net long {coin}: ${usd:,.0f}")
        ag_ejecucion(b, sym, cs[-1]["close"], a, cs[-1]["close_time"], p)


def smfollow_flip_exit(net):
    def f(p, r, c):
        coin = p["symbol"].replace("USDT", "")
        if net.get(coin, 0.0) < 0:
            return "flip_sm"
        return None
    return f


def merge_spawned(spawned=None):
    """Mezcla los relevos registrados sobre PARAMS (idempotente)."""
    spawned = spawned if spawned is not None else evo.load_spawned("torneo")
    for name, entry in spawned.items():
        if name not in PARAMS:
            PARAMS[name] = entry["params"]
    VARIANTS[:] = list(PARAMS)
    return spawned


def check_survival(b, eq, ts_ms, marks):
    if b["status"] != "alive":
        return
    evo.rollover_day(b, eq, ts_ms)
    if eq >= TARGET:
        b.update(status="objetivo", died=now_iso(),
                 reason=f"reto cumplido: equity ${eq:.2f} >= ${TARGET}")
        print(f"[torneo:{b['variant']}] OBJETIVO: {b['reason']}")
        return
    fam = evo.family_torneo(PARAMS[b["variant"]])
    hit = evo.death_reason(b, eq, ts_ms, fam, len(b["positions"]),
                           evo.ms_of(CLOSE_ISO), START)
    if hit is None:
        if ts_ms >= evo.ms_of(CLOSE_ISO) and not b.get("cierre_evaluado"):
            b["cierre_evaluado"] = now_iso()
        return
    kind, text = hit
    for p in list(b["positions"]):
        b["positions"].remove(p)
        log_trade(b, p, marks.get(p["symbol"], p["entry_px"]) * (1 - SLIP), "muerte")
    b.update(status="dead", died=now_iso(), reason=text)
    print(f"[torneo:{b['variant']}] MUERTE: {text}")
    rep = evo.spawn_replacement("torneo", b, PARAMS[b["variant"]], kind,
                                ts_ms, evo.ms_of(CLOSE_ISO), set(PARAMS))
    if rep and rep.get("blocked"):
        print(f"[torneo:{b['variant']}] relevo bloqueado: {rep['blocked']}")
    evo.log_death("torneo", b, kind, rep, eq)


def step(ts_ms):
    ts = now_iso()
    spawned = merge_spawned()
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
    hay_1h = any(PARAMS[v]["tf"] == "1h" and load_book(v)["status"] == "alive"
                 for v in VARIANTS)
    k1 = {}
    if hay_1h:
        for sym in UNIVERSE:
            try:
                ks = data.fetch_klines(sym, "1h", 220)
                k1[sym] = [c for c in ks if c["close_time"] < ts_ms]
            except Exception as e:
                print(f"[torneo] {sym} 1h: error de datos: {e}")
    newest = k4["BTCUSDT"][-1]["open_time"]
    newest1 = k1["BTCUSDT"][-1]["open_time"] if k1.get("BTCUSDT") else 0
    try:
        reg = regime_ok(ts_ms)
    except Exception as e:
        reg = False
        print(f"[torneo] regimen error: {e}")
    marks = {s: cs[-1]["close"] for s, cs in k4.items() if cs}
    status = {}
    for v in VARIANTS:
        b = load_book(v)
        b["generation"] = spawned.get(v, {}).get("generation", b.get("generation", 1))
        if b["status"] != "alive":
            status[v] = {"equity": round(equity_of(b, marks), 2), "status": b["status"],
                         "reason": b.get("reason"), "trades": b["trades"]}
            save_book(b)
            continue
        p = PARAMS[v]
        kx = k1 if p["tf"] == "1h" else k4
        newest_v = newest1 if p["tf"] == "1h" else newest
        if not kx.get("BTCUSDT"):
            status[v] = {"equity": round(equity_of(b, marks), 2), "status": b["status"],
                         "reason": "sin datos 1h", "trades": b["trades"]}
            save_book(b)
            continue
        if newest_v > b.get("last_candle", 0):
            b["last_candle"] = newest_v
            if p["mode"] == "msb11":
                variante_d.manage_exits_d(b, kx, net)
            else:
                extra = smfollow_flip_exit(net) if p["mode"] == "smfollow" else None
                manage_exits(b, kx, ts_ms, extra_exit=extra)
            eq0 = equity_of(b, marks)
            if reg and eq0 >= evo.floor(START) and ts_ms < evo.ms_of(CLOSE_ISO):
                if p["mode"] == "mr":
                    entries_mr(b, kx, net, p)
                elif p["mode"] == "smfollow":
                    entries_smfollow(b, kx, net, p)
                elif p["mode"] == "msb11":
                    try:
                        headlines = news.fetch_headlines()
                    except Exception:
                        headlines = []
                    variante_d.entries_d(b, kx, net, headlines, reg, p)
        eq = equity_of(b, marks)
        b["peak"] = max(b.get("peak", START), eq)
        check_survival(b, eq, ts_ms, marks)
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
    lineages = evo.lineage_report(ORIGINAL_VARIANTS, spawned, status)
    incidente = evo.incident_report(status)
    json.dump({"ts": ts, "generation": 1, "start": START, "target": TARGET,
               "rules": {"perdida_total": "10% inicial ($450, modelo FTMO)",
                          "perdida_diaria": "3% inicial vs apertura del dia UTC ($15, modelo FTMO)",
                          "inactividad_h_por_familia": evo.INACTIVITY_H,
                          "cierre_torneo": CLOSE_ISO,
                          "relevo": "escalera fija con hipotesis (NO aprendizaje automatico); tope 20; sin relevo <12h al cierre"},
               "lineages": lineages,
               "incidente_aisladas": incidente,
               "variants": status},
              open(STATUS_JSON, "w"), indent=2)


if __name__ == "__main__":
    _ts = int(time.time() * 1000)
    step(_ts)
    from . import moonbet
    moonbet.step(_ts)
