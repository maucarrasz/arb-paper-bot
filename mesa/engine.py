"""Bot 6 - Ejecucion simulada + estado compartido entre en vivo y backtest.
Fills: entrada al cierre de la vela de senal (live) o apertura de la siguiente
(backtest), siempre con slippage y comision taker. Salidas por stop/objetivo
tocados por la vela (si toca ambos en la misma vela, asumimos STOP primero:
criterio conservador), por ruptura de tendencia (EMA20<EMA50) o por tiempo (72h)."""
import time

from . import config, data


class Position:
    def __init__(self, symbol, entry_time, entry_px, qty, stop, target, reasons):
        self.symbol = symbol
        self.entry_time = entry_time
        self.entry_px = entry_px
        self.qty = qty
        self.stop = stop
        self.target = target
        self.reasons = reasons
        self.fees = entry_px * qty * config.FEE

    def to_dict(self):
        return dict(symbol=self.symbol, entry_time=self.entry_time,
                    entry_px=self.entry_px, qty=self.qty, stop=self.stop,
                    target=self.target, reasons=self.reasons, fees=self.fees)

    @staticmethod
    def from_dict(d):
        p = Position(d["symbol"], d["entry_time"], d["entry_px"], d["qty"],
                     d["stop"], d["target"], d.get("reasons", ""))
        p.fees = d.get("fees", p.fees)
        return p


def open_position(symbol, ref_px, qty, stop, target, ts, reasons):
    """Abre long simulado con slippage + fee."""
    fill = ref_px * (1 + config.SLIPPAGE)
    # recolocar stop/objetivo desde el fill real manteniendo distancias ATR
    d_stop = ref_px - stop
    d_tgt = target - ref_px
    p = Position(symbol, ts, fill, qty, fill - d_stop, fill + d_tgt, reasons)
    return p


def check_exit(pos, candle, closes_1h, ts_now):
    """Devuelve (exit_px, motivo) o (None, None). Conservador: stop antes que objetivo."""
    if candle["low"] <= pos.stop:
        return pos.stop * (1 - config.SLIPPAGE), "stop"
    if candle["high"] >= pos.target:
        return pos.target, "objetivo"
    if config.TREND_EXIT and len(closes_1h) >= config.EMA_SLOW + 1:
        e20, e50 = data.ema(closes_1h, config.EMA_FAST), data.ema(closes_1h, config.EMA_SLOW)
        if e20 and e50 and e20 < e50:
            return candle["close"] * (1 - config.SLIPPAGE), "ruptura de tendencia"
    if ts_now - pos.entry_time >= config.TIME_STOP_H * 3600 * 1000:
        return candle["close"] * (1 - config.SLIPPAGE), "tiempo (72h)"
    return None, None


def close_position(pos, exit_px, ts, motive):
    pos.fees += exit_px * pos.qty * config.FEE
    pnl = (exit_px - pos.entry_px) * pos.qty - pos.fees
    return {
        "symbol": pos.symbol, "open_ts": pos.entry_time, "close_ts": ts,
        "entry_px": round(pos.entry_px, 6), "exit_px": round(exit_px, 6),
        "qty": pos.qty, "notional": round(pos.entry_px * pos.qty, 2),
        "pnl_usd": round(pnl, 2),
        "pnl_pct": round(100 * pnl / (pos.entry_px * pos.qty), 2),
        "exit_reason": motive, "fees_usd": round(pos.fees, 2),
    }


class Book:
    """Libro paper: cash + posiciones. Equity inicial simulada."""

    def __init__(self, start_equity=config.START_EQUITY):
        self.cash = start_equity
        self.positions = []
        self.closed = []
        self.consec_losses = {}   # symbol -> racha
        self.cooldown_until = {}  # symbol -> ts ms
        self.day = None
        self.day_start_equity = start_equity
        self.day_pnl = 0.0

    def equity(self, marks=None):
        eq = self.cash
        for p in self.positions:
            px = (marks or {}).get(p.symbol, p.entry_px)
            eq += p.qty * px
        return eq

    def roll_day(self, ts_ms, marks=None):
        day = time.strftime("%Y-%m-%d", time.gmtime(ts_ms / 1000))
        if self.day != day:
            self.day = day
            self.day_start_equity = self.equity(marks)
            self.day_pnl = 0.0

    def daily_cap_hit(self):
        return self.day_pnl <= -config.DAILY_LOSS_CAP * self.day_start_equity

    def cooldown_active(self, symbol, ts_ms):
        return ts_ms < self.cooldown_until.get(symbol, 0)

    def register_close(self, trade, ts_ms):
        self.closed.append(trade)
        self.day_pnl += trade["pnl_usd"]
        if trade["pnl_usd"] < 0:
            n = self.consec_losses.get(trade["symbol"], 0) + 1
            self.consec_losses[trade["symbol"]] = n
            if n >= config.COOLDOWN_LOSSES:
                self.cooldown_until[trade["symbol"]] = ts_ms + config.COOLDOWN_H * 3600 * 1000
                self.consec_losses[trade["symbol"]] = 0
        else:
            self.consec_losses[trade["symbol"]] = 0
