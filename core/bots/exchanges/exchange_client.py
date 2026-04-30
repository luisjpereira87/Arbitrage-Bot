import logging
from abc import ABC

import ccxt.async_support as ccxt

from core.bots.exchanges.exchange_base import ExchangeBase
from core.dclass.open_position_dclass import OpenPosition
from core.dclass.opened_order_dclass import OpenedOrder
from core.dclass.prices_dclass import Prices
from core.dclass.signal_enum import Signal


class ExchangeClient(ExchangeBase, ABC):
    def __init__(self, exchange: ccxt.hyperliquid, wallet_address):
        super().__init__()
        self.exchange = exchange
        self.wallet_address = wallet_address

    def get_name(self):
        return "hyperliquid"

    async def print_balance(self):
        try:
            balance = await self.get_available_balance()
            logging.info(f"💰 Saldo total: {balance}")
        except Exception as e:
            logging.error(f"Erro ao buscar saldo: {e}")

    async def print_open_orders(self, symbol: str = ''):
        try:
            params = {'user': self.wallet_address}
            if symbol:
                open_orders = await self.exchange.fetch_open_orders(symbol, params=params)
            else:
                open_orders = await self.exchange.fetch_open_orders(params=params)
            logging.info(f"📘 Ordens abertas para {symbol if symbol else 'todos símbolos'} ({len(open_orders)}):")
            for order in open_orders:
                logging.info(
                    f"  ID: {order.get('id')}, Side: {order.get('side')}, Price: {order.get('price')}, Amount: {order.get('amount')}, Status: {order.get('status')}")
        except Exception as e:
            logging.error(f"Erro ao buscar ordens abertas: {e}")

    async def get_available_balance(self) -> float:
        try:
            balance = await self.exchange.fetch_balance(params={'user': self.wallet_address})
            return balance['total']['USDC']  # type: ignore
        except Exception as e:
            logging.error(f"Erro ao buscar saldo: {e}")
            raise

    async def cancel_all_orders(self, symbol: str = ''):
        try:
            params = {'user': self.wallet_address}
            if symbol:
                open_orders = await self.exchange.fetch_open_orders(symbol, params=params)
            else:
                open_orders = await self.exchange.fetch_open_orders(params=params)

            for order in open_orders:
                await self.exchange.cancel_order(order['id'], order['symbol'])  # type: ignore
            logging.info(f"🔁 Todas as ordens foram canceladas para {symbol if symbol else 'todos símbolos'}.")
        except Exception as e:
            logging.error(f"Erro ao cancelar ordens: {e}")

    async def get_entry_price(self, symbol: str) -> float:
        try:
            # ticker = await self.exchange.fetch_ticker(symbol)
            ohlcv = await self.exchange.fetch_ohlcv(symbol, '1m', 1)
            if ohlcv and len(ohlcv) > 0:
                return float(ohlcv[-1][4])  # Retorna o 'Close' do candle mais recente

            # Caminho Alternativo: API respondeu mas a lista está vazia
            logging.warning(f"⚠️ Lista OHLCV vazia para {symbol}")
            return 0.0
        except Exception as e:
            logging.error(f"Erro ao obter preço de entrada: {e}")
            return 0

    async def get_prices(self, pair: str) -> (Prices | None):
        try:
            # fetch_ticker no CCXT para Hyperliquid retorna bid, ask e last
            ticker = await self.exchange.fetch_ticker(pair)
            return Prices(ticker['bid'], ticker['ask'], ticker['last'])
        except Exception as e:
            logging.error(f"⚠️ Erro ao obter preços ({pair}): {e}")
            return None

    async def get_open_position(self, symbol: str = '') -> (OpenPosition | None):
        try:
            positions = await self.exchange.fetch_positions(params={'user': self.wallet_address})
            for pos in positions:
                if pos["symbol"] == symbol and float(pos.get('contracts', 0)) > 0:  # type: ignore

                    size = float(pos['contracts'])  # type: ignore
                    entry_price = pos.get('entryPrice') or pos.get('entry_price') or pos.get('averagePrice') or 0.0
                    _id = pos.get('id') or pos.get('info', {}).get('order', {}).get('oid')
                    unrealized_pnl = pos.get('unrealizedPnl') or pos.get('unrealizedPnl')
                    funding_rate = await self.exchange.fetch_funding_rate(symbol)

                    signal = 'hold'
                    if pos['side'] == 'long':
                        signal = 'buy'
                    elif pos['side'] == 'short':
                        signal = 'sell'

                    return OpenPosition(signal, size, entry_price, _id,
                                        size * entry_price, None, None, unrealized_pnl, funding_rate)  # type: ignore

        except Exception as e:
            logging.error(f"Erro ao obter posições abertas: {e}")
        return None

    @staticmethod
    def calculate_entry_amount(price_ref: float, capital_amount: float) -> float:
        """
        Calcula a quantidade a ser usada na entrada com base no capital disponível e no preço de referência.

        Args:
            price_ref (float): preço atual de referência do ativo.
            capital_amount (float): valor do capital disponível para trade (já calculado, ex: 1000 USD).

        Returns:
            float: quantidade de contratos ou tokens para a entrada.
        """
        try:
            if price_ref <= 0 or capital_amount <= 0:
                logging.warning(f"🚫 Preço de referência ({price_ref}) ou capital inválido ({capital_amount}).")
                return 0.0

            quantity = capital_amount / price_ref

            # Impede ordens abaixo de $10

            min_order_value = 10
            if quantity * price_ref < min_order_value:
                logging.warning(f"🚫 Ordem abaixo do mínimo de $10: {quantity * price_ref:.2f}")
                return 0.0

            # Opcional: ajuste para múltiplos mínimos
            # min_qty = 0.001
            # quantity = max(min_qty, math.floor(quantity / min_qty) * min_qty)

            return round(quantity, 6)

        except Exception as e:
            logging.error(f"Erro ao calcular quantidade de entrada: {e}")
            return 0.0

    async def place_entry_order(self, symbol: str, leverage: float, entry_amount: float, price_ref: float,
                                side: Signal) -> OpenedOrder:

        logging.info(
            f"🧾 Params finais para create_order: symbol={symbol}, type=market, side={side}, amount={entry_amount}, price={price_ref}")
        try:
            await self.exchange.set_margin_mode("isolated", symbol, {'leverage': leverage})

            logging.info(
                f"🧾 Params finais para create_order: symbol={symbol}, type=market, side={side}, amount={entry_amount}, price={price_ref}")

            logging.info(f"Enviando ordem market ({side})")

            entry_amount = float(self.exchange.amount_to_precision(symbol, entry_amount))
            precise_price = float(self.exchange.price_to_precision(symbol, price_ref))

            logging.info(
                f"🧾 Params finais para create_order: symbol={symbol}, type=market, side={side}, amount={entry_amount}, price={price_ref}, params=")

            logging.info(f"Enviando ordem market ({side}) com params: ")

            order = await self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=side.value,  # type: ignore
                amount=entry_amount,
                price=price_ref,
                params=None
            )
            raw_price = order.get('price')  # type: ignore
            final_price = float(raw_price) if (raw_price is not None and str(raw_price).strip() != '') else price_ref
            logging.info(
                f"✅ Ordem criada: id={order.get('id')}, side={order.get('side')}, amount={order.get('amount')}, price={order.get('price')}")  # type: ignore

            return OpenedOrder(str(order.get('id') or ""), None, None, None, symbol, None, str(order.get('side') or ""),
                               final_price, order.get('amount'), False, None)  # type: ignore

        except Exception as e:
            logging.error(f"Erro ao criar ordem de entrada: {e}")
            raise

    async def open_new_position(self, symbol: str, leverage: float, signal: Signal, capital_amount: float) -> (
            OpenedOrder | None):
        prices = await self.get_entry_price(symbol)

        if prices is None or prices <= 0:
            raise ValueError("❌ Invalid reference price (None or <= 0)")

        price_ref = prices
        entry_amount = self.calculate_entry_amount(price_ref, capital_amount)
        side = signal

        logging.info(
            f"{symbol}: Sending entry order {side} with qty {entry_amount} at price {price_ref}"
        )

        """
        min_order_value = 10
        if entry_amount * price_ref < min_order_value:
            logging.warning(
                f"🚫 Order below $10 minimum: {entry_amount * price_ref:.2f}"
            )
        """
        return await self.place_entry_order(symbol, leverage, entry_amount, price_ref, side)

    async def close_position(self, symbol: str, amount: float, side: Signal):
        """
        Fecha posição com ordem de mercado. Usa 'side' atual para calcular o lado oposto (close_side).
        """

        logging.info(f"[DEBUG] Tentando fechar posição: symbol={symbol}, side={side.value}, amount={amount}")

        try:
            orderbook = await self.exchange.fetch_order_book(symbol)

            if side == Signal.BUY:
                price = orderbook['asks'][0][0] if orderbook['asks'] else None
            else:
                price = orderbook['bids'][0][0] if orderbook['bids'] else None

            logging.info(f"[DEBUG] Preço usado para ordem market: {price}")

            if price is None:
                raise Exception("⚠️ Livro de ofertas vazio para fechamento.")

            # Não enviar preço em ordens market (exchange pode rejeitar)
            order = await self.exchange.create_order(
                symbol,
                'market',
                side.value,  # type: ignore
                amount,
                price,
                params={'reduceOnly': True}
            )
            logging.info(f"✅ Ordem de fechamento enviada: {order.get('info')}")  # type: ignore

        except Exception as e:
            logging.error(f"❌ Erro ao fechar posição: {e}")
            raise
