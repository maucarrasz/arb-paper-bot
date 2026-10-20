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

## Experimento acotado: 20 al 27 de octubre de 2026
Este repo corre como un experimento de prueba de 1 semana en GitHub Actions (repo publico = minutos gratis).
El workflow paper.yml lee precios cada ~2 segundos en corridas encadenadas de ~5h40m.
Fin automatico: ninguna corrida empieza en/ despues del 28-oct UTC; el workflow se deshabilita solo.
Despues se corre analyze.py sobre los CSV y se entrega el resumen honesto final.
Precision real: lectura cada ~2 s, con breves huecos al encadenar corridas y si algun exchange no responde.
Aviso de uso justo: GitHub puede restringir el uso desproporcionado de Actions en cuentas gratuitas;
si eso ocurre, el bot se detiene y se avisa en el resumen.
