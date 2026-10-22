"""Smart money: posicionamiento agregado de los top traders de Hyperliquid.

Gratis y sin clave (verificado 22-oct-2026):
- Ranking: GET https://stats-data.hyperliquid.xyz/Mainnet/leaderboard
- Posiciones de cualquier wallet: POST https://api.hyperliquid.xyz/info
  {"type":"clearinghouseState","user":"0x..."}
Limitacion honesta: NO hay historico publico de estas posiciones; no se puede
backtestear la senal hacia atras. Por eso (a) guardamos un snapshot por hora
en smartmoney_snapshots.csv para construir dataset propio, y (b) las variantes
que la usan se validan solo en paper en tiempo real (fuera de muestra por
construccion, pero sin pasado medible todavia).
"""
import csv
import os
import time

import requests

LB_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"
SNAP_CSV = "smartmoney_snapshots.csv"
S = requests.Session()


def top_wallets(n=10, window="month"):
    """Top n wallets por PnL del mes en el leaderboard oficial."""
    r = S.get(LB_URL, timeout=20)
    r.raise_for_status()
    rows = r.json().get("leaderboardRows", [])

    def pnl(row):
        for w, p in row.get("windowPerformances", []):
            if w == window:
                try:
                    return float(p.get("pnl", 0))
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    rows = sorted(rows, key=pnl, reverse=True)
    return [(row["ethAddress"], pnl(row)) for row in rows[:n]]


def wallet_positions(addr):
    """{coin: usd_neto_con_signo} de las posiciones abiertas de una wallet."""
    r = S.post(INFO_URL, json={"type": "clearinghouseState", "user": addr},
               timeout=20)
    r.raise_for_status()
    out = {}
    for ap in r.json().get("assetPositions", []):
        p = ap.get("position", {})
        coin = p.get("coin")
        try:
            szi = float(p.get("szi", 0) or 0)
            entry = float(p.get("entryPx", 0) or 0)
        except (TypeError, ValueError):
            continue
        if coin and szi:
            out[coin] = szi * entry  # +long / -short, en USD de entrada
    return out


def snapshot(n=10, sleep_s=0.25):
    """Net USD por moneda agregando los top n wallets. {coin: usd} (+long/-short)."""
    net = {}
    wallets = top_wallets(n)
    for addr, _pnl in wallets:
        try:
            for coin, usd in wallet_positions(addr).items():
                net[coin] = net.get(coin, 0.0) + usd
        except Exception as e:
            print(f"[smartmoney] {addr[:10]}: {e}")
        time.sleep(sleep_s)
    return net


def log_snapshot(ts_iso, net):
    new = not os.path.exists(SNAP_CSV)
    with open(SNAP_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "coin", "net_usd_top10"])
        for coin, usd in sorted(net.items(), key=lambda kv: -abs(kv[1])):
            w.writerow([ts_iso, coin, round(usd, 0)])


if __name__ == "__main__":
    net = snapshot()
    for c, u in sorted(net.items(), key=lambda kv: -abs(kv[1]))[:15]:
        print(f"{c:10s} {u:>16,.0f} USD")
