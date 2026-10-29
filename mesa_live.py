"""Mesa torneo EN VIVO en segundos (pedido de Mauricio, 27-oct-2026:
"no me sirve, debe ser en segundos").

Loop continuo (corridas encadenadas de ~5h40m en GitHub Actions, mismo
patron que paper.yml) que complementa a los pasos por vela cerrada:

1) Cada --poll segundos lee precios en vivo (REST ticker publico de Binance,
   sin clave) y evalua, por variante viva:
   - GEN1 (torneo): cierre por STOP si el precio vivo toca el stop
     (fill stop*(1-SLIP), mismo supuesto que la version por vela).
   - GEN2 (moonbet): BUST si el precio vivo toca bust_px (liquidacion 1/L).
   - Supervivencia (perdida total/diaria FTMO, inactividad, meta $1000) con
     equity mark-to-market en vivo -> las muertes y la meta se detectan en
     segundos, no en el siguiente tick de 3-5h.
2) Detecta cierres de vela (1h/4h) en ~15-30s y corre los pasos COMPLETOS
   existentes (torneo.step / moonbet.step): entradas por senal, salidas por
   RSI/tiempo, smart money, relevos y status JSON quedan IDENTICOS, solo que
   se evaluan segundos despues del cierre de vela en vez de horas.
   Las mechas intracadela que el polling de 5s no ve siguen cubiertas por el
   chequeo de low/high de la vela cerrada en esos pasos (antes tardaban
   horas; ahora segundos tras el cierre).
3) Persiste: commitea/pushea estado al repo en cada evento (apertura, cierre,
   muerte, objetivo) y al menos cada 15 min. Heartbeat mesa_live_state.json
   para que el cron horario (mesa.yml) se salte torneo/moonbet mientras el
   vivo este sano (ver gate en mesa/desk.py).

Limites honestos:
- Reaccion en segundos = gestion de stops/busts/muertes/meta. Las SENALES de
  entrada siguen siendo de vela cerrada 1h/4h (la estrategia no cambia).
- Polling cada 5s puede no ver una mecha de <5s que se recupere; la vela
  cerrada la captura despues (cobertura compuesta, no perdida).
- Fills simulados con los mismos supuestos (slippage/fee) que la version por
  ticks: mejor latencia, mismas reglas economicas.

Uso: python mesa_live.py --seconds 20400 --poll 5
     python mesa_live.py --seconds 120 --poll 5 --shadow   (prueba local)
"""
import argparse
import json
import os
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mesa import torneo, moonbet
from mesa import evolucion as evo
from mesa.reto100 import UNIVERSE, FEE, SLIP

TICKER_URL = "https://data-api.binance.vision/api/v3/ticker/price"
KLINE_URL = "https://data-api.binance.vision/api/v3/klines"
HEARTBEAT = "mesa_live_state.json"
COMMIT_EVERY_S = 900          # commit periodico aunque no haya eventos
CANDLE_CHECK_S = 20           # sondeo de cierre de vela
GIT_GLOBS = ["torneo_*.json", "torneo_*.csv", "moonbet_*.json",
             "moonbet_*.csv", HEARTBEAT]

S = requests.Session()


def now_ms():
    return int(time.time() * 1000)


def iso(ms=None):
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime((ms or now_ms()) / 1000))


def fetch_marks():
    """Precios vivos de todo el mercado en UNA llamada (peso 2)."""
    r = S.get(TICKER_URL, timeout=10)
    r.raise_for_status()
    d = {x["symbol"]: float(x["price"]) for x in r.json()}
    return {s: d[s] for s in UNIVERSE if s in d}


def last_closed_open_time(symbol, interval):
    """open_time de la ultima vela CERRADA (sondeo ligero, limit=2)."""
    r = S.get(KLINE_URL, params={"symbol": symbol, "interval": interval,
                                 "limit": 2}, timeout=10)
    r.raise_for_status()
    ks = r.json()
    now = now_ms()
    closed = [k for k in ks if int(k[6]) < now]
    return int(closed[-1][0]) if closed else 0


# ---------- salidas en vivo (solo lo que la vela cerrada NO captura a tiempo) ----------
def live_exits_torneo(b, marks, ts_ms):
    """GEN1: stop tocado por precio vivo. Devuelve True si hubo evento."""
    ev = False
    for p in list(b["positions"]):
        px = marks.get(p["symbol"])
        if px is None:
            continue
        if px <= p["stop"]:
            b["positions"].remove(p)
            exit_px = p["stop"] * (1 - SLIP)
            pnl = torneo.log_trade(b, p, exit_px, "stop")
            evo.touch(b, ts_ms)
            print(f"[live:{b['variant']}] STOP {p['symbol']} @ {px:.4f} <= {p['stop']:.4f} pnl ${pnl:.2f}")
            ev = True
    return ev


def live_exits_moonbet(b, marks, ts_ms):
    """GEN2: bust (liquidacion 1/L) tocado por precio vivo."""
    ev = False
    for bet in list(b["bets"]):
        px = marks.get(bet["symbol"])
        if px is None:
            continue
        busted = (px <= bet["bust_px"]) if bet["dir"] > 0 else (px >= bet["bust_px"])
        if busted:
            b["bets"].remove(bet)
            moonbet.close_bet(b, bet, bet["bust_px"], "bust")
            evo.touch(b, ts_ms)
            print(f"[live:{b['variant']}] BUST {bet['symbol']} @ {px:.4f} vs bust {bet['bust_px']:.4f} wager ${bet['wager']:.2f}")
            ev = True
    return ev


