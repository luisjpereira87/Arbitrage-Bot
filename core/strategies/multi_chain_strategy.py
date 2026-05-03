import logging
import time

import ccxt.async_support as ccxt

from core.bots.exchanges.exchange_client import ExchangeClient
from core.config.properties_multi import PropertiesMulti
from core.dclass.dex_opportunity_dclass import DexOpportunity
from core.dclass.profit_info_dclass import ProfitInfo
from core.dclass.signal_enum import Signal
from core.dclass.watched_pair_dclass import WatchedPair
from core.pools.pool_finder import PoolFinder
from core.strategies.arbitrage_base import ArbitrageBase
from core.web3.wallet_base import WalletBase


class MultiChainStrategy(ArbitrageBase):
    def __init__(self, web3_manager, properties: PropertiesMulti, pool_finder: PoolFinder, wallet: WalletBase,
                 capital_amount: float):
        super().__init__(web3_manager, properties.CONFIG)
        self.watched_pairs = None
        self.finder = pool_finder
        # self.min_profit = 0.20  # Minimum $ profit to trigger
        self.wallet = wallet
        self.capital = wallet.get_usdc_balance()
        self.config = properties.CONFIG
        # self.min_entry_spread = 2.5
        self.min_usdc_to_trade = 10.0
        self.min_exit_spread = -0.1
        # self.min_amount = 10
        self.active_positions = {}
        self.test = True

        self.hl = ccxt.hyperliquid({
            "walletAddress": properties.WALLET_ADDRESS_HL,
            "privateKey": properties.PRIVATE_KEY_WALLET_HL,
            "enableRateLimit": True,
            "timeout": 10000,
            "testnet": False,
            "options": {"defaultSlippage": 0.01},
        })

        self.exchange = ExchangeClient(self.hl, properties.WALLET_ADDRESS_HL)

        self.init_cache()

    def init_cache(self):
        # --- NOVO: CACHE INICIAL ---
        # Mapeamos logo todas as pools possíveis para os pares que queres vigiar
        all_pools_for_cache = set()
        self.watched_pairs = []
        FEE_TIERS = self.config.fees
        for symbol_a, symbol_b, hl_pair in self.config.multi_chain:

            # 1. Obter endereços
            addr_a = self.config.tokens.get(symbol_a).address
            addr_b = self.config.tokens.get(symbol_b).address
            dec_a = self.config.tokens.get(symbol_a).decimals
            dec_b = self.config.tokens.get(symbol_b).decimals

            # 2. Ordenar alfabeticamente (como a Uniswap faz internamente)
            # t0 será sempre o menor endereço, t1 o maior.
            t0, t1 = sorted([addr_a.lower(), addr_b.lower()])

            # 3. Chamar o finder uma única vez com a ordem correta
            # pools_map = self.finder.get_pools(t0, t1)
            pair_pools = {}
            for fee in FEE_TIERS:
                pool_found = self.finder.get_pools(t0, t1, fee)
                if pool_found:
                    # Em vez de apenas update, vamos renomear a chave para incluir a fee
                    for dex_name, addr in pool_found.items():
                        unique_key = f"{dex_name}_{fee}"  # Ex: UNI_V3_500, UNI_V3_3000
                        pair_pools[unique_key] = addr

            if not pair_pools:
                print(f"⚠️ Nenhuma pool encontrada para {symbol_a}/{symbol_b} em nenhuma fee tier.")

            for addr in pair_pools.values():
                all_pools_for_cache.add(addr.lower())

            # 4. Cálculo do zeroForOne para este par (USDC -> Ativo)
            # Se o endereço do USDC (addr_a) for menor que o do Ativo (addr_b), z4o = True
            z4o = int(addr_a, 16) < int(addr_b, 16)

            # Guardamos os endereços para evitar lookups repetidos no config

            self.watched_pairs.append(
                WatchedPair(addr_a, addr_b, symbol_a, symbol_b, dec_a, dec_b, hl_pair, pair_pools, z4o)
            )

        self.build_pool_cache(list(all_pools_for_cache))

    async def analyze_all_pairs(self):
        # 1. Obter inventário e saldos
        inventory = await self.get_all_balances()
        usdc_dex = inventory.get("USDC", 0.0)
        hl_equity = inventory.get("hl", 0.0)

        # Capital disponível para NOVAS entradas
        usdc_balance = min(usdc_dex, hl_equity)

        # 2. Identificar "Batatas Quentes" (Ativos já em carteira)
        active_tokens = {sym: units for sym, units in inventory.items()
                         if sym not in ["USDC", "hl"] and units > 0.00001}

        logging.info(f"📊 [ESTADO] DEX: {usdc_dex:.2f} USDC | HL: {hl_equity:.2f} USDC | Tokens: {active_tokens}")

        # 3. VERIFICAÇÃO LÓGICA:
        # Se não temos tokens para gerir E o saldo é baixo, então paramos.
        if not active_tokens and usdc_balance < self.min_usdc_to_trade:
            logging.info(
                f"⏳ Modo espera: Saldo insuficiente para novas entradas (${usdc_balance:.2f})."
            )
            return False

        # 4. Se chegámos aqui, ou temos saldo ou temos posições para gerir!
        eth_price = await self.exchange.get_entry_price("ETH/USDC:USDC")  # Usei get_prices em vez de entry_price
        gas_cost_usdc = self.wallet.get_gas_cost_usd(eth_price)

        # 5. Recolher preços em Batch
        all_pool_addrs = []
        for pair in self.watched_pairs:
            all_pool_addrs.extend(pair.pools_map.values())
        current_prices = self.get_quotes_batch(all_pool_addrs)

        # 6. LOOP DE DECISÃO
        for pair in self.watched_pairs:
            price_hl = await self.exchange.get_prices(pair.hl_pair)

            opportunity = self.find_cross_dex_spread(
                pair.addr_a, pair.addr_b,
                pair.symbol_a, pair.symbol_b,
                price_hl.bid, pair.pools_map,
                current_prices, usdc_balance, gas_cost_usdc
            )

            if not opportunity:
                continue

            # O manage_orders agora decide se fecha o que existe ou abre o que falta
            await self.manage_orders(opportunity, pair, active_tokens, inventory, usdc_balance)

        return True

    async def manage_orders(self, opportunity: DexOpportunity, watched_pair: WatchedPair, active_tokens: dict,
                            inventory: dict,
                            usdc_balance: float):

        dex_price = opportunity.price_dex
        hl_price = opportunity.price_hl
        profit = opportunity.profit
        pool_addr = opportunity.pool_addr
        dex_name = opportunity.dex_name
        direction = opportunity.direction
        dex_fee = opportunity.dex_fee
        spread_percent = opportunity.spread

        symbol_a = watched_pair.symbol_a  # USDC
        symbol_b = watched_pair.symbol_b  # Ativo Volátil (WETH, ARB...)
        hl_pair = watched_pair.hl_pair

        if abs(spread_percent) > 20:
            logging.warning(f"🚫 Spread de {spread_percent}% ignorado por segurança (Threshold 20%)")
            return None

        # --- LÓGICA DE EXECUÇÃO / GESTÃO ---

        # CENÁRIO A: Já temos este ativo volátil no contrato (Gestão de Posição)
        if symbol_b in active_tokens:
            units = active_tokens[symbol_b]

            # Busca posição de Short na Hyperliquid
            hl_pos = await self.exchange.get_open_position(hl_pair)

            if hl_pos is None:
                # Caso 1: Temos o token mas NÃO temos o Short (Hedge em falta)
                # Usamos lógica de ENTRADA (is_exit=False) porque vamos abrir uma posição nova na HL
                """
                check_v, min_p, min_s = self.check_viability_dynamic(profit, spread_percent, usdc_balance, dex_fee,is_exit=False)
                """
                logging.info(
                    f"🚀 Token em carteira sem proteção. Abrindo Short na HL para proteger {units} {symbol_b}...")
                await self.execute_entry_sequence(watched_pair, units * dex_price, dex_price, False, pool_addr,
                                                  direction)
                return True
            else:
                # Caso 2: Posição de Arbitragem Completa (Token + Short)
                # IMPORTANTE: Usamos lógica de SAÍDA (is_exit=True)
                check_v, min_p, min_s = self.check_viability_dynamic(profit, spread_percent, usdc_balance, dex_fee,
                                                                     is_exit=True)
                """
                logging.info(
                    f"⚖️ Monitorizando {symbol_b}: Net: ${profit:.4f} (Alvo: ${min_p:.4f}) | Spread: {spread_percent:.2f}% (Alvo: {self.min_exit_spread}%)")
                """
                if check_v:
                    logging.info(f"💰 SAÍDA: {symbol_b} | Lucro: ${profit:.4f} | Spread: {spread_percent:.2f}%")
                    await self.execute_exit_sequence(watched_pair, units, pool_addr, direction)
                    return True
                else:
                    logging.info(
                        f"💎 ATIVO: {units:.4f} {symbol_b} | Lucro Atual: ${profit:.4f} | Spread: {spread_percent:.2f}% | Alvo: {self.min_exit_spread}%")
                    return False

        # CENÁRIO B: Carteira limpa, procurar Novas Entradas
        elif not active_tokens:
            # Usamos lógica de ENTRADA (is_exit=False)
            check_v, min_p, min_s = self.check_viability_dynamic(profit, spread_percent, usdc_balance, dex_fee,
                                                                 is_exit=False)

            if check_v:
                logging.info(
                    f"🎯 Nova oportunidade: {spread_percent:.2f}% em {symbol_b} (Lucro Est: ${profit:.4f}). Executando!")
                await self.execute_entry_sequence(watched_pair, usdc_balance, dex_price, True, pool_addr, direction)
                return True

        return False

    async def execute_entry_sequence(self, pair: WatchedPair, amount_usdc: float, price_dex: float,
                                     is_dex_swap: bool, selected_pool: str, direction: bool):
        """
        1. Swap USDC -> Token (DEX)
        2. Open Short (Hyperliquid)
        """
        logging.info(f"🚀 Iniciando entrada em {pair.symbol_b} com {amount_usdc} USDC")

        if is_dex_swap:
            # Rota: [USDC, TOKEN_VOLATIL]
            tokens_para_swap = [pair.addr_a, pair.addr_b]

            if not pair.pools_map:
                logging.error(f"❌ Nenhuma pool configurada para {pair.symbol_b}")
                return False

            # PASSO 1: Swap na DEX
            # Convertemos o capital em float (ex: 15.0) para Wei de USDC (6 decimais)
            usdc_wei = int(amount_usdc * (10 ** pair.decimal_a))

            logging.info(f"🔄 Executando Swap DEX: {amount_usdc} USDC -> {pair.symbol_b}")
            tx_hash = self.wallet.send_transaction(
                pools_list=[selected_pool],
                dir_list=[direction],
                tokens_list=tokens_para_swap,
                amount_usd=usdc_wei  # Enviamos o valor exato em Wei (int)
            )

            if not tx_hash:
                logging.error("❌ Falha ao enviar transação para a DEX. Abortando entrada.")
                return False

            logging.info(f"✅ Transação DEX enviada! Hash: {tx_hash}")

        # PASSO 2: Hedge na Hyperliquid
        logging.info(f"📉 Abrindo Short na HL para cobrir ${amount_usdc} em {pair.hl_pair}...")
        hl_success = await self.exchange.open_new_position(
            pair.hl_pair,
            1.0,  # Leverage
            Signal.SELL,
            amount_usdc
        )

        if hl_success:
            logging.info(f"🔒 Hedge confirmado na HL para {pair.hl_pair}")

            # Registo em memória para monitorização de lucro
            try:
                hl_pos = await self.exchange.get_open_position(pair.hl_pair)
                price_perp = hl_pos.entry_price if hl_pos else 0.0
                entry_spread = (price_perp - price_dex) / price_dex if price_dex > 0 else 0

                self.active_positions[pair.hl_pair] = ProfitInfo(
                    entry_spread, price_perp, price_dex, amount_usdc,
                    0.001 * amount_usdc, 0.0, time.time(), pair
                )
                logging.info(f"📈 [MEMÓRIA] Posição Registada. Spread: {entry_spread * 100:.4f}%")
            except Exception as e:
                logging.error(f"⚠️ Erro ao registar em memória: {e}")
            return True
        else:
            logging.error(f"🚨 ERRO CRÍTICO: Swap feito na DEX, mas falha ao abrir Short na HL!")
            return False

    async def execute_exit_sequence(self, pair: WatchedPair, units, selected_pool: str, direction: bool):
        """
        1. Close Short (Hyperliquid)
        2. Swap Token -> USDC (DEX)
        """
        logging.info(f"💰 Iniciando fecho de ciclo para {pair.symbol_b}")

        # PASSO 1: Fechar Short na Hyperliquid
        current_position = await self.exchange.get_open_position(pair.hl_pair)

        hl_success = False
        if current_position and abs(float(current_position.size)) > 0:
            hl_size = abs(float(current_position.size))
            logging.info(f"📉 Fechando Short de {hl_size} unidades na HL...")
            await self.exchange.close_position(pair.hl_pair, hl_size, Signal.BUY)
            hl_success = True
        else:
            logging.warning(f"⚠️ Nenhuma posição ativa na HL para {pair.hl_pair}. Prosseguindo para venda na DEX.")
            hl_success = True

        if not hl_success:
            logging.error("❌ Falha crítica ao fechar posição na HL. Abortando venda na DEX.")
            return False

        # PASSO 2: Venda na DEX (Token -> USDC)
        # Consultamos a "Verdade Absoluta" da Blockchain (Saldo Real em Wei)
        units_in_wei = self.wallet.get_token_balance(pair.addr_b)

        if units_in_wei == 0:
            logging.error(f"❌ Erro: Saldo de {pair.symbol_b} no contrato é 0. Nada para vender.")
            return False

        logging.info(f"✅ Saldo real detectado: {units_in_wei} Wei. Vendendo na DEX...")

        # Rota de Saída: [TOKEN_VOLATIL, USDC]
        tokens_para_saida = [pair.addr_b, pair.addr_a]
        direction_exit = not direction

        tx_hash = self.wallet.send_transaction(
            pools_list=[selected_pool],
            dir_list=[direction_exit],
            tokens_list=tokens_para_saida,
            amount_usd=units_in_wei  # Enviamos o saldo real em Wei (int)
        )

        if tx_hash:
            logging.info(f"💵 Ciclo concluído com sucesso! TX: {tx_hash}")
            if pair.hl_pair in self.active_positions:
                del self.active_positions[pair.hl_pair]
            return True
        else:
            logging.error(f"🚨 Alerta: Short fechado na HL, mas falha no Swap da DEX. Tokens presos no contrato!")
            return False

    async def get_all_balances(self):
        # Usamos um Set para endereços já consultados (mais rápido que List)
        processed_addresses = set()
        # Dicionário final: {"USDC": 100.0, "ETH": 0.5, ...}
        balances = {}
        for pair in self.watched_pairs:
            # Verifica Token A (t_in)
            t_in = pair.addr_a
            t_out = pair.addr_b
            if t_in not in processed_addresses:
                raw_in = self.wallet.get_token_balance(token_address=t_in)
                balances[pair.symbol_a] = self.normalize_amount(raw_in, pair.decimal_a)
                processed_addresses.add(t_in)

            if t_out not in processed_addresses:
                raw_out = self.wallet.get_token_balance(token_address=t_out)
                balances[pair.symbol_b] = self.normalize_amount(raw_out, pair.decimal_b)
                processed_addresses.add(t_out)

        balances["hl"] = await self.exchange.get_available_balance()
        return balances

    def normalize_amount(self, raw_amount, decimals):
        """Converte o valor bruto da blockchain para unidades humanas"""
        if raw_amount == 0:
            return 0.0
        return raw_amount / (10 ** decimals)

    def find_cross_dex_spread(self, token_in, token_out, symbol_a, symbol_b, price_hl, pools_map,
                              current_prices, amount_usdc, gas_cost_usdc) -> (DexOpportunity | None):
        best_opportunity = None
        logging.info(f"--- 🔍 Verificando Par: {symbol_a}/{symbol_b} | Pools: {len(pools_map)}")

        # Normalização do capital (corrigido para symbol_a/USDC)
        token_in_data = self.config.tokens.get(symbol_a)
        decimals_in = token_in_data.decimals if token_in_data else 6
        # cap_human = amount_usdc / (10 ** decimals_in)
        cap_human = amount_usdc

        for dex_name, pool_addr in pools_map.items():
            p_addr_l = pool_addr.lower()
            current_price = current_prices.get(p_addr_l) or current_prices.get(pool_addr)

            q1 = self._calculate_quote_local(p_addr_l, token_in, token_out, current_price)
            if q1:
                raw_price_dex, direction, fee_dex_ppm = q1

                price_dex = 1 / raw_price_dex

                logging.info(f"Dex: {dex_name}, Pair: {symbol_a}/{symbol_b}, Price: {price_dex}")

                # Cálculos de lucro e spread
                fee_dex_percent = fee_dex_ppm / 1_000_000
                # Tokens que consegues comprar agora
                tokens_bought = (cap_human * (1 - fee_dex_percent)) / price_dex

                # Valor que recebes ao vender na HL (já descontando abertura e fecho de lá)
                total_recebido_hl = (tokens_bought * price_hl) * (1 - 0.00070)  # 0.00035 * 2

                # Custo total para recuperar o teu USDC na DEX (inclui a taxa de quando venderes o token)
                custo_reverter_dex = (tokens_bought * price_dex) * fee_dex_percent

                # Gás para as duas pontas
                total_gas = gas_cost_usdc * 2

                # LUCRO REAL = (O que ganhas na HL) - (O que gastaste na DEX) - (O que vais gastar para voltar) - (Gás)
                net_profit = total_recebido_hl - cap_human - custo_reverter_dex - total_gas

                spread_percent = ((price_hl / price_dex) - 1) * 100

                # --- A LÓGICA DE COMPARAÇÃO ---

                current_opp = DexOpportunity("MULTI_CHAIN", net_profit, spread_percent, symbol_b, price_dex, price_hl,
                                             pool_addr, dex_name, fee_dex_ppm, direction)

                # Se for a primeira ou se for melhor que a anterior, guarda
                if best_opportunity is None or current_opp.profit > best_opportunity.profit:
                    best_opportunity = current_opp

        # Só retorna depois de verificar TODAS as pools do map
        return best_opportunity

    """
    def check_viability_dynamic(self, net_profit, spread_percent, amount_usdc, fee_dex_ppm):
    
        #Decide se o trade vale a pena com base no capital atual.

        # 1. ROI Alvo (Return on Investment)
        # 0.001 significa que queres ganhar no mínimo 0.1% de lucro limpo sobre os $15
        # Com $15, isto daria $0.015 de lucro mínimo.
        target_roi = 0.001
        min_profit_required = amount_usdc * target_roi

        # 2. Spread de Segurança Dinâmico
        # O spread deve ser superior às taxas totais + uma margem de segurança (Buffer)
        # Buffer de 0.15% serve para cobrir slippage ou micro-oscilações da HL
        fee_dex_percent = fee_dex_ppm / 1_000_000
        fee_hl_percent = 0.00035  # 0.035%

        buffer_seguranca = 0.15
        min_spread_required = (fee_dex_percent * 100) + (fee_hl_percent * 100) + buffer_seguranca

        # 3. LOG de Decisão (Útil para debug no Railway)
        logging.info(f"--- 🧠 Análise Dinâmica ---")
        logging.info(f"Lucro: ${net_profit:.4f} (Min Req: ${min_profit_required:.4f})")
        logging.info(f"Spread: {spread_percent:.2f}% (Min Req: {min_spread_required:.2f}%)")

        # A decisão final (Mantemos o AND por segurança)
        if net_profit >= min_profit_required and spread_percent >= min_spread_required:
            return True, min_profit_required, min_spread_required

        return False, min_profit_required, min_spread_required
    """

    def check_viability_dynamic(self, net_profit, spread_percent, amount_usdc, fee_dex_ppm, is_exit=False):
        # 1. ROI Alvo (Ajustado)
        # Na entrada, buscamos 0.1% de lucro real.
        # Na saída, podemos ser mais flexíveis para não ficar "preso" no trade.
        target_roi = 0.0005 if is_exit else 0.001
        min_profit_required = amount_usdc * target_roi

        # 2. Spread de Segurança Dinâmico
        fee_dex_percent = fee_dex_ppm / 1_000_000
        fee_hl_percent = 0.00035

        # Na entrada (DEX->HL), o buffer protege contra slippage na compra.
        # Na saída (DEX<-HL), o buffer protege contra slippage na venda.
        buffer_seguranca = 0.05 if is_exit else 0.15

        min_spread_required = (fee_dex_percent * 100) + (fee_hl_percent * 100) + buffer_seguranca

        # LOG de Decisão (Útil para debug no Railway)
        logging.info(f"--- 🧠 Análise Dinâmica ---")
        logging.info(f"Lucro: ${net_profit:.4f} (Min Req: ${min_profit_required:.4f})")
        logging.info(f"Spread: {spread_percent:.2f}% (Min Req: {min_spread_required:.2f}%)")

        # 3. Lógica Especial de Saída (Spread Negativo)
        # Se o spread for negativo (DEX mais cara que HL), a arbitragem inverteu totalmente.
        # Isto é um sinal fortíssimo de saída, mesmo que o lucro seja baixo.
        if is_exit and spread_percent <= self.min_exit_spread:
            logging.info(f"🚨 Saída Estratégica: Spread Reverso detectado ({spread_percent}%)")
            return True, 0, 0

        # Decisão
        success = net_profit >= min_profit_required and spread_percent >= (0 if is_exit else min_spread_required)

        return success, min_profit_required, min_spread_required
