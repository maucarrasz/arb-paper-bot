"""Reglas de muerte y relevo automatico del torneo evolutivo (23-oct-2026).

Cadena del usuario verificada en su canal (WhatsApp, 23-oct, hora Lima):
- 09:28 "7 dias de torneo, los que no apuestan mueren, si no es rentable muere"
- 09:35 "La muerte es inactividad por horas no al final"
- 09:36 "No se, modela a los mejores y maximo de perdida tmb" (delega criterio)
- 09:36 "Si uno muere lo reemplaza automaticamente nueva arquitectura de bots
         con aprendizaje de sus errores"

Benchmark aplicado (fuentes verificables):
- FTMO, Trading Objectives (ftmo.com/en/trading-objectives/): verificado
  23-oct. FTMO tiene 3 programas con reglas DISTINTAS: 1-Step (diario 3%,
  total 10% trailing end-of-day), 2-Step (diario 5%, total 10% ESTATICO),
  Instant (diario 3%, total 10% trailing intraday). Lo implementado es un
  HIBRIDO declarado, no verbatim de ningun programa: diario 3% (del 1-Step)
  + total 10% ESTATICO (del 2-Step; el trailing NO se implementa: subiria el
  piso cuando la equity crece).
- Poker, antes/blinds (pokerology.com/poker/strategy/ante/): la inaccion
  drena fichas cada orbita. Adaptacion propia: en vez de drenar equity, la
  inactividad por horas mata la arquitectura (pedido explicito del usuario).
- Ventana de inactividad por FAMILIA de senal = 2x gap p90 medido sobre 45
  dias de klines publicas de Binance (data-api.binance.vision, 12 pares,
  medido 23-oct-2026):
    mr RSI<30 4h   p90  8h -> 16h | mr RSI<25 4h  p90  8h -> 16h
    mr_down RSI>70 p90 24h -> 48h | mr_down RSI>75 p90  4h -> 12h (piso)
    momo 4h        p90 16h -> 32h | brk 4h        p90 20h -> 40h
    mr RSI<25 1h   p90 14h -> 28h | smart money: no medible -> 48h provisional
  Consecuencia medida: los gaps maximos observados (252-520h en RSI 4h)
  superan el torneo entero de 7 dias; bajo esta regla las arquitecturas de
  baja cadencia mueren y el relevo migra hacia senales mas frecuentes. Es la
  presion evolutiva pedida, no un ajuste a posteriori.

Relevo automatico (anti-overfitting): escalera FIJA de mutaciones segun
diagnostico estructural (inactividad -> mas cadencia; limite de perdida ->
mitad de riesgo). Con 1-3 apuestas no hay aprendizaje estadistico afirmable:
la hipotesis registrada es estructural, nunca "la senal era mala". Toda hija
pasa validacion de limites/riesgo antes de competir. Ventana fija: todas las
generaciones comparten el cierre del torneo (born de la generacion original
+ 7 dias); sin relevo en las ultimas 12h; tope 20 relevos por torneo.
"""
import calendar
import csv
import json
import os
import re
import time

TOTAL_LOSS_PCT = 0.10   # FTMO Maximum Loss (10% del capital inicial)
DAILY_LOSS_PCT = 0.03   # FTMO Maximum Daily Loss (3% del capital inicial)
MIN_HOURS_TO_SPAWN = 12
MAX_SPAWNED = 20

# Ventanas recalibradas 23-oct 15:00 UTC tras auditoria de main: la primera
# medicion contaba re-disparos del mismo par como oportunidad nueva y
# subestimaba los gaps. Version corregida: gap entre EVENTOS NUEVOS (par que
# dispara sin haber disparado en la vela inmediatamente anterior), agregados
# sobre el universo de 12 pares, 270 velas 4h (~45 dias) y 720 velas 1h
# (~30 dias), data-api.binance.vision, 23-oct-2026.
INACTIVITY_H = {
    "mr30_4h": 64,   # p90 dedup 32h
    "mr25_4h": 88,   # p90 dedup 44h
    "mrdn30_4h": 56, # p90 dedup 28h
    "mrdn25_4h": 16, # p90 dedup 8h
    "momo_4h": 32,   # p90 dedup 16h
    "brk_4h": 32,    # p90 dedup 16h
    "mr25_1h": 28,   # p90 dedup 14h
    "sm": 48,        # no medible en backfill: provisional
}

