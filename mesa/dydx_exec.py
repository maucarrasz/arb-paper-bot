"""Ejecucion REAL en dYdX v4 testnet (dinero ficticio, motor de matching real).

Pedido de Mauricio (22-oct-2026): las 10 variantes del torneo dejan de ser
simuladas y ejecutan ordenes reales en una venue con matching real. Binance,
Deribit, Kraken, BitMEX y Gate estan bloqueados por geo desde nuestra infra;
Hyperliquid testnet exige deposito mainnet. dYdX v4 testnet es permisionless:
wallet propia + faucet gratis, sin KYC ni dinero real. NUNCA mainnet.

Arquitectura: UNA wallet (mnemonic en vault "dYdX testnet (agente)"), 10
subcuentas (0-9), una por variante del torneo, fondeadas con USD 500 de
faucet cada una. Equity REAL leida del indexer. Divergencia paper vs real se
reporta (fees/slippage/latencia reales).
"""
import asyncio
import os

import requests

INDEXER = "https://indexer.v4testnet.dydx.exchange"
FAUCET = "https://faucet.v4testnet.dydx.exchange/faucet/tokens"

# Mapa Binance symbol -> mercado dYdX v4 (los que existan en testnet)
SYMBOL_MAP = {
    "BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD", "SOLUSDT": "SOL-USD",
    "XRPUSDT": "XRP-USD", "DOGEUSDT": "DOGE-USD", "TRXUSDT": "TRX-USD",
    "ADAUSDT": "ADA-USD", "LINKUSDT": "LINK-USD", "AVAXUSDT": "AVAX-USD",
    "LTCUSDT": "LTC-USD", "BCHUSDT": "BCH-USD", "NEARUSDT": "NEAR-USD",
    "ATOMUSDT": "ATOM-USD", "UNIUSDT": "UNI-USD", "ARBUSDT": "ARB-USD",
    "OPUSDT": "OP-USD", "SUIUSDT": "SUI-USD", "APTUSDT": "APT-USD",
    "INJUSDT": "INJ-USD", "FILUSDT": "FIL-USD",
}
# dYdX v4 NO lista BNB: esas senales se saltan y se loguean (honesto).

MNEMONIC = os.environ.get("DYDX_MNEMONIC", "")


def wallet_and_address():
    from dydx_v4_client.key_pair import KeyPair
    from dydx_v4_client.wallet import Wallet
    kp = KeyPair.from_mnemonic(MNEMONIC)
    w = Wallet(kp, 0, 0)
    return w, w.address


def equity(address, subaccount):
    """Equity real en USD de una subcuenta (None si no existe aun)."""
    r = requests.get(f"{INDEXER}/v4/addresses/{address}/subaccountNumber/{subaccount}",
                     timeout=15)
    if r.status_code != 200:
        return None
    s = r.json().get("subaccount", {})
    try:
        return float(s.get("equity", 0))
    except (TypeError, ValueError):
        return None


def markets_live():
    """Mercados activos en testnet (para filtrar SYMBOL_MAP)."""
    r = requests.get(f"{INDEXER}/v4/perpetualMarkets", timeout=20)
    r.raise_for_status()
    return set(r.json().get("markets", {}).keys())


def faucet_claim(address, subaccount=0, amount=2000):
    """Pide fondos falsos al faucet. 202 = encolado. Limite 2/IP/dia."""
    r = requests.post(FAUCET, json={"address": address,
                                    "subaccountNumber": subaccount,
                                    "amount": amount, "asset": "USDC"},
                      timeout=30)
    return r.status_code


async def _node():
    from dydx_v4_client.network import TESTNET
    from dydx_v4_client.node.client import NodeClient
    return await NodeClient.connect(TESTNET.node)


async def transfer_to_subaccount(wallet, from_sub, to_sub, amount_usdc):
    """Mueve USDC entre subcuentas de la MISMA wallet (on-chain, gratis)."""
    from dydx_v4_client import Subaccount
    node = await _node()
    sender = Subaccount(wallet, from_sub)
    recipient = Subaccount(wallet, to_sub)
    tx = await node.transfer(sender=sender, recipient_subaccount=recipient,
                             asset_id=0, amount=int(amount_usdc * 10_000_000))
    return tx


async def market_order(wallet, subaccount, market_ticker, side, size_base):
    """Orden MARKET real en testnet. side: 'BUY'|'SELL'. Devuelve hash/resp."""
    from dydx_v4_client import Subaccount, OrderFlags
    from dydx_v4_client.indexer.rest.constants import OrderType, OrderSide
    from dydx_v4_client.indexer.rest.indexer_client import IndexerClient
    from dydx_v4_client.node.market import Market
    node = await _node()
    idx = IndexerClient("https://indexer.v4testnet.dydx.exchange")
    mk = await idx.markets.get_perpetual_markets(market_ticker)
    mdata = mk["markets"][market_ticker]
    market = Market(mdata)
    sub = Subaccount(wallet, subaccount)
    order_id = market.order_id(sub, 0, None, OrderFlags.SHORT_TERM)
    height = await node.latest_block_height()
    order = market.order(
        order_id=order_id, order_type=OrderType.MARKET,
        side=OrderSide.SIDE_BUY if side == "BUY" else OrderSide.SIDE_SELL,
        size=float(size_base), price=0,
        time_in_force=None, reduce_only=False,
        good_til_block=int(height) + 10,
    )
    return await node.place_order(wallet=sub.wallet, order=order)
