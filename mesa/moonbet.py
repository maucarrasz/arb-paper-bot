"""Torneo GEN2: apuestas subida/bajada con apalancamiento (pedido de Mauricio,
22-oct-2026 19:20 -05): "eso quiero, apuestas de subida y bajada, que las
arquitecturas de los bots sean para eso, solo que quede el mejor".

Mecanica modelada desde la documentacion VERIFICADA de Moon (help.moon.com):
- Apuesta direccional ABIERTA (up/down), sin horizonte fijo.
- wager W = perdida maxima. posicion = W * leverage (hasta 1000x).
- Liquidacion ("bust"): la apuesta se cierra perdiendo TODO el wager cuando
  el precio se mueve 1/L en contra (up: bust = entry*(1-1/L); down: entry*(1+1/L)).
- Cierre manual por senal en cualquier momento.
- Fees: apertura 1% SOLO sobre W. Performance fee MINIMO documentado 10% del
  beneficio (solo en ganadoras; 0 en perdedoras).
- Fuente: help.moon.com/articles/15064402 (leverage), 15388257 (fees).

HONESTIDAD (correccion de main 19:21): el 10% es el MINIMO documentado, no la
tarifa exacta universal. La simulacion lo usa como ESCENARIO OPTIMISTA explicito
y registra sensibilidad al 30% (escenario pesimista) en cada trade. Esto NO es
una replica exacta de la rentabilidad/payout de Moon: sus fills, slippage y
precio de cierre real no son observables desde fuera. Moon.com ademas esta
DESCARTADO como venue (ToS 3.2 prohibe Peru y United States); este torneo es
100% simulacion propia sobre datos de mercado reales publicos.
"""
import csv
import json
import os
import time

from . import data, smartmoney
from .reto100 import rsi, atr, UNIVERSE
from . import evolucion as evo

START = 500.0
TARGET = 1000.0
BORN_DAY = "2026-10-22"
# Ventana fija del torneo: born GEN2 (2026-10-23 00:22:30 UTC) + 7 dias.
# Los relevos comparten este cierre (el torneo NO se vuelve infinito).
CLOSE_ISO = "2026-10-30 00:22:30 UTC"
# Muerte por inactividad-horas + perdida diaria/total (FTMO) + relevo
# automatico con hipotesis: mesa/evolucion.py (reglas usuario 23-oct).
OPEN_FEE = 0.01          # 1% sobre wager (verificado)
PERF_FEE_OPT = 0.10      # minimo documentado = escenario optimista
PERF_FEE_PESS = 0.30     # sensibilidad pesimista (no documentada: prudencia)
SLIP = 0.0005
MAX_OPEN = 2

STATUS_JSON = "moonbet_status.json"
SPAWNED_JSON = "moonbet_spawned.json"
EQUITY_CSV = "moonbet_equity.csv"
TRADES_CSV = "moonbet_trades.csv"

# 10 arquitecturas: senal de direccion x apalancamiento x sizing.
# dir_sig: mr_up (RSI<rsi_in -> up, salida RSI>rsi_out), mr_down (RSI>100-rsi_in
# -> down, salida RSI<100-rsi_out), momo_up (close > close[-lookback]*(1+thr)),
# sm (direccion del smart money net), brk_up (cierre > max 20 cierres previos).
PARAMS = {
 "U1_mr_up_5x":     {"sig":"mr_up","rsi_in":30,"rsi_out":55,"lev":5,"wager_pct":0.02,"tf":"4h"},
 "U2_mr_up_25x":    {"sig":"mr_up","rsi_in":30,"rsi_out":55,"lev":25,"wager_pct":0.02,"tf":"4h"},
 "U3_mr_down_25x":  {"sig":"mr_down","rsi_in":30,"rsi_out":55,"lev":25,"wager_pct":0.02,"tf":"4h"},
 "U4_momo_up_10x":  {"sig":"momo_up","lookback":3,"thr":0.015,"lev":10,"wager_pct":0.02,"tf":"4h"},
 "U5_sm_10x":       {"sig":"sm","lev":10,"wager_pct":0.02,"tf":"4h"},
 "U6_mr_up_10x_1h": {"sig":"mr_up","rsi_in":25,"rsi_out":55,"lev":10,"wager_pct":0.02,"tf":"1h"},
 "U7_brk_up_50x":   {"sig":"brk_up","lookback":20,"lev":50,"wager_pct":0.01,"tf":"4h"},
 "U8_mr_up_100x":   {"sig":"mr_up","rsi_in":25,"rsi_out":55,"lev":100,"wager_pct":0.01,"tf":"4h"},
 "U9_mr_down_100x": {"sig":"mr_down","rsi_in":25,"rsi_out":55,"lev":100,"wager_pct":0.01,"tf":"4h"},
 "U10_sm_3x_big":   {"sig":"sm","lev":3,"wager_pct":0.05,"tf":"4h"},
}
VARIANTS = list(PARAMS)


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def load_book(v):
    path = f"moonbet_{v}_state.json"
    if os.path.exists(path):
        return json.load(open(path))
    return {"variant": v, "cash": START, "bets": [], "status": "alive",
            "peak": START, "last_candle": 0, "bets_n": 0, "wins": 0,
            "losses": 0, "busts": 0, "born": now_iso(), "died": None,
            "reason": None, "generation": 2}