SPAWNED_JSON = {"moonbet": "moonbet_spawned.json", "torneo": "torneo_spawned.json"}
DEATHS_CSV = {"moonbet": "moonbet_deaths.csv", "torneo": "torneo_deaths.csv"}
BASE_GEN = {"moonbet": 2, "torneo": 1}


def ms_of(iso):
    return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%d %H:%M:%S")) * 1000


def iso_now():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def floor(start):
    """Piso de perdida total (10% del inicial)."""
    return round(start * (1 - TOTAL_LOSS_PCT), 2)


def daily_amount(start):
    """Monto de perdida diaria maxima (3% del inicial)."""
    return round(start * DAILY_LOSS_PCT, 2)


def born_ms(b):
    s = (b.get("born") or "2026-10-22 00:00:00 UTC")[:19]
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M:%S")) * 1000
    except Exception:
        return 0


def rollover_day(b, eq, ts_ms):
    """FTMO: el limite diario se recalcula con la equity de apertura del dia."""
    day = time.strftime("%Y-%m-%d", time.gmtime(ts_ms / 1000))
    do = b.get("day_open")
    if not do or do.get("date") != day:
        b["day_open"] = {"date": day, "eq": round(eq, 2)}


def touch(b, ms):
    """Registra actividad (apertura o cierre) en el libro."""
    b["last_active_ms"] = max(b.get("last_active_ms") or 0, ms)


def family_moonbet(p):
    sig, tf = p.get("sig"), p.get("tf", "4h")
    if sig == "mr_up":
        if tf == "1h":
            return "mr25_1h"
        return "mr25_4h" if p.get("rsi_in", 30) <= 25 else "mr30_4h"
    if sig == "mr_down":
        return "mrdn25_4h" if p.get("rsi_in", 30) <= 25 else "mrdn30_4h"
    if sig == "momo_up":
        return "momo_4h"
    if sig == "brk_up":
        return "brk_4h"
    return "sm"


def family_torneo(p):
    mode, tf = p.get("mode"), p.get("tf", "4h")
    if mode == "mr":
        if tf == "1h":
            return "mr25_1h"
        return "mr25_4h" if p.get("rsi_in", 30) <= 25 else "mr30_4h"
    return "sm"  # smfollow / msb11: cadencia no medible en backfill


def death_reason(b, eq, ts_ms, family, open_n, close_ms, start):
    """Devuelve (kind, texto) si el libro debe morir; None si sigue vivo.
    Orden: perdida total -> perdida diaria -> inactividad -> cierre 7d."""
    if eq < floor(start):
        return ("perdida_total",
                f"perdida total: equity ${eq:.2f} < ${floor(start):.0f} "
                f"(10% del capital inicial, modelo FTMO)")
    do = b.get("day_open")
    if do and eq < do["eq"] - daily_amount(start):
        return ("perdida_diaria",
                f"perdida diaria: equity ${eq:.2f} < apertura del dia "
                f"${do['eq']:.2f} - ${daily_amount(start):.0f} "
                f"(3% del inicial, modelo FTMO)")
    win = INACTIVITY_H.get(family, 48)
    last = b.get("last_active_ms") or born_ms(b)
    silent_h = (ts_ms - last) / 3600000.0
    if open_n == 0 and ts_ms - last > win * 3600000:
        return ("inactividad",
                f"inactividad: {silent_h:.1f}h sin actividad ni posiciones "
                f"abiertas (ventana familia {family}: {win}h = 2x p90 medido)")
    if ts_ms >= close_ms and not b.get("cierre_evaluado"):
        activity = b.get("bets_n", b.get("trades", 0)) + open_n
        if activity == 0:
            return ("cierre_torneo", "7d torneo: sin actividad al cierre")
        if eq <= start:
            return ("cierre_torneo",
                    f"7d torneo: no rentable al cierre (eq ${eq:.2f} <= ${start:.0f})")
    return None


def load_spawned(tourney):
    path = SPAWNED_JSON[tourney]
    if os.path.exists(path):
        try:
            return json.load(open(path))
        except Exception:
            return {}
    return {}


