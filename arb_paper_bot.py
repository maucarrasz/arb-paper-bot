#!/usr/bin/env python3
"""Paper-trading BTC arbitrage bot v2. NO real orders, NO API keys, public data only.
WebSocket feeds (como los brokers: stream continuo, reconexion con backoff, fallback REST,
health por exchange, timestamps para no comparar datos viejos).
Run: python arb_paper_bot.py --seconds 300 --interval 2
"""
import argparse, csv, json, os, threading, time
import requests
from websockets.sync.client import connect

FEES = {"binance": 0.0010, "okx": 0.0010, "kucoin": 0.0010, "kraken": 0.0040, "coinbase": 0.0060}
SAFETY_MARGIN = 0.0005
SLIPPAGE = 0.0002
DELAY_S = 0.5
START_BALANCE = 1000.0
STALE_S = 20          # dato mas viejo que esto = exchange marcado caido para esa lectura
REST_FALLBACK_S = 60  # WS en silencio mas que esto = fallback REST toma el relevo
S = requests.Session()
STOP = threading.Event()
lock = threading.Lock()
PX = {}  # name -> dict(bid,ask,bq,aq,ts_exch,recv,msgs,recon,src,last_err)

def st_of(name):
    return PX.setdefault(name, dict(bid=None, ask=None, bq=0.0, aq=0.0, ts_exch=None,
                                    recv=0.0, msgs=0, recon=0, src="-", last_err=""))

def upd(name, bid, ask, bq, aq, ts_exch=None, src="ws"):
    with lock:
        st = st_of(name)
        st.update(bid=bid, ask=ask, bq=bq, aq=aq, ts_exch=ts_exch, recv=time.time(), src=src)
        st["msgs"] += 1

def mark_err(name, err):
    with lock:
        st = st_of(name); st["recon"] += 1; st["last_err"] = err

HW = None  # health csv writer
def hevent(name, event, detail=""):
    try:
        HW.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), name, event, str(detail)[:120]]); HF.flush()
    except Exception:
        pass

# ---------- REST (fallback, y usado por KuCoin para pedir su token WS) ----------
def jget(url):
    return S.get(url, timeout=5).json()

REST = {
 "binance": lambda: (lambda d: (float(d["bidPrice"]), float(d["askPrice"]), float(d["bidQty"]), float(d["askQty"])))(
     jget("https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol=BTCUSDT")),
 "okx": lambda: (lambda d: (float(d["bidPx"]), float(d["askPx"]), float(d["bidSz"]), float(d["askSz"])))(
     jget("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT")["data"][0]),
 "kucoin": lambda: (lambda d: (float(d["bestBid"]), float(d["bestAsk"]), float(d["bestBidSize"]), float(d["bestAskSize"])))(
     jget("https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=BTC-USDT")["data"]),
 "kraken": lambda: (lambda d: (float(d["b"][0]), float(d["a"][0]), float(d["b"][1]), float(d["a"][1])))(
     next(iter(jget("https://api.kraken.com/0/public/Ticker?pair=XBTUSD")["result"].values()))),
 "coinbase": lambda: (lambda d: (float(d["bid"]), float(d["ask"]), 1.0, 1.0))(
     jget("https://api.exchange.coinbase.com/products/BTC-USD/ticker")),
}

# ---------- WebSocket feeds (endpoints verificados en docs oficiales, 20-oct-2026) ----------
def kucoin_bullet():
    d = S.post("https://api.kucoin.com/api/v1/bullet-public", timeout=8).json()["data"]
    srv = d["instanceServers"][0]
    return f'{srv["endpoint"]}?token={d["token"]}', max(5, int(srv.get("pingInterval", 18000)) / 2000)

def p_binance(m):
    return float(m["b"]), float(m["a"]), float(m["B"]), float(m["A"]), None
def p_okx(m):
    if "data" not in m: return None
    d = m["data"][0]
    return float(d["bidPx"]), float(d["askPx"]), float(d["bidSz"]), float(d["askSz"]), float(d["ts"]) / 1000
def p_kucoin(m):
    if m.get("type") != "message": return None
    d = m["data"]
    return float(d["bestBid"]), float(d["bestAsk"]), float(d["bestBidSize"]), float(d["bestAskSize"]), None