def save_book(b):
    b["updated"] = now_iso()
    json.dump(b, open(f"moonbet_{b['variant']}_state.json", "w"), indent=2)


def pnl_gross(bet, px):
    move = (px / bet["entry_px"] - 1.0) * bet["dir"]
    return bet["wager"] * bet["lev"] * move


def bet_value(bet, px):
    """Valor actual de la apuesta abierta (escenario optimista de fees)."""
    g = pnl_gross(bet, px)
    fee = g * PERF_FEE_OPT if g > 0 else 0.0
    return max(0.0, bet["wager"] + g - fee)


def equity_of(b, marks):
    return b["cash"] + sum(bet_value(t, marks.get(t["symbol"], t["entry_px"]))
                           for t in b["bets"])


def close_bet(b, bet, px, motive):
    g = pnl_gross(bet, px)
    fee_opt = g * PERF_FEE_OPT if g > 0 else 0.0
    fee_pess = g * PERF_FEE_PESS if g > 0 else 0.0
    net_opt = g - fee_opt
    net_pess = g - fee_pess
    b["cash"] += max(0.0, bet["wager"] + net_opt)
    b["bets_n"] += 1
    if motive == "bust":
        b["busts"] += 1
    b["wins" if net_opt > 0 else "losses"] += 1
    new = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["variant", "symbol", "dir", "wager", "lev", "open_ts",
                        "close_ts", "entry_px", "exit_px", "pnl_gross",
                        "pnl_net_opt10", "pnl_net_pess30", "exit_reason"])
        w.writerow([b["variant"], bet["symbol"], "up" if bet["dir"] > 0 else "down",
                    round(bet["wager"], 2), bet["lev"], bet["open_ts"], now_iso(),
                    round(bet["entry_px"], 6), round(px, 6), round(g, 4),
                    round(net_opt, 4), round(net_pess, 4), motive])
    return net_opt


def manage_bets(b, kx):
    for bet in list(b["bets"]):
        cs = kx.get(bet["symbol"])
        if not cs:
            continue
        c = cs[-1]
        # bust intracandle (conservador: el extremo adverso manda)
        busted = (c["low"] <= bet["bust_px"]) if bet["dir"] > 0 else (c["high"] >= bet["bust_px"])
        if busted:
            b["bets"].remove(bet)
            close_bet(b, bet, bet["bust_px"], "bust")
            evo.touch(b, c["close_time"])
            print(f"[moonbet:{b['variant']}] BUST {bet['symbol']} wager ${bet['wager']:.2f}")
            continue
        closes = [x["close"] for x in cs]
        r = rsi(closes, 14)
        sig = PARAMS[b["variant"]]["sig"]
        exit_px, motive = None, None
        if sig == "mr_up" and r is not None and r > PARAMS[b["variant"]]["rsi_out"]:
            exit_px, motive = c["close"] * (1 - SLIP), "rsi_salida"
        elif sig == "mr_down" and r is not None and r < 100 - PARAMS[b["variant"]]["rsi_out"]:
            exit_px, motive = c["close"] * (1 + SLIP), "rsi_salida"
        elif sig == "momo_up":
            lb = PARAMS[b["variant"]]["lookback"]
            if len(closes) > lb and c["close"] < closes[-1 - lb]:
                exit_px, motive = c["close"] * (1 - SLIP), "momo_flip"
        if exit_px is not None:
            b["bets"].remove(bet)
            net = close_bet(b, bet, exit_px, motive)
            evo.touch(b, c["close_time"])
            print(f"[moonbet:{b['variant']}] CIERRE {bet['symbol']} ({motive}) net ${net:.2f}")