def save_spawned(tourney, d):
    json.dump(d, open(SPAWNED_JSON[tourney], "w"), indent=2)


def _cadence_ladder(tourney, q):
    """Escalera fija para muertes por inactividad: subir cadencia."""
    if tourney == "moonbet":
        sig = q.get("sig")
        if sig in ("mr_up", "mr_down"):
            if q.get("rsi_in", 30) < 30:
                old_ri, q["rsi_in"] = q.get("rsi_in", 30), 30
            elif q.get("rsi_in", 30) < 40:
                old_ri, q["rsi_in"] = q.get("rsi_in", 30), 40
            else:
                old_ri = None
            if old_ri is not None:
                if sig == "mr_up":
                    h = (f"umbral RSI mas frecuente (rsi_in {old_ri}->{q['rsi_in']}): "
                         f"entra en RSI<{old_ri} -> RSI<{q['rsi_in']}")
                else:
                    h = (f"umbral RSI mas frecuente (rsi_in {old_ri}->{q['rsi_in']}): "
                         f"entra en RSI>{100-old_ri} -> RSI>{100-q['rsi_in']}")
            elif q.get("tf") == "4h":
                q["tf"], q["rsi_in"] = "1h", 30
                h = "timeframe 4h->1h con RSI 30"
            else:
                q.update(sig="momo_up", lookback=3, thr=0.01)
                h = "MR agotada: cambio a momentum (p90 medido 16h)"
        elif sig == "momo_up":
            if q.get("thr", 0.015) > 0.01:
                q["thr"] = 0.01
                h = "umbral momentum 1.5%->1.0%"
            elif q.get("thr", 0.01) > 0.005:
                q["thr"] = 0.005
                h = "umbral momentum 1.0%->0.5%"
            else:
                q["lookback"] = 2
                h = "lookback momentum 3->2"
        elif sig == "brk_up":
            if q.get("lookback", 20) > 10:
                q["lookback"] = 10
                h = "breakout 20->10 cierres"
            else:
                q["lookback"] = 5
                h = "breakout 10->5 cierres"
        else:  # sm: cadencia no medible en backfill
            q.clear()
            q.update(sig="momo_up", lookback=3, thr=0.01, lev=10,
                     wager_pct=0.02, tf="4h")
            h = "smart-money no medible en backfill; cambio a momentum (p90 medido 16h)"
        return q, "mayor cadencia: " + h
    # torneo
    mode = q.get("mode")
    if mode == "mr":
        if q.get("rsi_in", 30) < 30:
            q["rsi_in"] = 30
            h = "RSI entrada 25->30"
        elif q.get("rsi_in", 30) < 40:
            q["rsi_in"] = 40
            h = "RSI entrada 30->40"
        elif q.get("tf") == "4h":
            q["tf"], q["rsi_in"], q["time_stop_h"] = "1h", 30, 24
            h = "timeframe 4h->1h"
        else:
            q["rsi_in"] = 40
            h = "RSI 40 en 1h (ultimo escalon)"
    elif mode == "smfollow":
        if q.get("need_dip"):
            q["need_dip"] = False
            h = "quita exigencia de dip RSI"
        elif q.get("tf") == "4h":
            q["tf"], q["time_stop_h"] = "1h", 24
            h = "timeframe 4h->1h"
        else:
            q["mode"], q["rsi_in"] = "mr", 30
            h = "smfollow agotado: cambio a mean reversion"
    else:  # msb11
        if q.get("tf") == "4h":
            q["tf"], q["time_stop_h"] = "1h", 48
            h = "timeframe 4h->1h"
        else:
            q["mode"], q["rsi_in"], q["time_stop_h"] = "mr", 30, 24
            h = "msb11 agotado: cambio a mean reversion"
    return q, "mayor cadencia: " + h


