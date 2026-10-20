# Bot de paper trading BTC (sin dinero real, sin API keys)
Correr (Windows/WSL, Python 3.10+):
  pip install requests
  python arb_paper_bot.py            # Ctrl+C para parar
  python analyze.py                  # reporte honesto del CSV
Escribe opportunities.csv y trades.csv. NO puede enviar ordenes.
Datos: Binance (data-api.binance.vision), OKX, KuCoin, Kraken, Coinbase (APIs publicas).
Comisiones taker usadas: Binance 0.10% (binance.com/en/support/faq/fee-structure-on-binance-115000429332),
Kraken Pro tier 1 0.40% (kraken.com/features/fee-schedule), Coinbase Exchange hasta 0.60% (help.coinbase.com/en/exchange/trading-and-funding/exchange-fees),
OKX y KuCoin 0.10% = SUPUESTO sin verificar. Slippage 0.02%/pata, delay 0.5s, margen 0.05%: supuestos mios.
Limites: fills optimistas (mejor precio, saldo ya repartido en todos los exchanges, sin costo de transferir), Kraken/Coinbase son USD y el resto USDT (base ~0.15%).
Estudios: Makarov & Schoar JFE 2020 (sciencedirect.com/science/article/abs/pii/S0304405X19301746); arxiv.org/abs/1812.00595.
