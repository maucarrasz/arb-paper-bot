#!/usr/bin/env python3
"""Paper-trading BTC arbitrage bot. NO real orders, NO API keys, public data only.
Run: python arb_paper_bot.py            (Ctrl+C to stop; prints summary)
     python arb_paper_bot.py --seconds 60
"""
import argparse, csv, os, time, concurrent.futures as cf
import requests

# Taker fee per exchange (fraction). Sources in README.md. Edit as you like.
FEES = {"binance": 0.0010, "okx": 0.0010, "kucoin": 0.0010, "kraken": 0.0040, "coinbase": 0.0060}
SAFETY_MARGIN = 0.0005   # extra 0.05% required on top of fees
SLIPPAGE = 0.0002        # 0.02% worse price on each leg
DELAY_S = 0.5            # simulated latency between seeing the gap and filling
START_BALANCE = 1000.0
S = requests.Session()

def j(url):
    return S.get(url, timeout=5).json()

def binance():  # public market-data host (api.binance.com is blocked from some countries/IPs)
    d = j("https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol=BTCUSDT")
    return float(d["bidPrice"]), float(d["askPrice"]), float(d["bidQty"]), float(d["askQty"])
def okx():
    d = j("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT")["data"][0]
    return float(d["bidPx"]), float(d["askPx"]), float(d["bidSz"]), float(d["askSz"])
def kucoin():
    d = j("https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=BTC-USDT")["data"]
    return float(d["bestBid"]), float(d["bestAsk"]), float(d["bestBidSize"]), float(d["bestAskSize"])
def kraken():   # USD, not USDT: carries a small basis vs USDT
    d = j("https://api.kraken.com/0/public/Ticker?pair=XBTUSD")["result"]
    d = next(iter(d.values()))
    return float(d["b"][0]), float(d["a"][0]), float(d["b"][1]), float(d["a"][1])
def coinbase():  # USD
    d = j("https://api.exchange.coinbase.com/products/BTC-USD/ticker")
    return float(d["bid"]), float(d["ask"]), 1.0, 1.0   # size not given: assume ok
FEEDS = {"binance": binance, "okx": okx, "kucoin": kucoin, "kraken": kraken, "coinbase": coinbase}

def snapshot(names=None):
    names = names or list(FEEDS)
    out = {}
    with cf.ThreadPoolExecutor(len(names)) as ex:
        futs = {n: ex.submit(FEEDS[n]) for n in names}
        for n, f in futs.items():
            try: out[n] = f.result()
            except Exception as e: print(f"  [warn] {n} failed: {type(e).__name__}")
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--seconds", type=int, default=0)
    ap.add_argument("--interval", type=float, default=1.0); a = ap.parse_args()
    opp_f, tr_f = "opportunities.csv", "trades.csv"
    new = not os.path.exists(opp_f)
    of = open(opp_f, "a", newline=""); ow = csv.writer(of)
    tf = open(tr_f, "a", newline=""); tw = csv.writer(tf)
    if new:
        ow.writerow(["ts","buy_ex","sell_ex","buy_ask","sell_bid","gross_pct","fees_pct","net_pct_after_margin"])
        tw.writerow(["ts","buy_ex","sell_ex","btc","buy_price","sell_price","fees_usd","pnl_usd","balance"])
    bal = START_BALANCE; opps = trades = wins = 0; worst = 0.0; polls = 0
    t0 = time.time()
    try:
        while not a.seconds or time.time() - t0 < a.seconds:
            loop = time.time(); polls += 1
            q = snapshot()
            if len(q) >= 2:
                for b in q:
                    for s in q:
                        if b == s: continue
                        ask, bid = q[b][1], q[s][0]
                        gross = (bid - ask) / ask
                        fees = FEES[b] + FEES[s]
                        if gross > fees + SAFETY_MARGIN:
                            opps += 1; ts = time.strftime("%Y-%m-%d %H:%M:%S")
                            ow.writerow([ts,b,s,ask,bid,f"{gross*100:.4f}",f"{fees*100:.3f}",f"{(gross-fees-SAFETY_MARGIN)*100:.4f}"]); of.flush()
                            # simulate: wait, re-read prices, add slippage
                            time.sleep(DELAY_S)
                            q2 = snapshot([b, s])
                            if b not in q2 or s not in q2: continue
                            buy_p = q2[b][1] * (1 + SLIPPAGE); sell_p = q2[s][0] * (1 - SLIPPAGE)
                            qty = min(bal / buy_p, q2[b][3], q2[s][2])
                            cost = qty * buy_p; proceeds = qty * sell_p
                            fee_usd = cost * FEES[b] + proceeds * FEES[s]
                            pnl = proceeds - cost - fee_usd
                            bal += pnl; trades += 1; wins += pnl > 0; worst = min(worst, pnl)
                            tw.writerow([ts,b,s,f"{qty:.6f}",f"{buy_p:.2f}",f"{sell_p:.2f}",f"{fee_usd:.2f}",f"{pnl:.2f}",f"{bal:.2f}"]); tf.flush()
                            print(f"{ts} SIM {b}->{s} pnl ${pnl:.2f} balance ${bal:.2f}")
            best = max(((q[s][0]-q[b][1])/q[b][1], b, s) for b in q for s in q if b != s) if len(q) > 1 else None
            if polls % 10 == 1 and best:
                print(f"{time.strftime('%H:%M:%S')} polls={polls} opps={opps} trades={trades} P&L=${bal-START_BALANCE:.2f} | best gross gap {best[0]*100:.3f}% ({best[1]}->{best[2]})")
            time.sleep(max(0, a.interval - (time.time() - loop)))
    except KeyboardInterrupt:
        pass
    print(f"\nSUMMARY polls={polls} opportunities={opps} sim_trades={trades} winners={wins} worst=${worst:.2f} final=${bal:.2f} P&L=${bal-START_BALANCE:.2f}")
    print("Real orders placed: 0 (this bot cannot place orders)")

if __name__ == "__main__":
    main()
