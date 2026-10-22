"""Backtest honesto de la mesa sobre velas historicas 1h de Binance.
Sin noticias (no hay historico RSS gratis): el veto de noticias no aplica aqui;
se documenta como diferencia vs. vivo. Uso:
  python -m mesa.backtest --days 180
"""
import argparse
import json
import time

from . import agents, config, data, engine


def run(days=180, verbose=False, end_ms=None):
    hist = {}
    hist4 = {}
    for sym in config.UNIVERSE:
        hist[sym] = data.fetch_history(sym, "1h", days, end_ms=end_ms)
        hist4[sym] = data.fetch_history(sym, "4h", days + 20, end_ms=end_ms)
        print(f"{sym}: {len(hist[sym])} velas 1h + {len(hist4[sym])} velas 4h descargadas", flush=True)
    # linea de tiempo comun e indices moviles (ventana creciente por simbolo)
    times = [c["open_time"] for c in hist[config.UNIVERSE[0]]]
    idx = {s: 0 for s in config.UNIVERSE}
    idx4 = {s: 0 for s in config.UNIVERSE}
    win4 = {}
    book = engine.Book()
    signals_log = []
    warmup = 60  # velas para indicadores
    # cache de cierres y resample por simbolo, incremental por posicion
    closes_cache = {s: [c["close"] for c in hist[s]] for s in config.UNIVERSE}

    for i, t in enumerate(times):
        if i < warmup:
            continue
        win = {}
        marks = {}
        for sym in config.UNIVERSE:
            j = idx[sym]
            h = hist[sym]
            while j + 1 < len(h) and h[j + 1]["open_time"] <= t:
                j += 1
            idx[sym] = j
            if h[j]["open_time"] > t:
                continue
            win[sym] = h[:j + 1]
            marks[sym] = h[j]["close"]
            h4 = hist4[sym]
            j4 = idx4[sym]
            while j4 + 1 < len(h4) and h4[j4 + 1]["close_time"] <= t:
                j4 += 1
            idx4[sym] = j4
            win4[sym] = h4[:j4 + 1]
        book.roll_day(t, marks)

        # 1) gestionar posiciones abiertas con la vela que cierra en t
        for pos in list(book.positions):
            if pos.symbol not in win:
                continue
            cs = win[pos.symbol]
            candle = cs[-1]
            closes = closes_cache[pos.symbol][:len(cs)]
            exit_px, motive = engine.check_exit(pos, candle, closes, candle["close_time"])
            if exit_px is not None:
                trade = engine.close_position(pos, exit_px, candle["close_time"], motive)
                book.cash += pos.qty * exit_px - (exit_px * pos.qty * config.FEE)
                book.positions.remove(pos)
                book.register_close(trade, candle["close_time"])
                if verbose:
                    print(f"CIERRE {trade['symbol']} {motive} pnl ${trade['pnl_usd']}")

        # 2) escanear nuevas senales (una por hora maximo, como el cron en vivo)
        news_ctx = {"score": 0, "label": "no aplica en backtest", "headlines": []}
        for sym in config.UNIVERSE:
            if sym not in win:
                continue
            cs = win[sym]
            if len(cs) < warmup:
                continue
            sig = agents.scan(sym, cs)
            if not sig.candidate:
                continue
            c4 = win4.get(sym, [])
            if len(c4) < 55:
                continue
            sig = agents.analyze(sig, cs, c4)
            guard = book.guard_active(t, marks)
            sig = agents.skeptical(
                sig, news_ctx, [p.symbol for p in book.positions],
                book.cooldown_active(sym, t) or guard, book.daily_cap_hit())
            if sig.skeptic_decision == "PASAR":
                signals_log.append(_sig_row(t, sig, "VETO"))
                continue
            sig = agents.size_up(sig, book.equity(marks), len(book.positions))
            if not sig.risk_ok:
                signals_log.append(_sig_row(t, sig, "VETO_RIESGO"))
                continue
            # abrir al cierre de la vela de senal (equivalente conservador al siguiente open en 1h)
            candle = cs[-1]
            pos = engine.open_position(sym, candle["close"], sig.qty, sig.stop,
                                       sig.target, candle["close_time"],
                                       "; ".join(sig.analyst_reasons))
            book.cash -= pos.qty * pos.entry_px + (pos.entry_px * pos.qty * config.FEE)
            book.positions.append(pos)
            signals_log.append(_sig_row(t, sig, "ABRIR"))
            if verbose:
                print(f"ABRIR {sym} @ {pos.entry_px:.2f} qty {pos.qty:.5f}")

    # cerrar lo abierto al final al ultimo precio
    for pos in list(book.positions):
        cs = hist[pos.symbol]
        exit_px = cs[-1]["close"] * (1 - config.SLIPPAGE)
        trade = engine.close_position(pos, exit_px, cs[-1]["close_time"], "fin de backtest")
        book.cash += pos.qty * exit_px - (exit_px * pos.qty * config.FEE)
        book.positions.remove(pos)
        book.register_close(trade, cs[-1]["close_time"])

    final_eq = book.equity()
    stats = compute_stats(book, final_eq, hist, days)
    return stats, signals_log, book.closed


