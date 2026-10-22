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

---

# Mesa de trading paper (6 bots) - `mesa/`

Conversion pedida por Mauricio (22-oct-2026) del bot a lo que describe el reel
de Instagram `antonspain_ia` ("Grok Bot y su mesa de trading con 6 bots"):
varios bots coordinados - uno detecta volumen, otro lee noticias para explicar
el movimiento, otro marca entrada/stop/objetivo, un esceptico rebate las
operaciones debiles, y corren 24/7.

**El reel no publica reglas, parametros ni resultados verificables** (las
ganancias que menciona son solo afirmaciones del creador). Esta es una
interpretacion propia, determinista e interpretable, con practicas estandar de
trading sistematico. NO usa LLM ni APIs de pago. Sigue siendo 100% paper:
sin claves de exchange, sin ordenes reales, dinero ficticio.

## Los 6 roles (modulos en `mesa/`)

1. **Scanner** (`agents.scan`): vela 1h cerrada con volumen >= 1.8x su SMA20
   y retorno 3h >= +1.2% en BTC/ETH/SOL (Binance publico).
2. **Noticias** (`news.py`): RSS publicos (CoinTelegraph, Decrypt) + lexicon
   simple por titular. Etiqueta alcista/bajista/mixto. Limitacion honesta:
   cuenta palabras clave, no "entiende" la noticia. Solo sirve de contexto
   y veto; nunca abre una operacion por si solo.
3. **Analista** (`agents.analyze`): plan entrada/stop/objetivo. Stop = 1.5xATR(14),
   objetivo = 2.25xATR (R:R 1.5), filtros de tendencia EMA20>EMA50 en 1h y 4h,
   RSI(14) <= 75, ATR% <= 4%.
4. **Esceptico** (`agents.skeptical`): rebate y veta (PASAR) con razones
   registradas: noticias muy negativas, posicion ya abierta, cooldown por
   perdidas, tope de perdida diaria, movimiento ya extendido (>8% en 3h).
5. **Riesgo** (`agents.size_up`): riesgo fijo 0.75% del equity por operacion,
   tope 25% notional, max 2 posiciones abiertas, tope de perdida diaria 2%,
   cooldown 24h tras 2 perdidas seguidas en un activo.
6. **Ejecucion simulada** (`engine.py`): fills con slippage 0.05% + comision
   taker 0.10% por lado. Salidas por stop, objetivo, ruptura de tendencia
   (EMA20<EMA50) o tiempo (72h). Si una vela toca stop y objetivo, cuenta
   el stop primero (criterio conservador).

Practicas de referencia: riesgo fijo por operacion ("1% rule"), stops por ATR
y filtro de tendencia son estandar en trading sistematico (p.ej.
pomegra.io/learn/library/track-e-trading-risk/risk-management/chapter-04-position-sizing-methods/the-1-percent-rule,
cleartape.ai/academy/trend-following-breakouts/stops-and-position-sizing).
Long-only spot: no simulamos margen ni shorts.

## Como corre

`.github/workflows/mesa.yml`: una pasada cada hora (minuto 7) en Actions
(repo publico = minutos gratis; cada corrida dura ~1 min). Estado y resultados
se commitean al repo: `mesa_state.json`, `mesa_signals.csv` (cada senal con la
decision de cada bot y sus razones), `mesa_trades.csv`, `mesa_equity.csv`.
El bot de arbitraje original sigue corriendo aparte hasta su fin automatico
(27-oct); esta mesa es independiente y no lo toca.

## Backtest honesto (`python -m mesa.backtest --days N`)

Datos: velas 1h reales de Binance. Sin veto de noticias (no existe historico
RSS gratuito): diferencia documentada vs. vivo. Fills conservadores.

| Periodo | Retorno mesa | Buy&hold BTC/ETH/SOL | Trades | Winrate | Max DD | Comisiones |
|---|---|---|---|---|---|---|
| 180d (abr-oct 2026) | -3.1% | +4.9 / +2.9 / +20.2% | 48 | 37.5% | 3.1% | $237 |
| 365d (oct 2025-oct 2026) | -2.3% | -24.6 / -37.0 / -42.7% | 93 | 41.9% | 5.2% | $465 |