def open_bet(b, sym, direction, px, candle_ms):
    p = PARAMS[b["variant"]]
    eq = equity_of(b, {sym: px})
    wager = p["wager_pct"] * eq
    cost = wager * (1 + OPEN_FEE)
    if wager < 1.0 or cost > b["cash"]:
        return False
    entry = px * (1 + SLIP) if direction > 0 else px * (1 - SLIP)
    bust = entry * (1 - 1.0 / p["lev"]) if direction > 0 else entry * (1 + 1.0 / p["lev"])
    b["cash"] -= cost
    b["bets"].append({"symbol": sym, "dir": direction, "wager": round(wager, 2),
                      "lev": p["lev"], "entry_px": entry, "bust_px": bust,
                      "open_ms": candle_ms, "open_ts": now_iso()})
    evo.touch(b, candle_ms)
    print(f"[moonbet:{b['variant']}] ABRIR {'UP' if direction>0 else 'DOWN'} {sym} "
          f"wager ${wager:.2f} x{p['lev']} @ {entry:.2f} bust {bust:.2f}")
    return True


def entries(b, kx, net, reg):
    p = PARAMS[b["variant"]]
    if len(b["bets"]) >= MAX_OPEN or not reg:
        return
    for sym in UNIVERSE:
        if len(b["bets"]) >= MAX_OPEN:
            break
        if any(t["symbol"] == sym for t in b["bets"]):
            continue
        cs = kx.get(sym)
        if not cs or len(cs) < 31:
            continue
        closes = [x["close"] for x in cs]
        r = rsi(closes, 14)
        sig = p["sig"]
        if sig == "mr_up" and r is not None and r < p["rsi_in"]:
            open_bet(b, sym, +1, cs[-1]["close"], cs[-1]["close_time"])
        elif sig == "mr_down" and r is not None and r > 100 - p["rsi_in"]:
            open_bet(b, sym, -1, cs[-1]["close"], cs[-1]["close_time"])
        elif sig == "momo_up":
            lb = p["lookback"]
            if len(closes) > lb and cs[-1]["close"] > closes[-1 - lb] * (1 + p["thr"]):
                open_bet(b, sym, +1, cs[-1]["close"], cs[-1]["close_time"])
        elif sig == "brk_up":
            lb = p["lookback"]
            if len(closes) > lb and cs[-1]["close"] > max(closes[-1 - lb:-1]):
                open_bet(b, sym, +1, cs[-1]["close"], cs[-1]["close_time"])
        elif sig == "sm":
            coin = sym.replace("USDT", "")
            u = net.get(coin, 0.0)
            # direccion del smart money solo si es la mas fuerte del tablero
            if net and u == max(net.values()) and u > 0:
                open_bet(b, sym, +1, cs[-1]["close"], cs[-1]["close_time"])
            elif net and u == min(net.values()) and u < 0:
                open_bet(b, sym, -1, cs[-1]["close"], cs[-1]["close_time"])


def merge_spawned(spawned=None):
    """Mezcla los relevos registrados sobre PARAMS (idempotente)."""
    spawned = spawned if spawned is not None else evo.load_spawned("moonbet")
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
        print(f"[moonbet:{b['variant']}] OBJETIVO: {b['reason']}")
        return
    fam = evo.family_moonbet(PARAMS[b["variant"]])
    hit = evo.death_reason(b, eq, ts_ms, fam, len(b["bets"]),
                           evo.ms_of(CLOSE_ISO), START)
    if hit is None:
        return
    kind, text = hit
    for bet in list(b["bets"]):
        b["bets"].remove(bet)
        px = marks.get(bet["symbol"], bet["entry_px"])
        px = px * (1 - SLIP) if bet["dir"] > 0 else px * (1 + SLIP)
        close_bet(b, bet, px, "muerte")
    b.update(status="dead", died=now_iso(), reason=text)
    print(f"[moonbet:{b['variant']}] MUERTE: {text}")
    rep = evo.spawn_replacement("moonbet", b, PARAMS[b["variant"]], kind,
                                ts_ms, evo.ms_of(CLOSE_ISO), set(PARAMS))
    if rep and rep.get("blocked"):
        print(f"[moonbet:{b['variant']}] relevo bloqueado: {rep['blocked']}")
    evo.log_death("moonbet", b, kind, rep)


