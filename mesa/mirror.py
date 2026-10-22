"""Espejo de ejecucion REAL del torneo en dYdX v4 testnet.

Corre FUERA de GitHub Actions (el mnemonic no vive en el repo): el agente lo
ejecuta cada hora desde su sandbox tras el run horario de Actions.

Que hace:
1. Compara las posiciones paper de cada variante (torneo_<v>_state.json) con
   la ultima foto del espejo: aperturas = BUY market, cierres = SELL market,
   en la subcuenta de la variante (indice en torneo.VARIANTS).
2. Si MIRROR_LIVE != "1", corre en DRY-RUN: loguea lo que haria sin ordenar.
3. Lee equity REAL de cada subcuenta desde el indexer y escribe
   torneo_real_status.json + torneo_real_equity.csv (comparacion honesta
   paper vs real: fees, slippage y latencia reales).

Nunca toca mainnet. Si una senal es de un mercado que dYdX no lista (BNB) o
el size es menor al minimo del mercado, se salta y queda en el log.
"""
import asyncio
import csv
import json
import os
import time

from . import dydx_exec
from .torneo import VARIANTS, PARAMS, START, TARGET, DEATH

LIVE = os.environ.get("MIRROR_LIVE") == "1"
MIRROR_STATE = "torneo_mirror_state.json"
REAL_STATUS = "torneo_real_status.json"
REAL_EQUITY = "torneo_real_equity.csv"


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def load_mirror():
    if os.path.exists(MIRROR_STATE):
        return json.load(open(MIRROR_STATE))
    return {"processed_trades": 0, "funded": {}, "started": None}


def paper_equity():
    """Equity paper actual por variante desde torneo_status.json."""
    if not os.path.exists("torneo_status.json"):
        return {}
    d = json.load(open("torneo_status.json"))
    out = {}
    for v, s in d.get("variants", {}).items():
        out[v] = s.get("equity")
    return out


def position_diffs(state):
    """Diff de posiciones por variante: (variant, 'BUY'|'SELL', symbol, qty).

    El ledger paper (torneo_<v>_state.json) es la fuente de senales: las
    posiciones NUEVAS se compran y las que desaparecieron se venden, todo en
    la subcuenta de la variante. Clave de posicion: symbol+open_ms."""
    ops = []
    last = state.setdefault("positions", {})
    for v in VARIANTS:
        path = f"torneo_{v}_state.json"
        cur = {}
        if os.path.exists(path):
            b = json.load(open(path))
            if b.get("status") == "alive":
                for p in b.get("positions", []):
                    cur[f"{p['symbol']}:{p['open_ms']}"] = (p["symbol"], p["qty"])
        prev = last.get(v, {})
        for k, (sym, qty) in cur.items():
            if k not in prev:
                ops.append((v, "BUY", sym, qty))
        for k, (sym, qty) in prev.items():
            if k not in cur:
                ops.append((v, "SELL", sym, qty))
        last[v] = cur
    return ops


async def replay(wallet, ops, live_markets):
    for v, side, sym, qty in ops:
        sub = VARIANTS.index(v)
        mkt = dydx_exec.SYMBOL_MAP.get(sym)
        if not mkt or mkt not in live_markets:
            print(f"[mirror] {v}: {sym} sin mercado en dYdX -> skip")
            continue
        print(f"[mirror] {v} sub{sub}: {side} {qty} {mkt} ({'LIVE' if LIVE else 'DRY'})")
        if not LIVE:
            continue
        try:
            tx = await dydx_exec.market_order(wallet, sub, mkt, side, float(qty))
            print(f"[mirror]   -> ok {str(tx)[:80]}")
        except Exception as e:
            print(f"[mirror]   -> ERROR: {e}")


def main():
    state = load_mirror()
    if not dydx_exec.MNEMONIC:
        print("[mirror] sin DYDX_MNEMONIC: solo estado paper, ejecucion desactivada")
    wallet = addr = None
    if dydx_exec.MNEMONIC:
        wallet, addr = dydx_exec.wallet_and_address()
        print(f"[mirror] wallet {addr}")
    try:
        live_markets = dydx_exec.markets_live()
    except Exception as e:
        live_markets = set()
        print(f"[mirror] markets error: {e}")
    ops = position_diffs(state)
    print(f"[mirror] {len(ops)} operaciones por replicar")
    if wallet and ops:
        asyncio.run(replay(wallet, ops, live_markets))
    peq = paper_equity()
    status = {"updated": now_iso(), "venue": "dydx_v4_testnet",
              "live": LIVE, "wallet": addr, "variants": {}}
    line = [now_iso()]
    for i, v in enumerate(VARIANTS):
        real = dydx_exec.equity(addr, i) if addr else None
        paper = peq.get(v)
        div = (real - paper) if (real is not None and paper is not None) else None
        status["variants"][v] = {
            "subaccount": i, "equity_real": real, "equity_paper": paper,
            "divergence_usd": round(div, 2) if div is not None else None,
            "mode": PARAMS[v]["mode"],
        }
        line.append("" if real is None else f"{real:.2f}")
        print(f"[mirror] {v}: real={real} paper={paper} div={div}")
    json.dump(status, open(REAL_STATUS, "w"), indent=2)
    new = not os.path.exists(REAL_EQUITY)
    with open(REAL_EQUITY, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts"] + VARIANTS)
        w.writerow(line)
    json.dump(state, open(MIRROR_STATE, "w"), indent=2)
    print(f"[mirror] estado guardado ({'LIVE' if LIVE else 'DRY-RUN'})")


if __name__ == "__main__":
    main()