def mutate(tourney, p, kind):
    """Escalera fija de mutacion. Devuelve (params_hija, hipotesis)."""
    q = dict(p)
    if kind == "inactividad":
        return _cadence_ladder(tourney, q)
    # perdida_total / perdida_diaria: mitad de riesgo, misma senal
    if tourney == "moonbet":
        q["wager_pct"] = max(0.005, round(q.get("wager_pct", 0.02) / 2, 4))
        q["lev"] = max(3, int(q.get("lev", 10)) // 2)
        return q, (f"misma senal, riesgo a la mitad (wager {q['wager_pct']*100:.2f}% eq, "
                   f"x{q['lev']}): la muerte fue por limite de perdida, no por direccion")
    q["risk"] = max(0.002, round(q.get("risk", 0.0075) / 2, 4))
    return q, (f"misma senal, riesgo por trade a la mitad ({q['risk']*100:.2f}% eq): "
               "la muerte fue por limite de perdida, no por direccion")


def validate(tourney, q):
    """Pruebas limite/riesgo antes de que una hija compita. (ok, motivo)."""
    try:
        if tourney == "moonbet":
            sig = q.get("sig")
            if sig not in ("mr_up", "mr_down", "momo_up", "brk_up", "sm"):
                return False, f"sig desconocida: {sig}"
            if q.get("tf") not in ("1h", "4h"):
                return False, f"tf invalido: {q.get('tf')}"
            lev, wp = q.get("lev", 0), q.get("wager_pct", 0)
            if not 1 <= lev <= 100:
                return False, f"lev {lev} fuera de [1,100]"
            if not 0.005 <= wp <= 0.05:
                return False, f"wager_pct {wp} fuera de [0.005,0.05]"
            if wp * 500 < 1.0:
                return False, "wager minimo $1 no alcanzable con equity $500"
            # una sola apuesta perdida no puede romper el limite diario 3%
            cost1 = wp * 500 * 1.01  # wager + fee de apertura 1%
            if cost1 > daily_amount(500):
                return False, (f"wager {wp*100:.2f}% = ${cost1:.2f} con fee: una sola "
                               f"perdedora supera el limite diario 3% (${daily_amount(500):.0f})")
            if sig in ("mr_up", "mr_down"):
                ri, ro = q.get("rsi_in"), q.get("rsi_out", 55)
                if ri is None or not 5 <= ri <= 45:
                    return False, f"rsi_in {ri} fuera de [5,45]"
                if not ri < ro <= 90:
                    return False, f"rsi_out {ro} inconsistente con rsi_in {ri}"
            if sig == "momo_up":
                if q.get("lookback", 0) < 2:
                    return False, "lookback < 2"
                if not 0 < q.get("thr", 0) <= 0.05:
                    return False, f"thr {q.get('thr')} fuera de (0,0.05]"
            if sig == "brk_up" and q.get("lookback", 0) < 2:
                return False, "lookback < 2"
            return True, "ok"
        # torneo
        if q.get("mode") not in ("mr", "smfollow", "msb11"):
            return False, f"mode desconocido: {q.get('mode')}"
        if q.get("tf") not in ("1h", "4h"):
            return False, f"tf invalido: {q.get('tf')}"
        ri = q.get("rsi_in", 30)
        if not 5 <= ri <= 45:
            return False, f"rsi_in {ri} fuera de [5,45]"
        risk = q.get("risk", 0.0075)
        if not 0.002 <= risk <= 0.02:
            return False, f"risk {risk} fuera de [0.002,0.02]"
        if q.get("cap", 0.3) > 0.5:
            return False, f"cap {q.get('cap')} > 0.5"
        if q.get("stop_atr", 2.0) <= 0:
            return False, "stop_atr <= 0"
        if q.get("time_stop_h", 96) < 1:
            return False, "time_stop_h < 1"
        return True, "ok"
    except Exception as e:
        return False, f"validacion error: {e}"


def _next_base(tourney, taken):
    if tourney == "moonbet":
        n = 10
        for k in taken:
            m = re.match(r"U(\d+)", k)
            if m:
                n = max(n, int(m.group(1)))
        return f"U{n + 1}"
    for ch in "KLMNOPQRSTUVWXYZ":
        if not any(k.startswith(ch + "_") for k in taken):
            return ch
    return None


def spawn_replacement(tourney, b, parent_params, kind, ts_ms, close_ms, taken_names):
    """Engendra UNA hija con hipotesis registrada. None si no procede;
    dict con 'blocked' si quedo bloqueado; dict con 'name' si nacio."""
    if kind == "cierre_torneo":
        return None
    if ts_ms >= close_ms - MIN_HOURS_TO_SPAWN * 3600000:
        return {"blocked": f"<{MIN_HOURS_TO_SPAWN}h al cierre del torneo"}
    spawned = load_spawned(tourney)
    if len(spawned) >= MAX_SPAWNED:
        return {"blocked": f"tope de {MAX_SPAWNED} relevos alcanzado"}
    newp, hypothesis = mutate(tourney, parent_params, kind)
    ok, why = validate(tourney, newp)
    if not ok:
        return {"blocked": f"validacion: {why}"}
    taken = set(taken_names) | set(spawned)
    base = _next_base(tourney, taken)
    if base is None:
        return {"blocked": "sin nombres libres"}
    gen = b.get("generation", BASE_GEN[tourney]) + 1
    tag = newp.get("sig") or newp.get("mode")
    name = f"{base}_{tag}_g{gen}"
    i = 2
    while name in taken:
        name = f"{base}_{tag}_g{gen}_{i}"
        i += 1
    entry = {"params": newp, "parent": b["variant"], "generation": gen,
             "hypothesis": hypothesis, "born": iso_now()}
    spawned[name] = entry
    save_spawned(tourney, spawned)
    return {"name": name, **entry}


def log_death(tourney, b, kind, rep, eq_final=None):
    """Apéndice al log de muertes (CSV, matchea el glob de commit torneo_*.csv /
    moonbet_*.csv del workflow)."""
    path = DEATHS_CSV[tourney]
    new = not os.path.exists(path)
    stats = {k: b.get(k) for k in ("trades", "bets_n", "wins", "losses", "busts", "peak")}
    stats["eq_final"] = round(eq_final, 2) if eq_final is not None else None
    rep = rep or {}
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "variant", "generation", "kind", "reason", "stats",
                        "rep_name", "rep_hypothesis", "rep_params", "rep_blocked"])
        w.writerow([iso_now(), b["variant"], b.get("generation", BASE_GEN[tourney]),
                    kind, b.get("reason"), json.dumps(stats, ensure_ascii=False),
                    rep.get("name"), rep.get("hypothesis"),
                    json.dumps(rep.get("params"), ensure_ascii=False) if rep.get("params") else "",
                    rep.get("blocked", "")])