def step(ts_ms):
    ts = now_iso()
    spawned = merge_spawned()
    try:
        net = smartmoney.snapshot()
    except Exception as e:
        net = {}
        print(f"[moonbet] smartmoney error: {e}")
    need_1h = any(PARAMS[v]["tf"] == "1h" and load_book(v)["status"] == "alive"
                  for v in VARIANTS)
    k4, k1 = {}, {}
    for sym in UNIVERSE:
        try:
            k4[sym] = [c for c in data.fetch_klines(sym, "4h", 220) if c["close_time"] < ts_ms]
        except Exception as e:
            print(f"[moonbet] {sym} 4h: {e}")
        if need_1h:
            try:
                k1[sym] = [c for c in data.fetch_klines(sym, "1h", 220) if c["close_time"] < ts_ms]
            except Exception as e:
                print(f"[moonbet] {sym} 1h: {e}")
    if not k4.get("BTCUSDT"):
        return
    try:
        from .torneo import regime_ok
        reg = regime_ok(ts_ms)
    except Exception:
        reg = False
    marks = {s: cs[-1]["close"] for s, cs in k4.items() if cs}
    status = {}
    for v in VARIANTS:
        b = load_book(v)
        b["generation"] = spawned.get(v, {}).get("generation", b.get("generation", 2))
        if b["status"] != "alive":
            status[v] = {"equity": round(equity_of(b, marks), 2), "status": b["status"],
                         "reason": b.get("reason"), "bets": b["bets_n"]}
            save_book(b)
            continue
        p = PARAMS[v]
        kx = k1 if p["tf"] == "1h" else k4
        newest = kx["BTCUSDT"][-1]["open_time"] if kx.get("BTCUSDT") else 0
        if newest > b.get("last_candle", 0):
            b["last_candle"] = newest
            manage_bets(b, kx)
            if equity_of(b, marks) >= evo.floor(START):
                entries(b, kx, net, reg)
        eq = equity_of(b, marks)
        b["peak"] = max(b.get("peak", START), eq)
        check_survival(b, eq, ts_ms, marks)
        status[v] = {"equity": round(eq, 2), "status": b["status"],
                     "reason": b.get("reason"), "bets": b["bets_n"],
                     "open": len(b["bets"])}
        save_book(b)
        print(f"[moonbet:{v}] equity ${eq:.2f} | {b['status']} | bets {b['bets_n']} | busts {b['busts']}")
        new = not os.path.exists(EQUITY_CSV)
        with open(EQUITY_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["ts", "variant", "equity", "status"])
            w.writerow([ts, v, round(eq, 2), b["status"]])
    json.dump({"ts": ts, "generation": 2, "mechanics": "moon-style leveraged up/down bets",
               "fees": {"open": OPEN_FEE, "perf_optimista_min_documentado": PERF_FEE_OPT,
                        "perf_pesimista_sensibilidad": PERF_FEE_PESS},
               "start": START, "target": TARGET,
               "rules": {"perdida_total": "10% inicial ($450, modelo FTMO)",
                          "perdida_diaria": "3% inicial vs apertura del dia UTC ($15, modelo FTMO)",
                          "inactividad_h_por_familia": evo.INACTIVITY_H,
                          "cierre_torneo": CLOSE_ISO,
                          "relevo": "automatico con hipotesis registrada; tope 20; sin relevo <12h al cierre"},
               "nota": "Simulacion propia con fees optimistas; NO replica exacta de Moon. Moon.com descartado por ToS (Peru/US).",
               "variants": status},
              open(STATUS_JSON, "w"), indent=2)


if __name__ == "__main__":
    step(int(time.time() * 1000))