Lectura honesta: la mesa **no gana dinero en backtest**: pierde poco porque
arriesga poco y pasa la mayor parte del tiempo fuera del mercado; en un ano
bajista eso preserva capital frente a hold, pero no es una estrategia
rentable tal cual. El cuello de botella es la relacion winrate/R:R (probamos objetivo 3.0xATR y fue peor: -6.3% en 365d) y el costo
de comisiones. **No promete rentabilidad; es una mesa paper para aprender y
medir con datos reales en adelante.**

## Reproducir

```
pip install requests
python -m mesa.desk            # una pasada en vivo (paper)
python -m mesa.backtest --days 180
```

## Mesa v2 (22-oct-2026, noche): remodelada siguiendo los mejores bots open source

Pedido de Mauricio: "investiga a fondo los mejores bots y modelalos usando esta
infraestructura". Investigados: Freqtrade, TradingAgents (arXiv:2412.20138),
Hummingbot, Jesse, NautilusTrader, y literatura de trend following (LeBeau,
regla SMA200, walk-forward). Que se modelo de quien:

1. **Filtro de regimen SMA200 en 4h** (regla de los 200 periodos, trend filter
   clasico): si el cierre 4h esta bajo su SMA200, la mesa no compra. En mercado
   bajista estructural la mesa se sienta a esperar.
2. **Salida Chandelier (Chuck LeBeau)**: se elimina el objetivo fijo; stop
   trailing = maximo high desde entrada - 2.5xATR, nunca baja. Corta perdedores
   igual (1.5xATR inicial) pero deja correr ganadores. Tiempo maximo 96h.
3. **Protecciones estilo Freqtrade** (docs.freqtrade.io/en/2024.1/includes/protections):
   - CooldownPeriod: 2h sin re-entrar al mismo activo tras cerrar.
   - StoplossGuard global: >=3 stops en 24h = pausa global 4h.
   - MaxDrawdown: drawdown de equity > 10% = pausa 48h.
   - LowProfitPairs: ultimas 5 ops de un activo sumando < -2% = bloqueo 72h.
4. **Debate Toro vs Oso (TradingAgents, Tauric Research)**: el esceptico ahora
   argumenta: lista puntos a favor (volumen, momentum, tendencia, regimen,
   noticias+) y en contra (RSI alto, movimiento extendido, noticias-,
   volatilidad). Solo COMPRA si toros > osos y al menos 3 argumentos a favor;
   todo queda registrado en mesa_signals.csv. TradingAgents usa LLMs; aqui el
   debate es determinista (gratis, reproducible, sin API de pago).
5. **Validacion walk-forward honesta**: params fijados a priori de las fuentes
   (no optimizados). Ventana IS (dentro de muestra) = 2025-10-22 a 2026-04-25;
   OOS (fuera de muestra) = 2026-04-25 a 2026-10-22. Resultados:

| Ventana | v1 (original) | v2 (remodelada) |
|---|---|---|
| IS 185d | -3.2% (72 trades, WR 41.7%, DD 5.1%) | -4.4% (33 trades, WR 24.2%, DD 4.7%) |
| OOS 180d | -2.5% (66 trades, WR 39.4%, DD 4.2%, fees $324) | **-1.7%** (36 trades, WR 33.3%, DD 2.5%, fees $180) |

Lectura honesta: **ninguna version gana dinero**. v2 opera la mitad, paga la
mitad de comisiones, tiene menor drawdown en ambas ventanas y es mejor fuera de
muestra (-1.7% vs -2.5%); peor dentro de muestra. El filtro de regimen la deja
fuera en mercados bajistas (por eso en OOS perdio menos que el mercado: BTC
-5.8%, ETH -13.5%, SOL -12.5% en la misma ventana). Sigue siendo un sistema
paper para aprender; no hay promesa de rentabilidad. v2 queda como modo activo
(`MODE = "v2"` en mesa/config.py); v1 sigue disponible con `--mode v1`.
