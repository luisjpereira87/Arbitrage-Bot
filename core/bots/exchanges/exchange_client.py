import asyncio
import logging
from abc import ABC
from typing import Any

import ccxt.async_support as ccxt
import pandas as pd

from core.bots.exchanges.exchange_base import ExchangeBase
from core.dclass.open_position_dclass import OpenPosition
from core.dclass.opened_order_dclass import OpenedOrder
from core.dclass.prices_dclass import Prices
from core.dclass.signal_enum import Signal


class ExchangeClient(ExchangeBase, ABC):
    def __init__(self, exchange: ccxt.Exchange, wallet_address):
        super().__init__()
        self.exchange = exchange
        self.wallet_address = wallet_address

        self._lighter_nonce = None
        self._nonce_lock = asyncio.Lock()
        self._order_lock = asyncio.Lock()

        self.realtime_exposure = {}

        self.account_index_lighter = 729593
        self.api_key_index_lighter = 254

        # print("AQUIII", self.exchange.options.get('api_secret'))
        if "lighter" in str(self.exchange.id).lower():
            # print("AQUIII", self.exchange.options.get('accountIndex'))
            # print("AQUIII", self.exchange.options.get('apiKeyIndex'))
            # Configurações estritas globais exigidas pela Lighter
            # self.exchange.options['accountIndex'] = self.account_index_lighter
            # self.exchange.options['apiKeyIndex'] = self.api_key_index_lighter
            self.exchange.options['builderFee'] = False
            self.exchange.options['approvedBuilderFee'] = True

            # 🎯 A MÁGICA: Substituímos o método do CCXT permanentemente aqui no init!
            # A partir deste momento, sempre que o CCXT precisar de um nonce, ele chama a nossa função robusta
            self.exchange.fetch_nonce = self._custom_fetch_nonce_lighter

    def get_name(self):
        return "hyperliquid"

    async def load_markets(self) -> dict | None:
        try:
            # fetch_ticker no CCXT para Hyperliquid retorna bid, ask e last
            return await self.exchange.load_markets()
        except Exception as e:
            logging.error(f"⚠️ Erro ao obter mercados: {e}")
            return None

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

    async def watch_available_balance(self) -> float:
        try:
            balance = await self.exchange.watch_balance(params={'user': self.wallet_address})
            return balance['total']['USDC']  # type: ignore
        except Exception as e:
            logging.error(f"Erro ao buscar saldo: {e}")
            raise

    async def cancel_all_orders(self, symbol: str = ''):
        async with self._order_lock:
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

    async def get_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 14) -> pd.DataFrame:
        try:
            # A fetch_ohlcv da CCXT retorna uma lista de listas: [timestamp, open, high, low, close, volume]
            data = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

            if not data:
                logging.warning("Nenhum dado de candle retornado.")
                return pd.DataFrame()

            # Converte para DataFrame
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            # Converte o timestamp para formato legível (opcional, mas recomendado)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

            return df

        except Exception as e:
            logging.error(f"Erro ao obter lista de candles: {e}")
            # Retorna um DataFrame vazio para não quebrar o resto do bot
            return pd.DataFrame()

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

    async def watch_prices(self, pair: str) -> (Prices | None):
        try:
            order_book = await self.exchange.watch_order_book(pair)

            # 🛡️ VALIDAÇÃO DE LIQUIDEZ CRÍTICA
            # Se as listas de bids ou asks estiverem vazias, o livro está morto
            if not order_book.get("bids") or not order_book.get("asks"):
                # Lançamos o IndexError de propósito para ser apanhado na Blacklist acima
                raise IndexError("Livro de ordens vazio na exchange (Falta de liquidez)")

            bid = order_book["bids"][0][0]
            ask = order_book["asks"][0][0]
            last = order_book.get("last", bid)

            return Prices(bid, ask, last)

        except IndexError as ie:
            # Deixa o IndexError subir limpo para o fetch_exchange_prices ativar a Blacklist
            raise ie

        except Exception as e:
            # Outros erros (ex: timeouts de rede, problemas de conexão)
            logging.error(f"⚠️ Erro de rede/conexão ao obter preços ({pair}): {e}")
            return None

    async def get_multiple_prices(self, pairs: list[str]) -> (dict[str, Prices] | None):
        try:
            # fetch_ticker no CCXT para Hyperliquid retorna bid, ask e last
            tickers = await self.exchange.fetch_tickers(pairs)

            results = {}
            for symbol, ticker in tickers.items():
                # Criamos um objeto Prices para cada par encontrado
                results[symbol] = Prices(
                    bid=ticker.get('bid'),
                    ask=ticker.get('ask'),
                    last=ticker.get('last')
                )
            return results

        except Exception as e:
            logging.error(f"⚠️ Erro ao obter preços ({pairs}): {e}")
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
        async with self._order_lock:
            logging.info(
                f"🧾 Params finais para create_order: symbol={symbol}, type=market, side={side}, amount={entry_amount}, price={price_ref}")
            try:

                params: dict[str, Any] = {}

                await self.exchange.set_margin_mode("isolated", symbol, {'leverage': leverage})

                logging.info(
                    f"🧾 Params finais para create_order: symbol={symbol}, type=market, side={side}, amount={entry_amount}, price={price_ref}")

                logging.info(f"Enviando ordem market ({side})")

                entry_amount = float(self.exchange.amount_to_precision(symbol, entry_amount))
                precise_price = float(self.exchange.price_to_precision(symbol, price_ref))

                logging.info(
                    f"🧾 Params finais para create_order: symbol={symbol}, type=market, side={side}, amount={entry_amount}, price={price_ref}, params=")

                logging.info(f"Enviando ordem market ({side}) com params: ")

                params['slippage'] = 0.01
                if "lighter" in str(self.exchange.id).lower():
                    params['integrator_account_index'] = 0
                    params['integrator_taker_fee'] = 0  # ✨ A chave que faltava aqui!
                    params['integrator_maker_fee'] = 0  # Prevenção: Próxima provável chave
                    params[
                        'integrator_fee_recipient'] = "0x0000000000000000000000000000000000000000"  # Endereço nulo padrão

                slippage_factor = 0.015

                if side == Signal.BUY:
                    execution_price = price_ref * (1 + slippage_factor)
                else:
                    execution_price = price_ref * (1 - slippage_factor)

                execution_price = float(self.exchange.price_to_precision(symbol, execution_price))
                order = await self.exchange.create_order(
                    symbol=symbol,
                    type='limit',
                    side=side.value,  # type: ignore
                    amount=entry_amount,
                    price=execution_price,
                    params=params
                )
                raw_price = order.get('price')  # type: ignore
                final_price = float(raw_price) if (
                        raw_price is not None and str(raw_price).strip() != '') else price_ref
                logging.info(
                    f"✅ Ordem criada: id={order.get('id')}, side={order.get('side')}, amount={order.get('amount')}, price={order.get('price')}")  # type: ignore

                return OpenedOrder(str(order.get('id') or ""), None, None, None, symbol, None,
                                   str(order.get('side') or ""),
                                   final_price, order.get('amount'), False, None)  # type: ignore

            except Exception as e:
                logging.error(f"Erro ao criar ordem de entrada: {e}")
                raise

    async def open_new_position(self, symbol: str, leverage: float, signal: Signal, capital_amount: float,
                                price_ref: (float | None) = None) -> (
            OpenedOrder | None):

        if price_ref is None:
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
        async with self._order_lock:
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

                params: dict[str, Any] = {}
                params['reduceOnly'] = True
                if "lighter" in str(self.exchange.id).lower():
                    params['integrator_account_index'] = 0
                    params['integrator_taker_fee'] = 0  # ✨ A chave que faltava aqui!
                    params['integrator_maker_fee'] = 0  # Prevenção: Próxima provável chave
                    params[
                        'integrator_fee_recipient'] = "0x0000000000000000000000000000000000000000"  # Endereço nulo padrão

                slippage_factor = 0.015

                # 3. Inverter o lado para o fecho e calcular o preço de proteção
                if side == Signal.BUY:
                    # A posição original era COMPRA -> Temos de VENDER para fechar.
                    # Aceitamos vender até 1.5% ABAIXO do Bid atual para limpar o livro.
                    execution_price = price * (1 - slippage_factor)
                else:
                    # A posição original era VENDA -> Temos de COMPRAR para fechar.
                    # Aceitamos comprar até 1.5% ACIMA do Ask atual para limpar o livro.
                    execution_price = price * (1 + slippage_factor)

                execution_price = float(self.exchange.price_to_precision(symbol, execution_price))
                amount = float(self.exchange.amount_to_precision(symbol, amount))

                # Não enviar preço em ordens market (exchange pode rejeitar)
                order = await self.exchange.create_order(
                    symbol,
                    'market',
                    side.value,  # type: ignore
                    amount,
                    price,
                    params=params
                )
                logging.info(f"✅ Ordem de fechamento enviada: {order.get('info')}")  # type: ignore
                return order
            except Exception as e:
                logging.error(f"❌ Erro ao fechar posição: {e}")
                raise

    async def _custom_fetch_nonce_lighter(self, *args, **kwargs) -> (int | None):
        """
        Método robusto que substitui o fetch_nonce do CCXT.
        Garante thread-safety (Lock) e decide se vai à API ou se incrementa em memória.
        """
        async with self._order_lock:
            if self._lighter_nonce is None:
                # 1. Tenta ir buscar primeiro às opções configuradas no main.py
                account_index = self.exchange.options.get('accountIndex')
                api_key_index = self.exchange.options.get('apiKeyIndex')

                # 🪐 RESOLUÇÃO DIRETA E SEM ERROS DO URL:
                urls_config = getattr(self.exchange, 'urls', {})
                api_url = urls_config.get('api', {}).get('public', '') or urls_config.get('www', '')

                is_sandbox = getattr(self.exchange, 'isSandboxMode', False)

                if is_sandbox or "testnet" in api_url.lower():
                    base_url = "https://testnet.zklighter.elliot.ai"
                    logging.warning("⚠️ [Lighter Engine] A apontar para o ambiente de TESTNET.")
                else:
                    # 🔥 FIXADO APÓS VALIDAÇÃO: O URL real de produção da Lighter Mainnet
                    base_url = "https://mainnet.zklighter.elliot.ai"
                    logging.info(f"⚡ [Lighter Engine] A apontar para o ambiente de MAINNET: {base_url}")

                url = f"{base_url}/api/v1/nextNonce?account_index={account_index}&api_key_index={api_key_index}"

                try:
                    logging.info(f"📡 [Lighter Engine] Cache vazia. Sincronizando nonce via URL Resolvido: {url}")
                    response = await self.exchange.fetch(url, method='GET')

                    # Saca o nonce da resposta da API
                    nonce = response.get('nonce', response.get('next_nonce', 0))
                    self._lighter_nonce = int(nonce)
                    logging.info(f"🟢 [Lighter Engine] Nonce sincronizado com sucesso: {self._lighter_nonce}")

                except Exception as e:
                    logging.error(f"❌ [Lighter Engine] Falha ao sincronizar nonce na API: {e}")
                    # Plano B de emergência para não trancar o arranque do bot inteiro (Hyperliquid)
                    logging.warning("⚠️ [Lighter Engine] Forçando Nonce inicial = 1 para ignorar bloqueio.")
                    self._lighter_nonce = 1
            else:
                self._lighter_nonce += 1
                logging.debug(f"⚡ [Lighter Engine] Nonce incrementado localmente em memória: {self._lighter_nonce}")

            return self._lighter_nonce

    async def validate_lighter_client(self):
        if "lighter" not in str(self.exchange.id).lower():
            return True

        try:
            await self.exchange.load_markets()

            # 1. O que definimos nas options
            opt_acc = str(self.exchange.options.get('accountIndex', ''))
            opt_api = str(self.exchange.options.get('apiKeyIndex', ''))

            # 2. O que o CCXT vai usar (o que vem do handle)
            handle_acc = getattr(self.exchange, 'handle_account_index', None)
            handle_api = getattr(self.exchange, 'handle_api_key_index', None)
            real_acc = None
            real_api = None
            if handle_acc:
                raw_acc = await handle_acc({}, 'createOrder', 'accountIndex', 'account_index')
                data_to_filter = str(raw_acc) if raw_acc is not None else ""
                real_acc = "".join(filter(lambda x: x.isdigit(), data_to_filter))

            if handle_api:
                raw_api = handle_api({}, 'loadAccount', 'apiKeyIndex', 'api_key_index')
                data_to_filter = str(raw_api) if raw_api is not None else ""
                real_api = "".join(filter(lambda x: x.isdigit(), data_to_filter))

            # 3. Comparação de integridade
            if opt_acc != real_acc or opt_api != real_api:
                logging.error(
                    f"❌ MISMATCH DE CONFIGURAÇÃO! Options: Acc={opt_acc}/API={opt_api} vs CCXT: Acc={real_acc}/API={real_api}")
                return False

            if not real_acc or not real_api:
                logging.error("❌ Índices vazios detectados!")
                return False

            logging.info(f"✅ Integridade validada: Acc={real_acc}, API={real_api}")
            return True
        except Exception as e:
            logging.error(f"❌ Erro na validação: {e}")
            return False
