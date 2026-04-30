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
        self.min_profit = 0.20  # Minimum $ profit to trigger
        self.wallet = wallet
        self.capital = capital_amount
        self.config = properties.CONFIG
        self.min_entry_spread = 2.5
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
        FEE_TIERS = [500, 3000, 10000]
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
        # 1. Obter inventário e saldos (Cache de Memória)
        inventory = await self.get_all_balances()
        usdc_balance = inventory.get("USDC", 0.0)
        hl_equity = inventory.get("hl", 0.0)

        # 2. Identificar se temos algum "Token Volátil" (Batata Quente)
        # Filtramos tokens que não são USDC nem o saldo da HL e têm saldo > 0
        active_tokens = {sym: units for sym, units in inventory.items()
                         if sym not in ["USDC", "hl"] and units > 0.00001}

        if usdc_balance <= self.min_usdc_to_trade and hl_equity <= self.min_usdc_to_trade:
            logging.info(
                f"🚫 usdc na carteira: {usdc_balance} < {self.min_usdc_to_trade} | usdc na Hyperliquid: {hl_equity} < {self.min_usdc_to_trade}")
            return False

        # 3. Recolher preços em Batch (Eficiência RPC)
        all_pool_addrs = []

        for pair in self.watched_pairs:
            all_pool_addrs.extend(pair.pools_map.values())
        current_prices = self.get_quotes_batch(all_pool_addrs)

        # 4. LOOP DE DECISÃO
        for pair in self.watched_pairs:
            price_hl = await self.exchange.get_prices(pair.hl_pair)
            opportunity = self.find_cross_dex_spread(
                pair.addr_a,
                pair.addr_b,
                pair.symbol_a,
                pair.symbol_b,
                price_hl.bid,
                pair.pools_map,
                current_prices,
                usdc_balance)

            if not opportunity: continue
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
        spread_percent = opportunity.spread

        symbol_a = watched_pair.symbol_a  # USDC
        symbol_b = watched_pair.symbol_b  # Ativo Volátil (WETH, ARB...)
        hl_pair = watched_pair.hl_pair

        if abs(spread_percent) > 20:
            logging.warning(f"🚫 Spread de {spread_percent}% ignorado por segurança (Threshold 20%)")
            return None

        logging.info(f"⚖️ {symbol_b} DEX-{dex_name}: {dex_price:.4f} | HL: {hl_price:.4f} | Net: ${profit:.4f} | "
                     f"Carteira {symbol_a}: {inventory.get(symbol_a)}, {symbol_b}: {inventory.get(symbol_b)} | "
                     f"Carteira HL {symbol_a}: {inventory.get('hl')} | "
                     f"Spread: {spread_percent:.2f}%")

        # --- LÓGICA DE EXECUÇÃO / GESTÃO ---

        # CENÁRIO A: Já temos este ativo volátil no contrato (Gestão de Posição)
        if symbol_b in active_tokens:
            units = active_tokens[symbol_b]
            logging.info(f"📦 Gerindo posição ativa: {units} {symbol_b}")

            # Busca posição de Short na Hyperliquid
            hl_pos = await self.exchange.get_open_position(hl_pair)

            if hl_pos is None:
                # Caso 1: Temos o token mas NÃO temos o Short
                if spread_percent >= self.min_entry_spread and profit >= self.min_profit:
                    logging.info(
                        f"🚀 Spread favorável ({spread_percent:.2f}%)! Abrindo Short na HL para proteger {units} {symbol_b}...")
                    await self.execute_entry_sequence(watched_pair, usdc_balance, dex_price, False, pool_addr,
                                                      direction)
                    # await self.exchange.open_new_position(hl_pair, 1, Signal.SELL, units * hl_price)
                else:
                    # NOVA LÓGICA: Aguarda o spread subir para abrir o hedge
                    logging.info(
                        f"⏳ {symbol_b} em carteira, mas spread ({spread_percent:.2f}%) abaixo do alvo de entrada ({self.min_entry_spread}%). Aguardando...")
                    return False
            else:
                # Caso 2: Temos o token E temos o Short (Posição de Arbitragem Completa)
                if profit >= self.min_profit or spread_percent <= self.min_exit_spread:
                    logging.info(
                        f"💰 Lucro atingido em {symbol_b}! Spread atual: {spread_percent:.2f}%. Fechando ciclo...")
                    await self.execute_exit_sequence(watched_pair, units, pool_addr, direction)
                    return True
                else:
                    logging.info(
                        f"💎 Arbitragem {symbol_b} saudável. Spread atual: {spread_percent:.2f}% | Alvo Saída: {self.min_exit_spread}%")
                    return False
        # CENÁRIO B: Carteira limpa, procurar Novas Entradas
        # Importante: só entra aqui se NÃO houver nenhum token ativo sendo gerido
        elif not active_tokens:
            if spread_percent >= self.min_entry_spread and profit >= self.min_profit:
                if usdc_balance >= self.min_usdc_to_trade:
                    logging.info(
                        f"🎯 Nova oportunidade detectada: {spread_percent:.2f}% em {symbol_b}. Executando entrada!")
                    await self.execute_entry_sequence(watched_pair, usdc_balance, dex_price, True, pool_addr, direction)
                    return True
                else:
                    logging.warning(f"⚠️ Saldo USDC ({usdc_balance}) insuficiente para entrar em {symbol_b}")
                    return False

        return False

    async def execute_entry_sequence(self, pair: WatchedPair, amount_usdc: float, price_dex: float,
                                     is_dex_swap: bool, selected_pool: str, direction: bool):
        """
        1. Swap USDC -> Token (DEX)
        2. Open Short (Hyperliquid) - Ambos usando o valor em USDC
        """
        logging.info(f"🚀 Iniciando entrada em {pair.symbol_b} com {amount_usdc} USDC")

        if is_dex_swap:

            # O segredo está aqui:
            # tokens_list[0] = O que tu TENS no contrato (USDC)
            # tokens_list[1] = O que tu QUERES receber (WETH/ARB...)
            tokens_para_swap = [pair.addr_a, pair.addr_b]

            if not pair.pools_map:
                return

            # selected_pool = list(pair.pools_map.values())[0]  # Pega a primeira pool encontrada

            # PASSO 1: Swap na DEX
            # Passamos os dados da Dataclass e o valor float de USDC
            tx_hash = self.wallet.send_transaction(
                pools_list=[selected_pool],  # Extrai os endereços das pools do dict
                dir_list=[direction],  # Usa o booleano pré-calculado
                tokens_list=tokens_para_swap,  # O token que sai (USDC)
                amount_usd=amount_usdc  # Valor em float
            )

            if not tx_hash:
                logging.error("❌ Falha ao enviar transação para a DEX. Abortando.")
                return False

            # PASSO 2: Hedge na Hyperliquid
            # Como o teu método já converte USDC -> Units internamente, passamos o valor direto
            logging.info(f"✅ Transação DEX enviada! Abrindo Short na HL para cobrir ${amount_usdc}...")

        hl_success = await self.exchange.open_new_position(
            pair.hl_pair,
            1.0,  # Alavancagem (Leverage)
            Signal.SELL,  # Ordem de Short
            amount_usdc  # O valor em dólares que o teu método vai converter
        )

        if hl_success:
            logging.info(f"🔒 Hedge confirmado na HL para o par {pair.hl_pair}")

            # --- NOVO BLOCO: REGISTO EM MEMÓRIA ---
            try:

                # 2. Pegamos os dados da posição que acabámos de abrir na HL
                # Idealmente, o teu open_new_position deve retornar o objeto da ordem ou o preço
                # Se não retornar, podemos ir buscar a posição atualizada:
                hl_pos = await self.exchange.get_open_position(pair.hl_pair)

                price_perp = 0.0
                if hl_pos is not None:
                    price_perp = hl_pos.entry_price

                # 3. Calculamos o Spread de Entrada
                entry_spread = (price_perp - price_dex) / price_dex

                # 4. Guardamos no dicionário da classe
                self.active_positions[pair.hl_pair] = ProfitInfo(entry_spread, price_perp, price_dex, amount_usdc,
                                                                 0.001 * amount_usdc, 0.0, time.time(), pair)
                logging.info(
                    f"📈 [MEMÓRIA] Posição Registada: Spread {entry_spread * 100:.4f}% | Perp: {price_perp} | Spot: {price_dex}")

            except Exception as e:
                logging.error(f"⚠️ Erro ao registar posição na memória (mas o hedge está aberto): {e}")
            return True
        else:
            # PONTO DE ATENÇÃO: Se a DEX executou e a HL não, tens o token "nu" no contrato
            logging.error(f"🚨 ERRO CRÍTICO: Swap feito na DEX, mas falha ao abrir Short na HL!")
            return False

    async def execute_exit_sequence(self, pair: WatchedPair, units, selected_pool: str, direction: bool):
        """
        1. Close Short (Hyperliquid)
        2. Swap Token -> USDC (DEX)
        """
        logging.info(f"💰 Realizando lucro para {pair.symbol_b} ({units} unidades)")

        # PASSO 1: Fechar Short na Hyperliquid
        # Geralmente fechamos a mercado para garantir a saída rápida
        current_position = await self.exchange.get_open_position(pair.hl_pair)

        hl_success = False
        if current_position:
            await self.exchange.close_position(pair.hl_pair, float(current_position.size), Signal.BUY)
            hl_success = True

        if not hl_success:
            logging.error("❌ Falha ao fechar posição na HL. Abortando venda na DEX para evitar exposição.")
            return False

        # PASSO 2: Venda na DEX (Token -> USDC)
        logging.info(f"✅ Short fechado na HL. Vendendo {units} {pair.symbol_b} na DEX...")

        units_in_wei = int(units * (10 ** pair.decimal_b))
        logging.info(f"✅ Vendendo {units_in_wei} (Wei) de {pair.symbol_b} na DEX...")

        # Rota: [TOKEN_VOLATIL, USDC]
        tokens_para_saida = [pair.addr_b, pair.addr_a]

        # IMPORTANTE: dir_list na saída costuma ser o inverso da entrada.
        # Se na entrada USDC->WETH era True, agora WETH->USDC deve ser False.
        direction_exit = not direction
        # selected_pool = list(pair.pools_map.values())[0]
        tx_hash = self.wallet.send_transaction(
            pools_list=[selected_pool],
            dir_list=[direction_exit],
            tokens_list=tokens_para_saida,
            amount_usd=units_in_wei
        )

        if tx_hash:
            logging.info(f"💵 Ciclo concluído com sucesso! TX: {tx_hash}")
            if pair.hl_pair in self.active_positions:
                del self.active_positions[pair.hl_pair]
            return True
        else:
            logging.error(
                f"🚨 Alerta: Short fechado na HL, mas erro ao vender na DEX. Tens {units} {pair.symbol_b} no contrato!")
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
                              current_prices, amount_usdc) -> (DexOpportunity | None):
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
                amount_after_dex = cap_human * (1 - fee_dex_percent)
                tokens_bought = amount_after_dex / price_dex
                total_after_hl = (tokens_bought * price_hl) * (1 - 0.00035)

                net_profit = total_after_hl - cap_human - 0.15
                spread_percent = ((price_hl / price_dex) - 1) * 100

                # --- A LÓGICA DE COMPARAÇÃO ---

                current_opp = DexOpportunity("MULTI_CHAIN", net_profit, spread_percent, symbol_b, price_dex, price_hl,
                                             pool_addr, dex_name, direction)

                # Se for a primeira ou se for melhor que a anterior, guarda
                if best_opportunity is None or current_opp.profit > best_opportunity.profit:
                    best_opportunity = current_opp

        # Só retorna depois de verificar TODAS as pools do map
        return best_opportunity
