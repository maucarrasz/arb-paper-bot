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

## Reto $100 -> $500 (meta ajustada 22-oct 22:21 UTC): seleccion evolutiva en paper

Pedido de Mauricio: objetivo declarado $100 -> $500 en paper (ajustado desde $1000 el 22-oct tras ver el Monte Carlo), con regla
"que sea rentable, si no muere el bot", modelando a los mejores sistemas
publicados. Sin promesas: los numeros de abajo son los medidos.

### Torneo de estrategias (backtest walk-forward, 590 dias, 12 pares USDT)

Costos reales en todos: 0.10% comision + 0.05% deslizamiento por lado.
IS = hasta 2026-04-25 (409d), OOS = despues (179d). Sin optimizar sobre OOS.

| Estrategia | Inspirada en | IS | OOS | Veredicto |
|---|---|---|---|---|
| ROT: rotacion momentum cross-sectional (top-2, 20d, filtro BTC>SMA200d) | literatura momentum crypto (SSRN 4322637, Grayscale) | -6.7% | -3.1% (38 trades) | ELIMINADA |
| BRK: breakout Donchian 20h + regimen + Chandelier | tendencia clasica / Freqtrade | -62.0% | -37.7% (474 trades) | ELIMINADA (sobre-opera, fees) |
| **MR: mean reversion RSI(14) 4h < 30, salida RSI>55 o stop 2xATR, filtro BTC>SMA200d** | mean reversion en rangos | -4.3% | **+4.6% (18 trades, Sharpe 1.98, DD 1.7%)** | **SUPERVIVIENTE** |

Variantes de MR probadas (disciplina: mismas reglas, solo umbrales):
(30/55/2.0) +4.6% OOS - la base; (25/55/2.0) +0.8%; (30/60/2.5) +2.6%;
(35/55/2.0) +3.5%. Todas negativas en IS. La evidencia de MR es DEBIL:
18 trades fuera de muestra, IS negativo, parametros fragiles.

Nota de honestidad tecnica: la primera version de ROT marcaba +84% OOS y
era un bug de lookahead (rebalancear antes de ganar el retorno del dia).
Corregido, desaparecio. Lo mismo se audito en MR/BRK (solo velas cerradas,
regimen del dia completado anterior).

### Probabilidad real del 10x (Monte Carlo, bootstrap de retornos diarios OOS de MR)

5000 simulaciones de 365 dias, $100 iniciales, funding 0.03%/dia sobre
notional apalancado, ruina = equity < $10 o -85% desde el pico (meta $500;
con la meta original $1000 los resultados eran iguales: 0% en todos):

| Apalancamiento | P(llegar a $500) | P(ruina) | Mediana a 1 ano |
|---|---|---|---|
| 1x | 0.0% | 0.0% | $98 |
| 3x | 0.0% | 0.0% | $94 |
| 5x | 0.0% | 0.0% | $89 |
| 10x | 0.0% | 0.08% | $76 |

**Veredicto: con un edge real y honesto, el 10x original en ~1 ano no sale (0% en las
5000 simulaciones a cualquier apalancamiento).** MR rinde ~+4.6% por 180d
OOS (~9%/ano si se sostiene); subir apalancamiento solo aumenta el costo de
funding y la varianza. El reto queda como objetivo de largo plazo, no como
expectativa.

### Libro paper en vivo: `mesa/reto100.py`

Corre dentro del workflow horario, libro independiente de $100 simulados
con la estrategia superviviente (MR-4h). Reglas de supervivencia
(seleccion evolutiva): equity < $50 = el libro MUERE y sera reemplazado
por una variante; equity >= $500 = reto cumplido. Estado en
`reto100_state.json`, historial en `reto100_equity.csv` y
`reto100_trades.csv` (commiteados cada hora por el workflow).

## Torneo GEN2 - apuestas up/down apalancadas (paper, estilo Moon)

Pedido de Mauricio (22-oct-2026 19:20 Lima): arquitecturas de bots para apuestas
de subida/bajada con dinero ficticio; que quede la mejor. Moon.com NO se usa
(ToS 3.2 prohibe Peru y United States). Esto es simulacion propia con datos de
mercado publicos.

**Reglas CONGELADAS: 2026-10-23 00:22 UTC** (mesa/moonbet.py, commit 7b85889).
El diseno uso solo documentacion publica de Moon y disciplina de velas cerradas;
NINGUN dato posterior a la congelacion se usara para cambiar reglas de variantes
vivas. La evaluacion es prospectiva desde la primera apuesta registrada
(moonbet_trades.csv). Si una regla cambia despues, se documenta como GEN2.x con
fecha y motivo, y la comparacion se reinicia desde ese cambio.

**ENMIENDA GEN2.1 / GEN1.1 (2026-10-23 14:28 UTC, 09:28 Lima)**: Mauricio por
WhatsApp: "7 dias de torneo, los que no apuestan mueren, si no es rentable
muere". Reemplaza la regla de 30 dias en AMBOS torneos (GEN1 torneo.py y GEN2
moonbet.py). Criterio aplicado: evaluacion por libro desde su propio `born` +
7 dias exactos; al cumplirse, muere la variante sin actividad alguna
(GEN1: trades+posiciones abiertas = 0; GEN2: apuestas cerradas+abiertas = 0)
o con equity <= $500 ("no rentable"; con el fee de apertura del 1% un apostador
no queda jamas en $500.00 exactos, asi que en la practica el '=' solo alcanza
a las que nunca apostaron). Muerte por equity < $250 y objetivo >= $1000 sin
cambios. Ventanas exactas: GEN1 (born 2026-10-22 22:28:59 UTC) muerte evaluable
desde 2026-10-29 22:28:59 UTC (29-oct 17:28 Lima); GEN2 (born 2026-10-23
00:22:30 UTC) desde 2026-10-30 00:22:30 UTC (29-oct 19:22 Lima). Sin reset de
estados ni historial; la comparacion entre variantes sigue prospectiva.

Mecanica verificada (help.moon.com): apuesta abierta up/down, wager = perdida
maxima, liquidacion (bust) al moverse 1/leverage en contra, cierre por senal.
Fees: 1% apertura sobre wager + performance MINIMO documentado 10% del beneficio
(escenario OPTIMISTA explicito; sensibilidad 30% registrada por trade). Esto NO
es replica exacta del payout de Moon: sus fills y slippage no son observables.

Protocolo de ganador (no basta llegar primero a USD 1000): se compara despues de
>= 30 apuestas cerradas por variante o >= 14 dias, lo que ocurra despues, con
pnl neto bajo AMBOS escenarios de fee; si ninguna es positiva en el escenario
pesimista, el resultado honesto es "sin edge" y se reporta asi. Llegar a la meta
dispara aviso inmediato pero NO declaracion de ganador.