def _sig_row(t, sig, final):
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M", time.gmtime(t / 1000)),
        "symbol": sig.symbol, "vol_ratio": round(sig.vol_ratio, 2),
        "ret_3h": round(sig.ret_3h * 100, 2), "rsi": round(sig.rsi, 1),
        "atr_pct": round(sig.atr_pct * 100, 2), "trend_1h": sig.trend_1h,
        "trend_4h": sig.trend_4h, "news": sig.news_label,
        "skeptic": sig.skeptic_decision,
        "reasons": " | ".join(sig.skeptic_reasons + sig.risk_reasons)[:300],
        "final": final,
    }


def compute_stats(book, final_eq, hist, days):
    tr = book.closed
    wins = [t for t in tr if t["pnl_usd"] > 0]
    losses = [t for t in tr if t["pnl_usd"] <= 0]
    # drawdown sobre equity realizada
    eq = config.START_EQUITY
    peak, max_dd = eq, 0.0
    for t in sorted(tr, key=lambda x: x["close_ts"]):
        eq += t["pnl_usd"]
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)
    first = hist[config.UNIVERSE[0]][0]
    last = hist[config.UNIVERSE[0]][-1]
    bh = {s: round(100 * (hist[s][-1]["close"] / hist[s][0]["open"] - 1), 2) for s in hist}
    return {
        "dias": days,
        "periodo": f"{time.strftime('%Y-%m-%d', time.gmtime(first['open_time']/1000))} a {time.strftime('%Y-%m-%d', time.gmtime(last['close_time']/1000))}",
        "equity_inicial": config.START_EQUITY,
        "equity_final": round(final_eq, 2),
        "retorno_pct": round(100 * (final_eq / config.START_EQUITY - 1), 2),
        "buy_hold_pct": bh,
        "trades": len(tr),
        "ganados": len(wins),
        "perdidos": len(losses),
        "winrate_pct": round(100 * len(wins) / len(tr), 1) if tr else 0,
        "avg_win_usd": round(sum(t["pnl_usd"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss_usd": round(sum(t["pnl_usd"] for t in losses) / len(losses), 2) if losses else 0,
        "max_drawdown_pct": round(100 * max_dd, 2),
        "fees_total_usd": round(sum(t["fees_usd"] for t in tr), 2),
        "senales_abiertas": sum(1 for t in tr),
        "salidas": {},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--mode", choices=["v1", "v2"], default=config.MODE)
    ap.add_argument("--to", default=None, help="fecha fin YYYY-MM-DD (UTC); defecto: ahora")
    ap.add_argument("--out", default="mesa_backtest_results.json")
    ap.add_argument("--trades-out", default="mesa_backtest_trades.csv")
    args = ap.parse_args()
    config.MODE = args.mode
    end_ms = None
    if args.to:
        import datetime
        end_ms = int(datetime.datetime.strptime(args.to, "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc).timestamp() * 1000)
    stats, signals, trades = run(days=args.days, end_ms=end_ms)
    stats["mode"] = args.mode
    stats["salidas"] = {}
    for t in trades:
        stats["salidas"][t["exit_reason"]] = stats["salidas"].get(t["exit_reason"], 0) + 1
    stats["nota"] = ("Backtest sin veto de noticias (sin historico RSS gratis). "
                     "Fills conservadores: stop antes que objetivo si la vela toca ambos, "
                     "slippage 0.05% + fee 0.10% por lado.")
    with open(args.out, "w") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    import csv
    if trades:
        with open(args.trades_out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
            w.writeheader()
            w.writerows(trades)
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