def live_pass(mod, live_fn, marks, ts_ms):
    """Una pasada viva sobre un torneo: salidas + supervivencia. True si hubo eventos."""
    mod.merge_spawned()
    dirty = False
    for v in mod.VARIANTS:
        b = mod.load_book(v)
        if b["status"] != "alive":
            continue
        if live_fn(b, marks, ts_ms):
            dirty = True
        eq = mod.equity_of(b, marks)
        b["peak"] = max(b.get("peak", mod.START), eq)
        antes = b["status"]
        marc = b.get("cierre_evaluado")
        mod.check_survival(b, eq, ts_ms, marks)
        if b["status"] != antes or b.get("cierre_evaluado") != marc:
            dirty = True
        if dirty:
            mod.save_book(b)
    return dirty


def heartbeat(extra=None, poll=5):
    d = {"ts": iso(), "mode": "live", "poll_s": poll,
         "note": "loop continuo torneo GEN1/GEN2 en segundos (paper)"}
    if extra:
        d.update(extra)
    json.dump(d, open(HEARTBEAT, "w"), indent=2)


def git_commit_push(msg):
    files = []
    for pat in GIT_GLOBS:
        files += subprocess.run(["git", "ls-files", "-mo", "--exclude-standard", pat],
                                capture_output=True, text=True).stdout.split()
    files += [f for f in [HEARTBEAT] if os.path.exists(f)]
    if not files:
        return False
    subprocess.run(["git", "add"] + sorted(set(files)), capture_output=True)
    r = subprocess.run(["git", "commit", "-m", msg], capture_output=True, text=True)
    if r.returncode != 0:
        return False
    subprocess.run(["git", "pull", "--rebase", "--autostash"], capture_output=True)
    p = subprocess.run(["git", "push"], capture_output=True, text=True)
    if p.returncode != 0:
        subprocess.run(["git", "pull", "--rebase", "--autostash"], capture_output=True)
        p = subprocess.run(["git", "push"], capture_output=True, text=True)
    print(f"[live] git push {'OK' if p.returncode == 0 else 'FALLO'}: {msg}")
    return p.returncode == 0


def run(seconds, poll, shadow=False):
    fin = time.time() + seconds
    last_commit = 0.0
    last_candle_probe = 0.0
    seen_closed = {"4h": None, "1h": None}
    dirty = False
    errores = 0
    heartbeat({"shadow": shadow}, poll)
    print(f"[live] inicio {iso()} | {seconds}s poll={poll}s shadow={shadow}")
    if not shadow:
        # arranque: procesa cualquier vela cerrada mientras no habia vivo
        try:
            ts = now_ms()
            torneo.step(ts)
            moonbet.step(ts)
            dirty = True
        except Exception as e:
            print(f"[live] step inicial error: {e}")
    while time.time() < fin:
        t0 = time.time()
        ts_ms = now_ms()
        try:
            marks = fetch_marks()
            errores = 0
        except Exception as e:
            errores += 1
            espera = min(60, poll * (2 ** min(errores, 4)))
            print(f"[live] error ticker ({errores}): {e}; reintento en {espera}s")
            time.sleep(espera)
            continue
        try:
            if live_pass(torneo, live_exits_torneo, marks, ts_ms):
                dirty = True
            if live_pass(moonbet, live_exits_moonbet, marks, ts_ms):
                dirty = True
        except Exception as e:
            print(f"[live] live_pass error: {e}")
        # sondeo de cierre de vela
        if t0 - last_candle_probe >= CANDLE_CHECK_S:
            last_candle_probe = t0
            try:
                for tf in ("4h", "1h"):
                    ot = last_closed_open_time("BTCUSDT", tf)
                    if seen_closed[tf] is None:
                        seen_closed[tf] = ot
                    elif ot > seen_closed[tf]:
                        seen_closed[tf] = ot
                        print(f"[live] vela {tf} cerrada detectada ({iso(ot)}); paso completo")
                        ts2 = now_ms()
                        try:
                            torneo.step(ts2)
                            moonbet.step(ts2)
                            dirty = True
                        except Exception as e:
                            print(f"[live] step vela error: {e}")
                        break
            except Exception as e:
                print(f"[live] sondeo vela error: {e}")
        heartbeat(poll=poll)
        if not shadow and (dirty or t0 - last_commit >= COMMIT_EVERY_S):
            if git_commit_push(f"mesa live {iso()}"):
                last_commit = t0
                dirty = False
        dt = time.time() - t0
        time.sleep(max(1.0, poll - dt))
    heartbeat({"fin": "corrida terminada por tiempo"}, poll)
    if not shadow:
        git_commit_push(f"mesa live cierre corrida {iso()}")
    print(f"[live] fin {iso()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=20400)
    ap.add_argument("--poll", type=int, default=5)
    ap.add_argument("--shadow", action="store_true")
    a = ap.parse_args()
    run(a.seconds, a.poll, a.shadow)
