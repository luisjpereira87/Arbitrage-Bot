import asyncio
import logging

import ccxt.pro as ccxtpro

from core.bots.exchanges.exchange_client import ExchangeClient
from core.bots.exchanges.indicators_utils import IndicatorsUtils
from core.config.properties_multi import PropertiesMulti
from core.dclass.open_position_dclass import OpenPosition
from core.dclass.signal_enum import Signal
from core.meteora.dclass import RangeStatus


class HlClient:
    def __init__(self):
        properties = PropertiesMulti()
        hl = ccxtpro.hyperliquid({
            "walletAddress": properties.WALLET_ADDRESS_HL,
            "privateKey": properties.PRIVATE_KEY_WALLET_HL,
            "enableRateLimit": True,
            "timeout": 10000,
            "testnet": False,
            "options": {"defaultSlippage": 0.01},
        })

        self.hl_exchange = ExchangeClient(hl, properties.WALLET_ADDRESS_HL)
        self.symbol = "SOL/USDC:USDC"
        self.cached_price = 0.0
        self.out_of_range_since = None

    async def start(self):
        """Chama isto no início do teu bot principal."""
        asyncio.create_task(self.update_price_loop())

    async def open_position(self, capital_amount: float) -> bool:
        position = await self.get_position()
        if position:
            logging.warning("⚠️ Posição já existente, a fechar posição...")
            await self.close_position()

        opened_order = await self.hl_exchange.open_new_position(self.symbol, 1.0, Signal.SELL, capital_amount,
                                                                self.cached_price)
        if opened_order:
            return True
        return False

    async def get_position(self) -> OpenPosition:
        return await self.hl_exchange.get_open_position(self.symbol)

    async def close_position(self) -> bool:
        position = await self.get_position()

        if position:
            await self.hl_exchange.close_position(self.symbol, position.size, Signal.BUY)
            return True
        return False

    async def update_price_loop(self):
        while True:
            try:
                prices = await self.hl_exchange.watch_prices(self.symbol)
                if prices and hasattr(prices, 'bid') and prices.bid > 0:
                    self.cached_price = prices.bid
                else:
                    logging.warning("⚠️ Recebido preço inválido da HL")
            except Exception as e:
                logging.error(f"❌ Erro no loop de preços: {e}")
            await asyncio.sleep(0.5)  # Aumentei para 0.5s para aliviar o processamento

    async def is_price_outside_range(self, min_price: float, max_price: float,
                                     margin_percent: float = 0.0) -> bool:
        """
        Verifica se o preço atual está fora da zona de conforto.
        buffer: percentagem ou valor absoluto para evitar falsos positivos.
        """

        if self.cached_price <= 0:
            logging.info("⏳ Aguardando feed de preço da Hyperliquid...")
            await asyncio.sleep(0.5)
            return False

        try:
            # prices = await self.hl_exchange.watch_prices(self.symbol)
            # current_price = self.cached_price

            # print(f"DEBUG: Preço: {current_price} | Range: [{min_price:.2f} - {max_price:.2f}]")

            interval_size = max_price - min_price

            # 2. Define o "alerta" a 10% das extremidades
            # Queremos que o preço esteja dentro de 90% da largura total
            margin_abs = interval_size * margin_percent

            trigger_lower = min_price + margin_abs
            trigger_upper = max_price - margin_abs
            # print(f"DEBUG: Preço: {current_price} | Range: [{trigger_lower:.2f} - {trigger_upper:.2f}]")

            if trigger_lower >= trigger_upper:
                logging.info("⚠️ Aviso: Margem de segurança maior que o próprio range!")
                return True  # Rebalanceia imediatamente

            if self.cached_price < trigger_lower or self.cached_price > trigger_upper:
                return True
            return False

        except Exception as e:
            logging.error(f"❌ Erro ao validar a posição: {e}")
            return False

    async def check_range_status(self, min_price: float, max_price: float, margin_percent: float = 0.0) -> RangeStatus:
        if self.cached_price <= 0:
            return RangeStatus.INSIDE

        interval_size = max_price - min_price
        margin_abs = interval_size * margin_percent

        trigger_lower = min_price + margin_abs
        trigger_upper = max_price - margin_abs

        if self.cached_price < trigger_lower:
            return RangeStatus.OUT_LOWER
        elif self.cached_price > trigger_upper:
            return RangeStatus.OUT_UPPER

        return RangeStatus.INSIDE

    async def get_balance(self) -> tuple[float | None, float, bool]:
        position = await self.hl_exchange.get_open_position(self.symbol)
        balance = await self.hl_exchange.get_available_balance()
        unrealized_pnl = 0.0
        is_position = False
        if position:
            unrealized_pnl = position.unrealizedPnl
            is_position = True

        return unrealized_pnl, balance, is_position

    async def calculate_dynamic_range_width(self, limit=30, lookback=14):
        ohlcv = await self.hl_exchange.get_ohlcv(self.symbol, limit=limit)
        return IndicatorsUtils.calculate_channel_width(ohlcv, lookback=lookback)

    async def adjust_balance(self, capital_amount: float, dex_price: float) -> tuple[
        float, float]:
        return await self.hl_exchange.get_perfect_quantities(capital_amount, dex_price, self.symbol)

    async def is_market_turbulent(self, threshold=0.005):
        # Obtém o dataframe
        ohlcv = await self.hl_exchange.get_ohlcv(self.symbol, limit=1)

        # Extraímos o valor usando .iloc[-1] (a última linha)
        # Isto garante que obténs o valor bruto (float) e não uma Series com índice
        high = float(ohlcv['high'].iloc[-1])
        low = float(ohlcv['low'].iloc[-1])

        amplitude = (high - low) / low

        is_turbulent = amplitude > threshold

        # print(f"🔍 [Market Check] Amplitude: {amplitude:.6f} | Turbulento: {is_turbulent}")

        return is_turbulent