def p_kraken(m):
    if m.get("channel") != "ticker" or "data" not in m: return None
    d = m["data"][0]
    ts = None
    if d.get("timestamp"):
        try: ts = time.mktime(time.strptime(d["timestamp"][:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception: ts = None
    return float(d["bid"]), float(d["ask"]), float(d.get("bid_qty", 1)), float(d.get("ask_qty", 1)), ts
def p_coinbase(m):
    if m.get("type") != "ticker": return None
    return float(m["best_bid"]), float(m["best_ask"]), float(m.get("best_bid_size") or 1), float(m.get("best_ask_size") or 1), None

WS = {
 "binance": dict(url=lambda: ("wss://data-stream.binance.vision:443/ws/btcusdt@bookTicker", 60), sub=None, parse=p_binance, appping=None),
 "okx": dict(url=lambda: ("wss://ws.okx.com:8443/ws/v5/public", 15), sub=json.dumps({"op": "subscribe", "args": [{"channel": "tickers", "instId": "BTC-USDT"}]}), parse=p_okx, appping="ping"),
 "kucoin": dict(url=kucoin_bullet, sub=json.dumps({"id": "1", "type": "subscribe", "topic": "/market/ticker:BTC-USDT", "privateChannel": False, "response": True}), parse=p_kucoin, appping="kucoin"),
 "kraken": dict(url=lambda: ("wss://ws.kraken.com/v2", 60), sub=json.dumps({"method": "subscribe", "params": {"channel": "ticker", "symbol": ["BTC/USD"]}}), parse=p_kraken, appping=None),
 "coinbase": dict(url=lambda: ("wss://ws-feed.exchange.coinbase.com", 60), sub=json.dumps({"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]}), parse=p_coinbase, appping=None),
}

def ws_worker(name, cfg):
    backoff = 2
    n = 0
    while not STOP.is_set():
        try:
            url, recv_to = cfg["url"]()
            with connect(url, ping_interval=20, ping_timeout=20, open_timeout=10, close_timeout=3, max_size=2**20) as ws:
                if cfg["sub"]: ws.send(cfg["sub"])
                hevent(name, "ws_connected", url.split("?")[0]); backoff = 2; n = 0
                while not STOP.is_set():
                    try:
                        raw = ws.recv(timeout=recv_to)
                    except TimeoutError:
                        if cfg["appping"] == "ping": ws.send("ping")
                        elif cfg["appping"] == "kucoin": ws.send(json.dumps({"id": str(int(time.time())), "type": "ping"}))
                        continue
                    try:
                        m = json.loads(raw)
                    except Exception:
                        continue
                    r = cfg["parse"](m)
                    if r:
                        bid, ask, bq, aq, ts_exch = r
                        upd(name, bid, ask, bq, aq, ts_exch, src="ws")
        except Exception as e:
            if STOP.is_set(): break
            mark_err(name, type(e).__name__)
            hevent(name, "ws_down", type(e).__name__)
            n += 1
            hevent(name, f"ws_reconnect_{n}", f"backoff {backoff}s")
            STOP.wait(backoff)
            backoff = min(30, backoff * 2)
    hevent(name, "ws_worker_exit", "")

def rest_fallback_worker():
    while not STOP.is_set():
        STOP.wait(5)
        if STOP.is_set(): break
        now = time.time()
        for name, fn in REST.items():
            with lock: recv = st_of(name)["recv"]
            if now - recv > REST_FALLBACK_S:
                try:
                    bid, ask, bq, aq = fn()
                    upd(name, bid, ask, bq, aq, src="rest")
                    hevent(name, "rest_fallback", "WS sin dato fresco; REST toma el relevo")
                except Exception as e:
                    hevent(name, "rest_failed", type(e).__name__)

def main():
    global HW, HF
    ap = argparse.ArgumentParser(); ap.add_argument("--seconds", type=int, default=0)
    ap.add_argument("--interval", type=float, default=2.0); a = ap.parse_args()
    new = not os.path.exists("opportunities.csv")
    of = open("opportunities.csv", "a", newline=""); ow = csv.writer(of)
    tf = open("trades.csv", "a", newline=""); tw = csv.writer(tf)
    HF = open("health.csv", "a", newline=""); HW = csv.writer(HF)
    if new:
        ow.writerow(["ts","buy_ex","sell_ex","buy_ask","sell_bid","gross_pct","fees_pct","net_pct_after_margin"])
        tw.writerow(["ts","buy_ex","sell_ex","btc","buy_price","sell_price","fees_usd","pnl_usd","balance"])
    if os.path.getsize("health.csv") == 0 if os.path.exists("health.csv") else True:
        HW.writerow(["ts","exchange","event","detail"]); HF.flush()
    hevent("bot", "start", f"seconds={a.seconds} interval={a.interval}")
    threads = [threading.Thread(target=ws_worker, args=(n, c), daemon=True) for n, c in WS.items()]
    threads.append(threading.Thread(target=rest_fallback_worker, daemon=True))
    for t in threads: t.start()
    time.sleep(3)  # dar tiempo a que conecten los WS
    bal = START_BALANCE; opps = trades = wins = 0; worst = 0.0; polls = 0
    t0 = time.time()
    while not a.seconds or time.time() - t0 < a.seconds:
        loop = time.time(); polls += 1
        now = time.time()
        with lock: snap = {n: dict(s) for n, s in PX.items()}
        q = {}
        for n, st in snap.items():
            if st["bid"] and st["ask"] and now - st["recv"] <= STALE_S:
                q[n] = (st["bid"], st["ask"], st["bq"], st["aq"])
        caidos = [n for n in WS if n not in q]
        if len(q) >= 2:
            for b in q:
                for s_ in q:
                    if b == s_: continue
                    ask, bid = q[b][1], q[s_][0]
                    gross = (bid - ask) / ask
                    fees = FEES[b] + FEES[s_]
                    if gross > fees + SAFETY_MARGIN:
                        opps += 1; ts = time.strftime("%Y-%m-%d %H:%M:%S")
                        ow.writerow([ts,b,s_,ask,bid,f"{gross*100:.4f}",f"{fees*100:.3f}",f"{(gross-fees-SAFETY_MARGIN)*100:.4f}"]); of.flush()
                        time.sleep(DELAY_S)
                        with lock: q2 = {n: dict(s) for n, s in PX.items()}
                        now2 = time.time()
                        ok = all(n in q2 and q2[n]["bid"] and now2 - q2[n]["recv"] <= STALE_S for n in (b, s_))
                        if not ok: continue
                        buy_p = q2[b]["ask"] * (1 + SLIPPAGE); sell_p = q2[s_]["bid"] * (1 - SLIPPAGE)
                        qty = min(bal / buy_p, q2[b]["aq"] or 1, q2[s_]["bq"] or 1)
                        cost = qty * buy_p; proceeds = qty * sell_p
                        fee_usd = cost * FEES[b] + proceeds * FEES[s_]
                        pnl = proceeds - cost - fee_usd
                        bal += pnl; trades += 1; wins += pnl > 0; worst = min(worst, pnl)
                        tw.writerow([ts,b,s_,f"{qty:.6f}",f"{buy_p:.2f}",f"{sell_p:.2f}",f"{fee_usd:.2f}",f"{pnl:.2f}",f"{bal:.2f}"]); tf.flush()
                        print(f"{ts} SIM {b}->{s_} pnl ${pnl:.2f} balance ${bal:.2f}")
        best = max(((q[s_][0]-q[b][1])/q[b][1], b, s_) for b in q for s_ in q if b != s_) if len(q) > 1 else None
        if polls % 10 == 1:
            line = f"{time.strftime('%H:%M:%S')} polls={polls} opps={opps} trades={trades} P&L=${bal-START_BALANCE:.2f}"
            if best: line += f" | best gross gap {best[0]*100:.3f}% ({best[1]}->{best[2]})"
            if caidos: line += f" | CAIDOS: {','.join(caidos)}"
            print(line, flush=True)
            h = "HEALTH " + " | ".join(
                f"{n} age={now-st_of(n)['recv']:.0f}s src={st_of(n)['src']} msgs={st_of(n)['msgs']} recon={st_of(n)['recon']}"
                for n in WS)
            print(h, flush=True)
        time.sleep(max(0, a.interval - (time.time() - loop)))
    hevent("bot", "stop", f"polls={polls}")
    STOP.set()
    print(f"\nSUMMARY polls={polls} opportunities={opps} sim_trades={trades} winners={wins} worst=${worst:.2f} final=${bal:.2f} P&L=${bal-START_BALANCE:.2f}")
    for n in WS:
        s = st_of(n)
        print(f"HEALTH_SUMMARY {n} msgs={s['msgs']} recon={s['recon']} src={s['src']} last_err={s['last_err']}")
    print("Real orders placed: 0 (this bot cannot place orders)")

if __name__ == "__main__":
    main()
