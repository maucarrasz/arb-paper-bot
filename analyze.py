import csv
r=list(csv.DictReader(open("trades.csv")))
o=sum(1 for _ in open("opportunities.csv"))-1 if __import__("os").path.exists("opportunities.csv") else 0
w=[x for x in r if float(x["pnl_usd"])>0]
print(f"opportunities={o} sim_trades={len(r)} winners={len(w)}")
if r:
    p=[float(x['pnl_usd']) for x in r]; print(f"total=${sum(p):.2f} biggest_loss=${min(p):.2f} best=${max(p):.2f}")
print("Honest note: fills here are optimistic (top-of-book, pre-funded balances on every exchange, no withdrawal/transfer cost, USD vs USDT basis ignored).")
