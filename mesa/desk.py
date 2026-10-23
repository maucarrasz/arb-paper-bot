"""Orquestador en vivo de la mesa. Corre 1 vez por hora en GitHub Actions.
Lee estado de mesa_state.json (commiteado en el repo), gestiona posiciones,
escanea senales, pasa la mesa completa (scanner -> noticias -> analista ->
esceptico -> riesgo -> ejecucion simulada) y commitea CSVs + estado.
Uso: python -m mesa.desk"""
import csv
import json
import os
import time

from . import agents, config, data, engine, moonbet, news, reto100, torneo

STATE = "mesa_state.json"
SIGNALS_CSV = "mesa_signals.csv"
TRADES_CSV = "mesa_trades.csv"
EQUITY_CSV = "mesa_equity.csv"


def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def load_state():
    book = engine.Book()
    if not os.path.exists(STATE):
        return book
    d = json.load(open(STATE))
    book.cash = d["cash"]
    book.guard_until = d.get("guard_until", 0)
    book.peak_equity = d.get("peak_equity")
    book.positions = [engine.Position.from_dict(p) for p in d["positions"]]
    book.consec_losses = d.get("consec_losses", {})
    book.cooldown_until = d.get("cooldown_until", {})
    book.day = d.get("day")
    book.day_start_equity = d.get("day_start_equity", config.START_EQUITY)
    book.day_pnl = d.get("day_pnl", 0.0)
    return book


def save_state(book):
    json.dump({
        "cash": book.cash,
        "positions": [p.to_dict() for p in book.positions],
        "consec_losses": book.consec_losses,
        "cooldown_until": book.cooldown_until,
        "day": book.day,
        "day_start_equity": book.day_start_equity,
        "day_pnl": book.day_pnl,
        "guard_until": getattr(book, "guard_until", 0),
        "peak_equity": getattr(book, "peak_equity", None),
        "mode": config.MODE,
        "updated": now_iso(),
    }, open(STATE, "w"), indent=2)


