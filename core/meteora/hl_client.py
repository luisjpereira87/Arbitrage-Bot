import asyncio
import enum
import logging

import ccxt.pro as ccxtpro

from core.bots.exchanges.exchange_client import ExchangeClient
from core.bots.exchanges.indicators_utils import IndicatorsUtils, RsiResponse
from core.config.properties_multi import PropertiesMulti
from core.dclass.open_position_dclass import OpenPosition
from core.dclass.signal_enum import Signal
from core.meteora.dclass import RangeStatus


class DirectionMarket(enum.Enum):
    UP = "UP"
    DOWN = "DOWN"
    NEUTRAL = "NEUTRAL"


class HlClient:
    def __init__(self):
        properties = PropertiesMulti()
        hl = ccxtpro.hyperliquid({
            "walletAddress": properties.WALLET_ADDRESS_HL.lower(),
            "privateKey": properties.PRIVATE_KEY_WALLET_HL,
            "enableRateLimit": True,
            "timeout": 10000,
            "testnet": False,
            "options": {"defaultSlippage": 0.01},
        })

        self.hl_exchange = ExchangeClient(hl, properties.WALLET_ADDRESS_HL.lower())
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

    async def get_range_distance_percentage(self, min_price: float, max_price: float) -> float:
        if self.cached_price <= 0:
            return 1.0  # Segurança caso o preço não esteja carregado

        interval_size = max_price - min_price
        if interval_size <= 0:
            return 0.0

        # Ponto médio do range
        mid_price = (min_price + max_price) / 2

        # Se o preço está abaixo do centro, medimos a distância relativa ao min_price
        if self.cached_price <= mid_price:
            # Distância do preço atual até ao min_price em relação ao tamanho total do intervalo (ou metade dele)
            # Quanto mais próximo de min_price, mais próximo de 0.0 (crítico)
            distance = (self.cached_price - min_price) / interval_size
        else:
            # Se está acima do centro, medimos a distância até ao max_price
            distance = (max_price - self.cached_price) / interval_size

        # Garante que fica limitado entre 0.0 e 1.0
        return max(0.0, min(1.0, distance))

    async def get_balance(self) -> tuple[float | None, float, bool]:
        position = await self.hl_exchange.get_open_position(self.symbol)
        balance = await self.hl_exchange.get_available_balance()
        unrealized_pnl = 0.0
        is_position = False
        if position:
            unrealized_pnl = position.unrealizedPnl
            is_position = True

        return unrealized_pnl, balance, is_position

    async def calculate_dynamic_range_width(self, limit=30, lookback=14, buffer=0.0) -> float:
        ohlcv = await self.hl_exchange.get_ohlcv(self.symbol, limit=limit)
        range_percentage_raw = IndicatorsUtils.calculate_channel_width(ohlcv, lookback=lookback)
        print(range_percentage_raw)
        if buffer > 0:
            range_percentage_raw = range_percentage_raw * (1 + (buffer * 2))
        print(range_percentage_raw)
        return range_percentage_raw
        # return 0.04

    async def adjust_balance(self, capital_amount: float, dex_price: float) -> tuple[
        float, float]:
        return await self.hl_exchange.get_perfect_quantities(capital_amount, dex_price, self.symbol)

    async def is_market_turbulent_(self, threshold=0.005) -> bool:
        # Obtém o dataframe
        ohlcv = await self.hl_exchange.get_ohlcv(self.symbol, limit=1)

        # Extraímos o valor usando .iloc[-1] (a última linha)
        # Isto garante que obténs o valor bruto (float) e não uma Series com índice
        high = float(ohlcv['high'].iloc[-1])
        low = float(ohlcv['low'].iloc[-1])

        amplitude = (high - low) / low

        is_turbulent = amplitude > threshold
        return is_turbulent

    async def is_market_turbulent(self, threshold=0.005) -> tuple[bool, DirectionMarket]:
        """
        Verifica se o mercado está turbulento e determina a direção dominante do candle.
        Retorna: (is_turbulent: bool, direction: str) onde direction pode ser 'UP', 'DOWN' ou 'NEUTRAL'.
        """
        # Obtém o dataframe
        ohlcv = await self.hl_exchange.get_ohlcv(self.symbol, limit=1)

        # Extraímos os valores usando .iloc[-1] (a última linha)
        high = float(ohlcv['high'].iloc[-1])
        low = float(ohlcv['low'].iloc[-1])
        open_price = float(ohlcv['open'].iloc[-1])
        close_price = float(ohlcv['close'].iloc[-1])

        amplitude = (high - low) / low
        is_turbulent = amplitude > threshold

        # Determina a direção com base no sentido do candle (Close vs Open)
        if close_price > open_price:
            direction = DirectionMarket.UP
        elif close_price < open_price:
            direction = DirectionMarket.DOWN
        else:
            direction = DirectionMarket.NEUTRAL

        return is_turbulent, direction

    async def calculate_hedge_size_based_on_alpha(self, current_price: float, capital_amount: float, ) -> float:
        # Buscar os últimos 14 candles de forma assíncrona
        ohlcv = await self.hl_exchange.get_ohlcv(self.symbol, limit=14)

        # Assumindo que o DataFrame tem uma coluna chamada 'close'
        recent_closes = ohlcv['close'].tail(5)  # ou usar os 14 todos, ex: ohlcv['close']
        sma_recent = recent_closes.mean()

        if current_price > sma_recent:
            # Mercado acima da média recente -> tendência de alta de curto prazo
            return capital_amount * 0.80
        else:
            # Mercado abaixo da média recente -> tendência de baixa
            return capital_amount * 0.90

    async def check_rsi_condition(self, period=14) -> RsiResponse:
        ohlcv = await self.hl_exchange.get_ohlcv(self.symbol, limit=period + 100)
        return IndicatorsUtils.check_rsi_condition(ohlcv, period)
