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
        self.atr = (entry_px - stop) / config.STOP_ATR   # ATR al entrar (Chandelier)
        self.max_high = entry_px                          # max high desde entrada

    def to_dict(self):
        return dict(symbol=self.symbol, entry_time=self.entry_time,
                    entry_px=self.entry_px, qty=self.qty, stop=self.stop,
                    target=self.target, reasons=self.reasons, fees=self.fees,
                    atr=self.atr, max_high=self.max_high)

    @staticmethod
    def from_dict(d):
        p = Position(d["symbol"], d["entry_time"], d["entry_px"], d["qty"],
                     d["stop"], d["target"], d.get("reasons", ""))
        p.fees = d.get("fees", p.fees)
        p.atr = d.get("atr", p.atr)
        p.max_high = d.get("max_high", p.entry_px)
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
    """Devuelve (exit_px, motivo) o (None, None). Conservador: stop antes que objetivo.
    v2: stop trailing Chandelier (max_high - 2.5*ATR, nunca baja), sin objetivo fijo."""
    v2 = getattr(config, "MODE", "v1") == "v2"
    if v2:
        pos.max_high = max(pos.max_high, candle["high"])
        trailing = max(pos.stop, pos.max_high - config.CHANDELIER_ATR * pos.atr)
        if candle["low"] <= trailing:
            motive = "stop" if trailing <= pos.stop + 1e-12 else "chandelier"
            return trailing * (1 - config.SLIPPAGE), motive
    else:
        if candle["low"] <= pos.stop:
            return pos.stop * (1 - config.SLIPPAGE), "stop"
        if candle["high"] >= pos.target:
            return pos.target, "objetivo"
    if config.TREND_EXIT and len(closes_1h) >= config.EMA_SLOW + 1:
        e20, e50 = data.ema(closes_1h, config.EMA_FAST), data.ema(closes_1h, config.EMA_SLOW)
        if e20 and e50 and e20 < e50:
            return candle["close"] * (1 - config.SLIPPAGE), "ruptura de tendencia"
    tstop = config.TIME_STOP_H_V2 if v2 else config.TIME_STOP_H
    if ts_now - pos.entry_time >= tstop * 3600 * 1000:
        return candle["close"] * (1 - config.SLIPPAGE), f"tiempo ({tstop}h)"
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
        if getattr(config, "MODE", "v1") == "v2":
            # CooldownPeriod: 2h sin re-entrar al simbolo tras cualquier cierre
            self.cooldown_until[trade["symbol"]] = max(
                self.cooldown_until.get(trade["symbol"], 0),
                ts_ms + config.COOLDOWN_AFTER_CLOSE_H * 3600 * 1000)
            # StoplossGuard global: >=N stops en 24h = pausa global
            h24 = ts_ms - config.SL_GUARD_LOOKBACK_H * 3600 * 1000
            stops = [t for t in self.closed
                     if t["close_ts"] >= h24 and t["exit_reason"].startswith("stop")]
            if len(stops) >= config.SL_GUARD_TRADES:
                self.guard_until = max(getattr(self, "guard_until", 0),
                                       ts_ms + config.SL_GUARD_STOP_H * 3600 * 1000)
            # LowProfitPairs: ultimas N ops del simbolo sumando < X% = bloqueo 72h
            ts = [t for t in self.closed if t["symbol"] == trade["symbol"]][-config.LOWPROFIT_TRADES:]
            if len(ts) >= config.LOWPROFIT_TRADES and sum(t["pnl_pct"] for t in ts) < config.LOWPROFIT_PCT:
                self.cooldown_until[trade["symbol"]] = max(
                    self.cooldown_until.get(trade["symbol"], 0),
                    ts_ms + config.LOWPROFIT_STOP_H * 3600 * 1000)

    def guard_active(self, ts_ms, marks=None):
        """MaxDrawdown + StoplossGuard globales (v2)."""
        if getattr(config, "MODE", "v1") != "v2":
            return False
        if ts_ms < getattr(self, "guard_until", 0):
            return True
        peak = getattr(self, "peak_equity", None)
        eq = self.equity(marks)
        if peak is None or eq > peak:
            self.peak_equity = eq
            peak = eq
        if peak > 0 and (peak - eq) / peak > config.DD_MAX:
            self.guard_until = ts_ms + config.DD_STOP_H * 3600 * 1000
            self.peak_equity = eq  # reinicia el pico tras disparar la pausa
            return True
        return False
