import asyncio
import logging
import math
import time
from datetime import datetime

import ccxt.async_support as ccxt

from core.bots.exchanges.exchange_client import ExchangeClient
from core.config.properties_multi import PropertiesMulti
from core.dclass.active_position_dclass import ActivePosition
from core.dclass.dex_opportunity_dclass import DexOpportunity
from core.dclass.signal_enum import Signal
from core.dclass.watched_pair_dclass import WatchedPair
from core.pools.pool_finder import PoolFinder
from core.strategies.arbitrage_base import ArbitrageBase
from core.utils.trade_position_multi import TradePositionMulti
from core.web3.wallet_base import WalletBase


class MultiChainStrategy(ArbitrageBase):
    def __init__(self, web3_manager, properties: PropertiesMulti, pool_finder: PoolFinder, wallet: WalletBase,
                 capital_amount: float):
        super().__init__(web3_manager, properties.CONFIG)
        self.watched_pairs = None
        self.finder = pool_finder
        self.wallet = wallet
        self.capital = wallet.get_usdc_balance()
        self.config = properties.CONFIG
        self.min_usdc_to_trade = 10.0
        self.min_exit_spread = -1.5
        self.max_slots = 2

        # self.active_position = TradePosition.get_position()

        self.hl = ccxt.hyperliquid({
            "walletAddress": properties.WALLET_ADDRESS_HL,
            "privateKey": properties.PRIVATE_KEY_WALLET_HL,
            "enableRateLimit": True,
            "timeout": 10000,
            "testnet": False,
            "options": {"defaultSlippage": 0.01},
        })

        self.exchange = ExchangeClient(self.hl, properties.WALLET_ADDRESS_HL)

        self.pool_blacklist: dict = {}  # { "pool_address": timestamp_liberacao }
        self.blacklist_duration = 300  # 5 minutos de "castigo"

        self.active_positions = TradePositionMulti.load_all_positions()

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
        dex_balance_usdc = inventory.get("USDC", 0.0)
        hl_balance_usdc = inventory.get("hl", 0.0)

        """
        total_balance_usdc = dex_balance_usdc + hl_balance_usdc

        # Capital disponível para NOVAS entradas
        usdc_balance_to_trade = min(dex_balance_usdc, hl_balance_usdc)
        """

        # --- AJUSTE DE SALDO PARA MULTI-SLOT ---
        # Somamos o que já está investido na DEX (via JSONs) para saber o capital total da estratégia
        capital_investido_dex = sum(pos.initial_balance_dex_usd for pos in self.active_positions.values())
        # O capital total é o que temos livre + o que já está alocado na ponta DEX
        # (Não somamos a ponta HL porque o saldo da HL já considera a margem isolada)
        total_strategy_capital = dex_balance_usdc + capital_investido_dex

        # Definimos quanto cada slot DEVE ter
        target_per_slot = total_strategy_capital / self.max_slots

        # O saldo disponível para este ciclo é o menor entre o "teórico do slot" e o que temos na carteira
        usdc_balance_to_trade = min(target_per_slot, dex_balance_usdc, hl_balance_usdc)
        # ----------------------------------------

        total_balance_usdc = dex_balance_usdc + hl_balance_usdc

        # 2. Identificar "Batatas Quentes" (Ativos já em carteira)
        active_tokens = {sym: units for sym, units in inventory.items()
                         if sym not in ["USDC", "hl"] and units > 0.00001}

        logging.info(
            f"📊 [ESTADO] DEX: {dex_balance_usdc:.2f} USDC | HL: {hl_balance_usdc:.2f} USDC | Tokens: {active_tokens}")

        # 3. VERIFICAÇÃO LÓGICA:
        # Se não temos tokens para gerir E o saldo é baixo, então paramos.
        if not active_tokens and usdc_balance_to_trade < self.min_usdc_to_trade:
            logging.info(
                f"⏳ Modo espera: Saldo insuficiente para novas entradas (${usdc_balance_to_trade:.2f})."
            )
            return False

        symbols_to_fetch = [p.hl_pair for p in self.watched_pairs]
        all_prices_hl = await self.exchange.get_multiple_prices(symbols_to_fetch)

        # 4. Se chegámos aqui, ou temos saldo ou temos posições para gerir!
        eth_price = all_prices_hl['ETH/USDC:USDC'].bid
        gas_cost_usdc = self.wallet.get_gas_cost_usd(eth_price)

        # 5. Recolher preços em Batch
        all_pool_addrs = []
        for pair in self.watched_pairs:
            all_pool_addrs.extend(pair.pools_map.values())
        current_prices = self.get_quotes_batch(all_pool_addrs)

        # 6. LOOP DE DECISÃO
        for pair in self.watched_pairs:

            symbol_base = pair.symbol_b  # Ex: "ARB"
            active_position = None
            if symbol_base in self.active_positions:
                # Passas a posição específica para o manage_orders
                active_position = self.active_positions[symbol_base]
            elif len(self.active_positions) < self.max_slots:
                active_position = None

            """
            if self.active_position is not None:
                # Extrai o símbolo base do JSON (ex: "ARB/USDC" -> "ARB")
                pos_base_symbol = self.active_position.symbol.split('/')[0]
                if pair.symbol_b != pos_base_symbol:
                    continue
            """
            price_data = all_prices_hl[pair.hl_pair]
            if not price_data or price_data.bid is None:
                logging.warning(f"⚠️ Sem dados para {pair.hl_pair}, saltando...")
                continue

            hl_price = price_data.bid

            opportunity = self.find_cross_dex_spread(
                pair.addr_a, pair.addr_b,
                pair.symbol_a, pair.symbol_b,
                hl_price, pair.pools_map,
                current_prices, usdc_balance_to_trade, gas_cost_usdc
            )

            if not opportunity:
                continue

            # O manage_orders agora decide se fecha o que existe ou abre o que falta
            success = await self.manage_orders(opportunity, active_position, pair, usdc_balance_to_trade,
                                               total_balance_usdc,
                                               hl_price)
            if success:
                logging.info(f"✅ Executar trasação para {pair.symbol_b}. Parando ciclo para atualizar saldos.")
                return True  # Sai do analyze_all_pairs imediatamente

        return True

    async def manage_orders(self, opportunity: DexOpportunity, active_position: ActivePosition,
                            watched_pair: WatchedPair, usdc_balance_to_trade: float,
                            total_balance_usdc: float,
                            price_hl: float):

        dex_price = opportunity.price_dex
        # hl_price = opportunity.price_hl
        profit = opportunity.profit  # Este é o lucro ESTIMADO para entrada
        pool_addr = opportunity.pool_addr
        direction = opportunity.direction
        dex_fee = opportunity.dex_fee
        spread_percent = opportunity.spread

        symbol_b = watched_pair.symbol_b
        hl_pair = watched_pair.hl_pair

        if abs(spread_percent) > 20:
            logging.warning(f"🚫 Spread de {spread_percent}% ignorado por segurança (Threshold 20%)")
            return None

        # --- LÓGICA DE EXECUÇÃO / GESTÃO ---

        # CENÁRIO A: Monitorização de Posição Aberta (Lido via JSON)

        if active_position and active_position.status == "OPEN":
            if symbol_b in active_position.symbol:
                # 1. Calcular Lucro Real Absoluto (Baseado no JSON)
                current_profit_real = TradePositionMulti.check_exit_profitability(active_position, dex_price, price_hl)

                # 2. Validar Saída (Nova Assinatura: foco no lucro real)
                # Nota: amount_usdc não é usado na saída, passamos None ou 0
                check_v = self.check_viability_dynamic(
                    watched_pair=watched_pair,
                    net_profit=current_profit_real,
                    spread_percent=spread_percent,
                    amount_usdc=active_position.total_balance_before_usd,
                    is_exit=True
                )

                if check_v:
                    logging.info(f"💰 META ATINGIDA: Fechando {symbol_b} | Lucro Real: ${current_profit_real:.4f}")
                    # Importante: units_dex vem do JSON para garantir que vendemos TUDO o que compramos
                    await self.execute_exit_sequence(watched_pair, active_position.units_dex, pool_addr, direction,
                                                     dex_price, int(dex_fee))
                    return True
                else:
                    logging.info(
                        f"JSON INFO: {active_position}")

                    lucro_seguro = current_profit_real if current_profit_real is not None else 0.0
                    # O log agora mostra quanto falta para o teu alvo de $0.20
                    logging.info(
                        f"💎 MONITORANDO: {symbol_b} | Lucro Real: ${lucro_seguro:.4f} | Spread: {spread_percent:.2f}%")
                    return False
            else:
                # Se for outro par (ex: AAVE), ignora a monitorização e não tenta abrir nada
                logging.info(
                    f"⏳ Já existe uma posição aberta em {active_position.symbol}. Ignorando {symbol_b}...")
                return False

        # CENÁRIO B: Procurar Novas Entradas (Se não houver posição no JSON)
        elif not active_position:
            # Validar Entrada (Ainda usamos spread e lucro estimado)
            check_v = self.check_viability_dynamic(
                watched_pair=watched_pair,
                net_profit=profit,
                spread_percent=spread_percent,
                amount_usdc=usdc_balance_to_trade,
                is_exit=False
            )

            if check_v:
                logging.info(f"🎯 Oportunidade Validada: {spread_percent:.2f}% em {symbol_b}. Executando...")
                adjust_balance = self.adjust_balance(usdc_balance_to_trade, dex_price, hl_pair, symbol_b)

                # Passamos o real_units para a entrada para garantir hedge perfeito
                await self.execute_entry_sequence(
                    watched_pair, adjust_balance, total_balance_usdc, dex_price, price_hl, int(dex_fee),
                    pool_addr, direction
                )
                return True

        return False

    def adjust_balance(self, usdc_balance_to_trade: float, dex_price: float, hl_pair: str, symbol_b: str) -> float:
        try:
            # 1. Garantir que os mercados estão carregados no CCXT
            if hl_pair not in self.hl.markets:
                logging.warning(f"⚠️ Par {hl_pair} não carregado. A tentar usar valor bruto.")
                return usdc_balance_to_trade

            market = self.hl.market(hl_pair)

            # 2. Calcular quantidade bruta de tokens
            raw_qty = usdc_balance_to_trade / dex_price

            # 3. Obter a precisão (número de casas decimais permitidas)
            # Na HL, isto vem geralmente em market['precision']['amount']
            precision = market['precision']['amount']

            # 4. Forçar o arredondamento para baixo (Floor) com base na precisão
            # Se precision for 0 (caso do PENDLE), factor será 1. Se for 2, factor será 100.
            factor = 10 ** precision
            clean_qty = math.floor(raw_qty * factor) / factor

            # 5. Converter para o formato de string/float que o CCXT aceita (evita erros de float binário)
            clean_qty = float(self.hl.amount_to_precision(hl_pair, clean_qty))

            # 6. Calcular o custo em USD para comprar EXATAMENTE essa quantidade
            # Adicionamos 0.3% de margem para cobrir a taxa da DEX (0.05% a 0.3%) e slippage
            # Assim garantimos que o contrato tem USDC suficiente para completar o swap
            adjust_balance = clean_qty * dex_price * 1.003

            if adjust_balance > usdc_balance_to_trade:
                logging.warning(f"⚠️ Ajuste excedeu balance original. Recalculando...")
                # Se a margem de 0.3% estourou o teto, reduzimos uma unidade de precisão
                step = 1 / factor
                clean_qty -= step
                adjust_balance = clean_qty * dex_price * 1.003

            logging.info(
                f"🎯 [PRECISÃO {symbol_b}] Qtd: {clean_qty} | "
                f"USD Original: ${usdc_balance_to_trade:.2f} | USD Ajustado: ${adjust_balance:.4f}"
            )

            return adjust_balance

        except Exception as e:
            logging.error(f"❌ Erro crítico no adjust_balance: {e}")
            return usdc_balance_to_trade

    async def execute_entry_sequence(self, pair: WatchedPair, amount_usdc_to_trade: float, total_balance_usdc: float,
                                     dex_price: float,
                                     hl_price: float,
                                     dex_fee: int, selected_pool: str, direction: bool):

        # 1. Validação inicial de segurança
        if amount_usdc_to_trade < 11.0:
            logging.warning(f"🚫 Abortando: Valor ${amount_usdc_to_trade:.2f} inferior ao mínimo CEX.")
            return False

        # 2. Check de Liquidez e Obtenção de Unidades REAIS
        expected_units = amount_usdc_to_trade / dex_price
        viable, real_units = self.wallet.is_swap_viable(
            token_in=pair.addr_a,
            token_out=pair.addr_b,
            amount_in_usd=amount_usdc_to_trade,
            expected_out_units=expected_units,
            fee=dex_fee,
            tolerance=0.001
        )

        # CÁLCULO DO VALOR REAL QUE VAI SER EXECUTADO
        # É aqui que corrigimos a incoerência: o valor na HL deve ser igual ao valor da DEX
        actual_trade_value_usdc = real_units * dex_price

        if not viable or actual_trade_value_usdc < 10.5:
            logging.error(
                f"❌ Abortando: Liquidez insuficiente ou valor final (${actual_trade_value_usdc:.2f}) abaixo do mínimo.")
            self.pool_blacklist[selected_pool.lower()] = time.time() + self.blacklist_duration
            return False

        logging.info(f"🚀 Iniciando entrada: DEX (${actual_trade_value_usdc:.2f}) | HL (${actual_trade_value_usdc:.2f})")

        # 3. Execução na DEX

        # Importante: usar o valor que a liquidez permite, convertido para Wei
        usdc_wei = int(actual_trade_value_usdc * (10 ** pair.decimal_a))

        tx_hash = self.wallet.send_transaction(
            pools_list=[selected_pool],
            dir_list=[direction],
            tokens_list=[pair.addr_a, pair.addr_b],
            amount_usd=usdc_wei
        )

        if not tx_hash:
            logging.error("❌ Falha crítica no envio da transação DEX.")
            return False

        logging.info(f"⏳ Aguardando confirmação DEX (Hash: {tx_hash})...")
        await asyncio.sleep(3)  # Aumentado para 3s para dar folga ao RPC

        # 4. Hedge na Hyperliquid (Usando o MESMO valor que entrou na DEX)
        try:
            hl_result = await self.exchange.open_new_position(
                pair.hl_pair,
                1.0,
                Signal.SELL,
                actual_trade_value_usdc  # <--- AGORA ESTÁ SINCRONIZADO
            )
        except Exception as e:
            logging.error(f"💥 Exceção ao abrir posição na HL: {e}")
            hl_result = None

        # 5. Validação do Hedge e Rollback
        if hl_result:
            # 1. Definição do Preço Seguro (Ordem de prioridade)
            price_to_use = hl_result.price or hl_price

            # 2. Definição do Valor Seguro (Balanço em USD)
            # Se a HL falhar, usamos o valor da DEX menos uma margem de erro (ex: 0.40 USD)
            dex_value = float(actual_trade_value_usdc)
            safety_margin = 0.40

            if hl_result.price and hl_result.amount:
                # Cenário Ideal: Temos dados reais da HL
                actual_hl_cost_usd = float(hl_result.price * hl_result.amount)
                actual_hl_units = float(hl_result.amount)
            else:
                # Cenário de Falha: Assumimos o valor da DEX com margem de segurança
                logging.warning(f"⚠️ Falha nos dados da HL. Assumindo valor DEX - ${safety_margin}")
                actual_hl_cost_usd = dex_value - safety_margin
                # Estimamos as unidades com base nesse valor seguro
                actual_hl_units = actual_hl_cost_usd / price_to_use

            # 3. Cálculo do Total e criação da Posição
            actual_total_value_usd = dex_value + actual_hl_cost_usd

            logging.info(f"🔒 Hedge confirmado na HL a ${price_to_use}")

            new_pos = ActivePosition(
                status="OPEN",
                symbol=pair.hl_pair,
                units_dex=float(real_units),
                initial_balance_dex_usd=dex_value,
                initial_balance_hl_usd=actual_hl_cost_usd,
                total_balance_before_usd=total_balance_usdc,
                total_balance_after_usd=actual_total_value_usd,
                entry_price_hl=float(price_to_use),  # Guardamos o preço seguro
                entry_price_dex=float(dex_price),
                timestamp=datetime.now().isoformat()
            )

            TradePositionMulti.save_position(new_pos)
            # self.active_position = new_pos
            self.active_positions[pair.hl_pair] = new_pos
            return True
        else:
            self.force_exit_to_usdc(pair.addr_b, pair.addr_a, real_units, selected_pool, not direction)

            return False

    def force_exit_to_usdc(self, token_address: str, usdc_addr: str, amount_units: float, pool_to_use: str,
                           direction_to_use: bool):
        """
        Saída de emergência sem dependência de estado interno volátil.
        """
        logging.critical(f"🚨 [EMERGENCY] Tentando vender {amount_units} do token {token_address}")

        try:

            # 1. Obter decimais do token (via teu config ou helper)
            t_addr_info = self.config.tokens_by_address.get(token_address)
            dec_in = t_addr_info.decimals if t_addr_info else 18
            amount_in_wei = int(amount_units * (10 ** dec_in))

            # 2. Executar o swap de reversão
            tx_hash = self.wallet.send_transaction(
                pools_list=[pool_to_use],
                dir_list=[direction_to_use],
                tokens_list=[token_address, usdc_addr],
                amount_usd=amount_in_wei
            )

            if tx_hash:
                logging.info(f"✅ [EMERGENCY] Sucesso! Hash: {tx_hash}")
                return True

            return False

        except Exception as e:
            logging.error(f"💀 [EMERGENCY] Falha crítica na saída de emergência: {e}")
            return False

    async def execute_exit_sequence(self, pair: WatchedPair, units, selected_pool: str, direction: bool,
                                    dex_price: float, dex_fee: int):
        """
        Sequência de Fecho Protegida:
        1. Valida liquidez via Quoter (Simulação)
        2. Executa Swap na DEX (Token -> USDC)
        3. Se (e apenas se) a DEX responder, fecha o Short na HL
        4. Limpa o estado (JSON)
        """
        logging.info(f"🚀 Validando cotação final para saída em {pair.symbol_b}...")
        expected_usdc = units * dex_price
        # 1. Simulação Quoter (Token -> USDC)
        # Verificamos se o que vamos receber cobre o custo inicial ou a meta
        viable, real_units_usdc = self.wallet.is_swap_viable(
            token_in=pair.addr_b,  # Endereço do ARB
            token_out=pair.addr_a,  # Endereço do USDC
            amount_in_usd=units,
            expected_out_units=expected_usdc,
            fee=dex_fee,
            tolerance=0.003  # 1.5% de tolerância para garantir a saída
        )

        if not viable:
            logging.warning(f"⏳ Saída adiada para {pair.symbol_b}: Slippage/Liquidez desfavorável no momento.")
            return False

        logging.info(f"💰 Condições ideais detectadas. Iniciando fecho de ciclo...")

        # 2. Venda na DEX (A parte mais sensível)
        # Consultamos o saldo real no contrato para evitar falhas de 'Insufficient Balance'
        units_in_wei = self.wallet.get_token_balance(pair.addr_b)

        if units_in_wei == 0:
            logging.error(f"❌ Erro: Saldo de {pair.symbol_b} no contrato é 0. O JSON pode estar desalinhado.")
            return False

        logging.info(f"✅ Saldo em carteira: {units_in_wei} Wei. Enviando transação para a DEX...")

        # Definimos a rota e a direção inversa da entrada
        tokens_para_saida = [pair.addr_b, pair.addr_a]
        direction_exit = not direction

        tx_hash = self.wallet.send_transaction(
            pools_list=[selected_pool],
            dir_list=[direction_exit],
            tokens_list=tokens_para_saida,
            amount_usd=units_in_wei  # Enviamos o valor inteiro em Wei
        )

        # TRAVA DE SEGURANÇA: Se a DEX falhar, paramos aqui para manter o Short aberto (Hedge)
        if not tx_hash:
            logging.error("🚨 FALHA NO SWAP DEX: A transação não foi enviada. Mantendo Short na HL para proteção.")
            return False

        logging.info(f"🔗 Swap enviado (Hash: {tx_hash}). Fechando agora a ponta na Hyperliquid...")

        # 3. Fechar Short na Hyperliquid
        try:
            current_position = await self.exchange.get_open_position(pair.hl_pair)

            if current_position and abs(float(current_position.size)) > 0:
                hl_size = abs(float(current_position.size))
                logging.info(f"📉 Fechando Short de {hl_size} unidades em {pair.hl_pair}...")
                # Na HL, para fechar um Short (SELL), fazemos um BUY
                await self.exchange.close_position(pair.hl_pair, hl_size, Signal.BUY)
                hl_success = True
            else:
                logging.warning(
                    f"⚠️ Nenhuma posição ativa detectada na HL para {pair.hl_pair}. O Short pode ter sido fechado manualmente.")
                hl_success = True  # Consideramos sucesso para limpar o JSON

        except Exception as e:
            logging.error(f"🚨 ERRO CRÍTICO ao fechar HL: {e}. Tokens vendidos na DEX mas Short continua aberto!")
            hl_success = False

        # 4. Finalização e Limpeza de Estado
        if tx_hash and hl_success:
            logging.info(f"💵 CICLO CONCLUÍDO COM SUCESSO! DEX: {tx_hash} | HL: OK")
            TradePositionMulti.clear_position(pair.hl_pair)
            self.active_positions.pop(pair.hl_pair, None)
            return True

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
            if pool_addr.lower() in self.pool_blacklist:
                if time.time() < self.pool_blacklist[pool_addr.lower()]:
                    continue  # Pula esta pool, ainda está castigada
                else:
                    del self.pool_blacklist[pool_addr.lower()]  # Já pode tentar de novo

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

    def check_viability_dynamic(self, watched_pair: WatchedPair, net_profit, amount_usdc, is_exit=False,
                                spread_percent=None):
        """
        Validação de viabilidade com metas de 0.5% para entrada e 0.7% para saída.
        """

        # --- 1. LÓGICA DE ENTRADA ---
        if not is_exit:
            # Exigimos 0.5% de lucro líquido para abrir a posição
            min_profit_required = amount_usdc * 0.005
            min_spread_required = 1

            success = net_profit >= min_profit_required and spread_percent >= min_spread_required

            if spread_percent > 0.20:
                gap = min_profit_required - net_profit
                gap_str = f" | Falta: ${gap:.4f}" if gap > 0 else " | ✅ PRONTO"

                logging.info(
                    f"🔍 [SCANNER] {watched_pair.symbol_b} | "
                    f"Spread: {spread_percent:.2f}% | "
                    f"Lucro Est: ${net_profit:.4f} | "
                    f"Alvo Min: ${min_profit_required:.4f}"
                    f"{gap_str}"
                )

            return success

        # --- 2. LÓGICA DE SAÍDA ---
        else:
            # Definimos a meta de 0.7% sobre o capital total do trade
            roi_target = 0.007
            min_net_profit_out = amount_usdc * roi_target if amount_usdc > 0 else 0.50

            # Cálculo de progresso para o log
            progress = (net_profit / min_net_profit_out) * 100 if min_net_profit_out > 0 else 0

            status_icon = "💰" if net_profit > 0 else "⏳"
            status_msg = (
                f"{status_icon} [MONITOR] {watched_pair.symbol_b} | "
                f"Lucro: ${net_profit:.4f}/${min_net_profit_out:.2f} | "
                f"Progresso: {progress:.1f}% | Spread: {spread_percent:.2f}%"
            )

            if net_profit >= min_net_profit_out:
                logging.info(f"✅ META ALCANÇADA! {status_msg}")
                return True

            # Log de monitorização (ajustado para ser mais scannable)
            logging.info(status_msg)

            return False
