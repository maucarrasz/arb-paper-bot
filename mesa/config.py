"""Configuracion de la mesa de trading paper (determinista, sin LLM, sin claves).

Inspirada en el reel de Instagram (antonspain_ia, oct-2026) que describe una
"mesa de 6 bots": scanner de volumen, lector de noticias, analista de
entrada/stop/objetivo, esceptico que rebate, riesgo y ejecucion.
El reel NO da reglas ni resultados verificables; estas reglas son propias,
simples e interpretables, siguiendo practicas estandar de trading sistematico
(riesgo fijo por operacion, stops por ATR, filtro de tendencia, limite diario).
"""

UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL = "1h"
KLINES_LIMIT = 220          # velas 1h por simbolo en vivo (~9 dias, cubre EMAs/ATR)
KLINE_URL = "https://data-api.binance.vision/api/v3/klines"

# --- Scanner (bot 1: detector de volumen/momentum) ---
VOL_RATIO_MIN = 1.8         # volumen de la ultima vela cerrada >= 1.8x su SMA20
RET_3H_MIN = 0.012          # retorno de 3h >= +1.2% (solo long: spot sin margen)

# --- Analista (bot 3: entrada/stop/objetivo) ---
RSI_N = 14
RSI_LONG_MAX = 75.0         # no comprar sobreextendido
ATR_N = 14
STOP_ATR = 1.5              # stop = entrada - 1.5*ATR
TARGET_ATR = 2.25           # objetivo = entrada + 2.25*ATR (R:R 1.5)
MIN_RR = 1.5
ATR_PCT_MAX = 0.04          # ATR/close > 4% = mercado caotico, veto
EMA_FAST, EMA_SLOW = 20, 50 # filtro de tendencia en 1h y 4h

# --- Esceptico (bot 4: rebate y decide comprar/esperar/pasar) ---
NEWS_VETO_SCORE = -2        # contexto de noticias muy negativo veta el long

# --- Riesgo (bot 5) ---
START_EQUITY = 10000.0      # USD simulados
RISK_PCT = 0.0075           # arriesga 0.75% del equity por operacion
MAX_OPEN = 2                # max posiciones abiertas simultaneas
DAILY_LOSS_CAP = 0.02       # -2% realizado en el dia UTC = no abrir mas hoy
NOTIONAL_CAP = 0.25         # notional max 25% del equity por posicion
COOLDOWN_LOSSES = 2         # 2 perdidas seguidas en un simbolo...
COOLDOWN_H = 24             # ...= 24h sin operar ese simbolo

# --- Ejecucion simulada (bot 6) ---
FEE = 0.001                 # 0.10% taker por lado (Binance spot estandar)
SLIPPAGE = 0.0005           # 0.05% por lado (supuesto)
TIME_STOP_H = 72            # cierre por tiempo a las 72h si no toco stop/objetivo
TREND_EXIT = True           # salida discrecional si EMA20 < EMA50 en 1h