def lineage_report(roots, spawned, status_map):
    """Capital virtual inyectado y PnL por linaje (raiz + relevos).

    Cada relevo repone $500 virtuales: una ganadora reseteada a $500 NO es
    comparable con un padre que perdio. Aqui se contabiliza el coste total
    por linaje y su PnL agregado.
    """
    parent_of = {n: e.get("parent") for n, e in spawned.items()}

    def root_of(n):
        seen = set()
        while n in parent_of and parent_of[n] and n not in seen:
            seen.add(n)
            n = parent_of[n]
        return n

    out = {}
    for name in list(roots) + list(spawned):
        st = (status_map.get(name) or {}).get("status")
        if st == "aislada":
            continue  # hijas invalidadas: no suman capital ni retorno competitivo
        r = root_of(name)
        d = out.setdefault(r, {"miembros": [], "capital_inyectado": 0.0,
                               "equity_total": 0.0})
        d["miembros"].append(name)
        d["capital_inyectado"] += 500.0
        eq = (status_map.get(name) or {}).get("equity")
        if eq is not None:
            d["equity_total"] += eq
    for r, d in out.items():
        d["capital_repuesto"] = d["capital_inyectado"] - 500.0
        d["equity_total"] = round(d["equity_total"], 2)
        d["pnl"] = round(d["equity_total"] - d["capital_inyectado"], 2)
        d["capital_inyectado"] = round(d["capital_inyectado"], 2)
    return out


def incident_report(status_map):
    """Contabilidad APARTE del incidente: variantes aisladas, su capital
    repuesto y su PnL fuera del resultado competitivo."""
    out = {}
    for n, sm in status_map.items():
        if sm.get("status") == "aislada":
            eq = sm.get("equity")
            out[n] = {"equity": eq,
                      "pnl_aislado": round(eq - 500.0, 2) if eq is not None else None,
                      "capital_repuesto": 500.0}
    return out
