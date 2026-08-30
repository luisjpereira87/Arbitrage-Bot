import asyncio
import logging
from abc import ABC
from typing import Any

import ccxt.async_support as ccxt
import lighter
import pandas as pd
from ccxt.base.types import OrderType, OrderSide, Num

from core.bots.exchanges.exchange_base import ExchangeBase
from core.dclass.open_position_dclass import OpenPosition
from core.dclass.opened_order_dclass import OpenedOrder
from core.dclass.prices_dclass import Prices
from core.dclass.signal_enum import Signal


class ExchangeClient(ExchangeBase, ABC):
    def __init__(self, exchange: ccxt.lighter | ccxt.hyperliquid, wallet_address):
        super().__init__()
        self.exchange = exchange
        self.wallet_address = wallet_address

        self._lighter_nonce = None
        self._order_lock = asyncio.Lock()

        self.account_index_lighter = 729593
        self.api_key_index_lighter = 254

        if "lighter" in str(self.exchange.id).lower():
            self.exchange.options['builderFee'] = False
            self.exchange.options['approvedBuilderFee'] = True
            self.exchange.fetch_nonce = self._custom_fetch_nonce_lighter
            self.exchange.create_order = self.create_order_patched

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
        try:
            params: dict[str, Any] = {}

            await self.exchange.set_margin_mode("isolated", symbol, {'leverage': leverage})

            # 1. Aplicar a precisão do CCXT ANTES de qualquer log ou cálculo
            entry_amount = float(self.exchange.amount_to_precision(symbol, entry_amount))
            precise_price = float(self.exchange.price_to_precision(symbol, price_ref))

            slippage_factor = 0.015
            if side == Signal.BUY:
                execution_price = price_ref * (1 + slippage_factor)
            else:
                execution_price = price_ref * (1 - slippage_factor)

            execution_price = float(self.exchange.price_to_precision(symbol, execution_price))

            logging.info(
                f"🧾 Params finais para create_order: symbol={symbol}, type=limit, side={side}, amount={entry_amount}, price={execution_price}")

            if "lighter" in str(self.exchange.id).lower():
                params['integrator_account_index'] = 0
                params['integrator_taker_fee'] = 0
                params['integrator_maker_fee'] = 0
                params['integrator_fee_recipient'] = "0x0000000000000000000000000000000000000000"

            order = await self.exchange.create_order(
                symbol=symbol,
                type='limit',
                side='buy' if str(side.value).lower() == 'buy' else 'sell',
                amount=entry_amount,
                price=execution_price,
                params=params
            )

            raw_price = order.get('price')
            final_price = float(raw_price) if (raw_price is not None and str(raw_price).strip() != '') else price_ref

            logging.info(
                f"✅ Ordem criada: id={order.get('id')}, side={order.get('side')}, amount={order.get('amount')}, price={order.get('price')}")

            return OpenedOrder(str(order.get('id') or ""), None, None, None, symbol, None,
                               str(order.get('side') or ""),
                               final_price, order.get('amount'), False, None)

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
            execution_price = price
            if "lighter" in str(self.exchange.id).lower():
                params['integrator_account_index'] = 0
                params['integrator_taker_fee'] = 0  # ✨ A chave que faltava aqui!
                params['integrator_maker_fee'] = 0  # Prevenção: Próxima provável chave
                params[
                    'integrator_fee_recipient'] = "0x0000000000000000000000000000000000000000"  # Endereço nulo padrão

                slippage_factor = 0.03

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
                'buy' if str(side.value).lower() == 'buy' else 'sell',  # type: ignore
                amount,
                execution_price,
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

    async def sign_create_order_with_sdk(self, private_key: str, account_index: int, api_key_index: int,
                                         safe_order: dict):
        """
        Gera a assinatura criptográfica utilizando a SDK oficial da Lighter em Python puro,
        evitando a dependência de ficheiros .so/.dylib via ctypes.
        """
        try:
            # Determinar o URL base do endpoint (ex: testnet ou mainnet conforme a tua config)
            base_url = getattr(self.exchange, 'options', {}).get('url', 'https://mainnet.zklighter.elliot.ai')

            # Inicializar o SignerClient oficial da Lighter
            signer_client = lighter.SignerClient(
                url=base_url,
                api_private_keys={int(api_key_index): private_key},
                account_index=int(account_index)
            )

            # Gerar a assinatura chamando o método nativo da SDK para criação de ordens
            # A SDK devolve diretamente o tipo de transação (tx_type) e as informações assinadas (tx_info)
            tx_type, tx_info, tx_hash, err = signer_client.sign_create_order(
                market_index=int(safe_order['market_index']),
                client_order_index=int(safe_order['client_order_index']),
                base_amount=safe_order['base_amount'],
                price=safe_order['avg_execution_price'],  # ou o preço correspondente enviado na ordem
                is_ask=bool(safe_order['is_ask']),
                order_type=int(safe_order['order_type']),
                time_in_force=int(safe_order['time_in_force']),
                reduce_only=bool(safe_order['reduce_only']),
                trigger_price=int(safe_order.get('trigger_price', 0)),
                order_expiry=int(safe_order['order_expiry']),
                nonce=int(safe_order['nonce']),
                api_key_index=int(api_key_index)
            )
            await signer_client.close()
        finally:
            await signer_client.close()
        return tx_type, tx_info

    async def create_order_patched(self, symbol: str, type: OrderType, side: OrderSide, amount: float,
                                   price: Num = None, params={}):
        # 1. Debug de entrada
        logging.info(f"🔍 [PATCH] Iniciando create_order para {symbol} | Lado: {side} | Qtd: {amount}")

        await self.exchange.load_markets()
        accountIndex, params = await self.exchange.handle_account_index(params, 'createOrder', 'accountIndex',
                                                                        'account_index')
        params['accountIndex'] = accountIndex

        market = self.exchange.market(symbol)
        orderRequests = self.exchange.create_order_request(symbol, type, side, amount, price, params)

        order = orderRequests[0]

        logging.info(f"🔍 [DEBUG CCXT ORDER] raw base_amount: {order.get('base_amount')}")
        logging.info(f"🔍 [DEBUG CCXT ORDER] raw price: {order.get('avg_execution_price')}")
        logging.info(f"🔍 [DEBUG CCXT ORDER] market_index: {order.get('market_index')}")
        logging.info(f"🔍 [DEBUG CCXT ORDER] is_ask: {order.get('is_ask')}")

        apiKeyIndex = order['api_key_index']

        # 2. Diagnóstico de Identidade
        logging.info(f"🆔 [PATCH] Identidade: AccountIndex={accountIndex} | ApiKeyIndex={apiKeyIndex}")

        strAccountIndex = self.exchange.number_to_string(accountIndex)
        strApiKeyIndex = self.exchange.number_to_string(apiKeyIndex)

        # 3. Log de carga do signer (Adaptado para SDK Oficial)
        logging.info("🔑 [PATCH] A preparar signer via SDK oficial da Lighter...")

        # Recuperamos a chave privada necessária para o signer oficial
        private_key = self.exchange.get_lighter_private_key(strAccountIndex, strApiKeyIndex)

        # 4. Preparação da assinatura
        if self.exchange.safe_integer(order, 'nonce') is None:
            order['nonce'] = await self.exchange.fetch_nonce(accountIndex, apiKeyIndex)
            logging.info(f"🔢 [PATCH] Nonce obtido: {order['nonce']}")

        try:

            # market = self.exchange.market(symbol)
            # market_info = market.get('info', {})

            safe_order = {
                'market_index': int(order['market_index']),
                'client_order_index': order['client_order_index'],
                'base_amount': order['base_amount'],
                'avg_execution_price': order['avg_execution_price'],
                'is_ask': order['is_ask'],
                'order_type': order['order_type'],
                'time_in_force': order['time_in_force'],
                'reduce_only': order['reduce_only'],
                'trigger_price': order['trigger_price'],
                'order_expiry': order['order_expiry'],
                'integrator_account_index': order['integrator_account_index'],
                'integrator_taker_fee': order['integrator_taker_fee'],
                'integrator_maker_fee': order['integrator_maker_fee'],
                'nonce': int(order['nonce']),
                'api_key_index': int(apiKeyIndex),
                'account_index': int(accountIndex),
                'symbol': symbol,
            }

            logging.info("✍️ [PATCH] Assinando transação com a SDK oficial da Lighter...")

            # --- SUBSTITUIÇÃO DO CTYPES PELA SDK OFICIAL ---
            tx_type, tx_info = await self.sign_create_order_with_sdk(
                private_key=private_key,
                account_index=int(accountIndex),
                api_key_index=int(apiKeyIndex),
                safe_order=safe_order
            )
            # -----------------------------------------------

            logging.info("✨ [PATCH] Assinatura realizada com sucesso via SDK!")

        except Exception as e:
            logging.error(f"💥 [PATCH] ERRO NA ASSINATURA: {str(e)}")
            logging.error(f"📍 [PATCH] Estado no momento do erro: Acc={accountIndex}, ApiKey={apiKeyIndex}")
            raise e

        request = {'tx_type': tx_type, 'tx_info': tx_info}
        response = await self.exchange.publicPostSendTx(request)
        logging.info(f"📥 [PATCH] Resposta da API recebida: {response}")

        combined_data = self.exchange.deep_extend(response, order)
        parsed_order = self.exchange.parse_order(combined_data, market)

        if response.get('tx_hash'):
            parsed_order['id'] = response.get('tx_hash')
        return parsed_order

    async def adjust_balance(self, capital_amount: float, dex_price: float, symbol: str) -> float:
        try:
            # 1. Garantir que os mercados estão carregados

            # print(self.exchange.markets(symbol))
            """
            if symbol not in self.exchange.markets:
                logging.warning(f"⚠️ Par {symbol} não carregado. A tentar usar valor bruto.")
                return capital_amount
            """
            await self.exchange.load_markets()

            # 2. Calcular quantidade bruta de tokens (Ex: 24.85 / 50.24 = 0.49462)
            raw_qty = capital_amount / dex_price

            adjusted_qty = raw_qty * dex_price
            # 3 e 4. Deixar o CCXT tratar o arredondamento de forma nativa e segura
            # O método 'amount_to_precision' da exchange sabe EXATAMENTE como a HL quer o número.
            # Forçamos a conversão para float para podermos fazer contas matemáticas a seguir.
            clean_qty = float(self.exchange.amount_to_precision(symbol, raw_qty))

            # 5. Calcular o custo em USD para comprar essa quantidade com a margem de 0.3%
            adjust_balance = clean_qty * dex_price * 1.003

            # 6. Validação de teto de gastos: se a margem de 0.3% ultrapassou o teu slot disponível
            if adjust_balance > capital_amount:
                logging.warning(f"⚠️ Ajuste excedeu balance original para {symbol}. Recalculando...")

                # Em vez de inventar o 'step' com o factor, usamos a precisão da própria exchange
                # No CCXT, a variação mínima (tick size do amount) está em market['limits']['amount']['min'] ou market['precision']['amount']
                # Para evitar bugs, vamos apenas reduzir 1% da quantidade para garantir que cabe no orçamento
                clean_qty = float(self.exchange.amount_to_precision(symbol, clean_qty * 0.98))
                adjust_balance = clean_qty * dex_price * 1.003

            # 7. Segurança máxima: Se depois de tudo a quantidade for zero, não podemos operar
            if clean_qty <= 0:
                logging.warning(
                    f"🚫 [PRECISÃO {symbol}] Quantidade calculada é zero. Saldo insuficiente para o preço do token.")
                return 0.0

            logging.info(
                f"🎯 [PRECISÃO {symbol}] Qtd: {clean_qty} | "
                f"USD Original: ${capital_amount:.2f} | USD Ajustado: ${adjust_balance:.4f}"
            )

            return adjust_balance

        except Exception as e:
            logging.error(f"💥 Erro no adjust_balance para {symbol}: {e}")
            return 0.0

    async def get_perfect_quantities(self, capital_usd: float, dex_price: float, symbol: str) -> tuple[
        float, float]:
        await self.exchange.load_markets()

        # 1. Quantidade teórica bruta
        raw_qty = capital_usd / dex_price

        # 2. Arredondar usando a precisão da Exchange (HL)
        # Por padrão, o CCXT costuma arredondar para baixo (floor) na maioria das exchanges,
        # mas para sermos ultra-seguros, podemos validar:
        clean_qty = float(self.exchange.amount_to_precision(symbol, raw_qty))

        # 3. Verificação de segurança: Se, por algum motivo de arredondamento 'para cima',
        # o custo ultrapassar o capital, subtraímos o 'tick size' mínimo
        actual_cost_usd = clean_qty * dex_price

        if actual_cost_usd > capital_usd:
            precision = self.exchange.markets[symbol]['precision']['amount']
            clean_qty = clean_qty - precision
            actual_cost_usd = clean_qty * dex_price

        logging.info(f"⚖️ [PRECISÃO] Ajustado para {clean_qty} SOL | Custo Real: ${actual_cost_usd:.4f}")

        return clean_qty, actual_cost_usd