def append_csv(path, fieldnames, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if new:
            w.writeheader()
        w.writerow(row)


def log_signal(ts, sig, final):
    append_csv(SIGNALS_CSV,
               ["ts", "symbol", "vol_ratio", "ret_3h_pct", "rsi", "atr_pct",
                "trend_1h", "trend_4h", "news_label", "news_score",
                "entry", "stop", "target", "rr", "skeptic", "final", "reasons"],
               {"ts": ts, "symbol": sig.symbol, "vol_ratio": round(sig.vol_ratio, 2),
                "ret_3h_pct": round(sig.ret_3h * 100, 2), "rsi": round(sig.rsi, 1),
                "atr_pct": round(sig.atr_pct * 100, 2), "trend_1h": sig.trend_1h,
                "trend_4h": sig.trend_4h, "news_label": sig.news_label,
                "news_score": sig.news_score, "entry": round(sig.entry, 4),
                "stop": round(sig.stop, 4), "target": round(sig.target, 4),
                "rr": round(sig.rr, 2), "skeptic": sig.skeptic_decision,
                "final": final,
                "reasons": " | ".join(sig.skeptic_reasons + sig.risk_reasons)[:300]})


def log_trade(trade):
    f = dict(trade)
    f["open_ts"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(f["open_ts"] / 1000))
    f["close_ts"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime(f["close_ts"] / 1000))
    append_csv(TRADES_CSV,
               ["symbol", "open_ts", "close_ts", "entry_px", "exit_px", "qty",
                "notional", "pnl_usd", "pnl_pct", "exit_reason", "fees_usd"], f)


def run():
    ts_ms = int(time.time() * 1000)
    book = load_state()
    klines, klines4 = {}, {}
    marks = {}
    for sym in config.UNIVERSE:
        try:
            ks = data.fetch_klines(sym)
            closed = [c for c in ks if c["close_time"] < ts_ms]
            klines[sym] = closed
            ks4 = data.fetch_klines(sym, "4h", 260)
            klines4[sym] = [c for c in ks4 if c["close_time"] < ts_ms]
            marks[sym] = closed[-1]["close"]
        except Exception as e:
            print(f"[desk] {sym}: error de datos: {e}")
    book.roll_day(ts_ms, marks)

    # 1) Ejecucion: gestionar posiciones abiertas con la ultima vela cerrada
    for pos in list(book.positions):
        cs = klines.get(pos.symbol)
        if not cs:
            continue
        candle = cs[-1]
        closes = [c["close"] for c in cs]
        exit_px, motive = engine.check_exit(pos, candle, closes, candle["close_time"])
        if exit_px is not None:
            trade = engine.close_position(pos, exit_px, candle["close_time"], motive)
            book.cash += pos.qty * exit_px - (exit_px * pos.qty * config.FEE)
            book.positions.remove(pos)
            book.register_close(trade, candle["close_time"])
            log_trade(trade)
            print(f"[desk] CIERRE {trade['symbol']} ({motive}) pnl ${trade['pnl_usd']}")

    # 2) Noticias (bot 2): una sola descarga para todos los simbolos
    headlines = news.fetch_headlines()
    print(f"[desk] titulares RSS: {len(headlines)}")

    # 3) Scanner -> analista -> esceptico -> riesgo -> ejecucion
    for sym in config.UNIVERSE:
        cs = klines.get(sym)
        if not cs or len(cs) < 60:
            continue
        sig = agents.scan(sym, cs)
        if not sig.candidate:
            continue
        sig = agents.analyze(sig, cs, klines4[sym])
        guard = book.guard_active(ts_ms, marks)
        sig = agents.skeptical(sig, news.context(sym, headlines),
                               [p.symbol for p in book.positions],
                               book.cooldown_active(sym, ts_ms) or guard,
                               book.daily_cap_hit())
        if sig.skeptic_decision == "PASAR":
            log_signal(now_iso(), sig, "VETO")
            print(f"[desk] {sym} VETO: {'; '.join(sig.skeptic_reasons)[:120]}")
            continue
        sig = agents.size_up(sig, book.equity(marks), len(book.positions))
        if not sig.risk_ok:
            log_signal(now_iso(), sig, "VETO_RIESGO")
            print(f"[desk] {sym} VETO_RIESGO: {'; '.join(sig.risk_reasons)[:120]}")
            continue
        candle = cs[-1]
        pos = engine.open_position(sym, candle["close"], sig.qty, sig.stop,
                                   sig.target, candle["close_time"],
                                   "; ".join(sig.analyst_reasons))
        book.cash -= pos.qty * pos.entry_px + (pos.entry_px * pos.qty * config.FEE)
        book.positions.append(pos)
        log_signal(now_iso(), sig, "ABRIR")
        print(f"[desk] ABRIR {sym} @ {pos.entry_px:.2f} notional ${pos.qty*pos.entry_px:.2f}")

    # 4) Equity + estado
    eq = book.equity(marks)
    append_csv(EQUITY_CSV, ["ts", "equity", "cash", "open_positions", "day_pnl"],
               {"ts": now_iso(), "equity": round(eq, 2), "cash": round(book.cash, 2),
                "open_positions": len(book.positions), "day_pnl": round(book.day_pnl, 2)})
    save_state(book)
    print(f"[desk] equity ${eq:.2f} | cash ${book.cash:.2f} | abiertas {len(book.positions)} | pnl dia ${book.day_pnl:.2f}")

    # 5) Reto $100 -> $1000 (libro paper independiente, superviviente MR-4h)
    try:
        reto100.step(ts_ms)
    except Exception as e:
        print(f"[desk] reto100 error: {e}")
    # 6) Torneo evolutivo de variantes (paper USD 500 c/u, meta USD 1000)
    try:
        torneo.step(ts_ms)
    except Exception as e:
        print(f"[desk] torneo error: {e}")
    # 7) Torneo GEN2: apuestas up/down apalancadas estilo Moon (paper)
    try:
        moonbet.step(ts_ms)
    except Exception as e:
        print(f"[desk] moonbet error: {e}")
    # Escenar archivos del reto/torneo para que el commit del workflow los incluya
    import glob
    import subprocess
    patrones = ("reto100_*.json", "reto100_*.csv", "torneo_*.json",
                "torneo_*.csv", "smartmoney_*.csv",
                "moonbet_*.json", "moonbet_*.csv")
    for pat in patrones:
        for f in glob.glob(pat):
            subprocess.run(["git", "add", f], capture_output=True)


if __name__ == "__main__":
    run()
