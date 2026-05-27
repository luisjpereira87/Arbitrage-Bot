import asyncio
import logging
import math
import time
from datetime import datetime

import ccxt.async_support as ccxt

from core.bots.exchanges.exchange_client import ExchangeClient
from core.config.properties_multi import PropertiesMulti
from core.dclass.active_position_dclass import ActivePosition
from core.dclass.chains_enum import Chains
from core.dclass.dex_opportunity_dclass import DexOpportunity
from core.dclass.signal_enum import Signal
from core.dclass.watched_pair_dclass import WatchedPair
from core.pools.pool_finder import PoolFinder
from core.strategies.arbitrage_base import ArbitrageBase
from core.utils.trade_position_multi import TradePositionMulti
from core.web3.executors.executor_base import ExecutorBase


class MultiChainStrategy(ArbitrageBase):
    def __init__(self, web3_manager, properties: PropertiesMulti, pool_finder: PoolFinder, wallet: ExecutorBase,
                 capital_amount: float):
        super().__init__(web3_manager, properties.CONFIG)
        # self.watched_pairs: list[WatchedPair] = []
        self.finder = pool_finder
        self.wallet = wallet
        # self.capital = wallet.get_usdc_balance()
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

    async def calculate_all_chains_capital(self) -> dict:
        """
        Calcula o capital disponível, alocado e os tokens ativos para todas as chains
        de uma única vez antes do loop de decisão.
        """
        inventory = await self.get_all_balances()
        hl_balance_usdc = inventory.get("hl", 0.0)

        # Estrutura onde vamos guardar os resultados processados por chain
        # Ex: {"solana": {"usdc_to_trade": 100.0, "active_tokens": {...}}, ...}
        chains_capital = {}

        # Mapeamos quais são as chains que temos configuradas nos nossos pares
        active_chains = {pair.chain for pair in self.watched_pairs}

        for chain in active_chains:
            chain_balances = inventory.get(chain.value, {})

            # 🚀 SOLUÇÃO DINÂMICA: Procuramos o par modelo desta chain para saber o nome do USDC
            # Pode ser "USDC" na Arbitrum ou "USDC_SOL" na Solana
            pair_modelo = next((p for p in self.watched_pairs if p.chain == chain), None)
            usdc_symbol = pair_modelo.symbol_a if pair_modelo else "USDC"

            dex_balance_usdc = chain_balances.get(usdc_symbol, 0.0)
            # dex_balance_usdc = 50.0

            # Filtrar posições ativas desta chain específica
            capital_investido_nesta_dex = sum(
                pos.initial_balance_dex_usd for pos in self.active_positions.values()
                if getattr(pos, 'chain', None) == chain
            )

            total_chain_capital = dex_balance_usdc + capital_investido_nesta_dex
            target_per_slot = total_chain_capital / self.max_slots

            # O capital para o trade respeita o limite do slot, o saldo real da DEX e a margem livre na HL
            usdc_balance_to_trade = min(target_per_slot, dex_balance_usdc, hl_balance_usdc)
            total_balance_usdc = dex_balance_usdc + hl_balance_usdc

            # Identificar "Batatas Quentes" desta chain
            active_tokens = {
                sym: units for sym, units in chain_balances.items()
                if "USDC" not in sym and units > 0.00001
            }

            # Guardamos no dicionário com o índice da chain
            chains_capital[chain] = {
                "usdc_balance_to_trade": usdc_balance_to_trade,
                "total_balance_usdc": total_balance_usdc,
                "active_tokens": active_tokens,
                "dex_balance_usdc": dex_balance_usdc
            }

            logging.info(
                f"📊 [BANCA CALCULADA] {chain.value.upper()} | "
                f"DEX USDC: {dex_balance_usdc:.2f} | Slot Alocado: {usdc_balance_to_trade:.2f} | "
                f"Tokens: {active_tokens} | "
                f"HL USDC: {hl_balance_usdc}"
            )

        return chains_capital

    async def _fetch_hyperliquid_prices(self):
        """Procura os preços atuais na Hyperliquid para todos os pares vigiados."""
        symbols_to_fetch = [p.hl_pair for p in self.watched_pairs]
        return await self.exchange.get_multiple_prices(symbols_to_fetch)

    async def analyze_all_pairs(self):
        # 1. 🚀 PASSO CRÍTICO: Calcula a banca de todas as chains de uma só vez antes do loop!
        chains_capital = await self.calculate_all_chains_capital()

        # 2. Atualiza preços da Hyperliquid e da Arbitrum (Dados frescos para este ciclo)
        all_prices_hl = await self._fetch_hyperliquid_prices()
        # self._update_arbitrum_pool_quotes()

        # 3. Estimar custos de gás (Exemplo Arbitrum)
        eth_price = all_prices_hl.get('ETH/USDC:USDC').bid if 'ETH/USDC:USDC' in all_prices_hl else 3000.0
        gas_cost_usdc = await self.wallet.get_gas_cost_usd(eth_price, Chains.ARBITRUM)

        # 4. LOOP DE DECISÃO (Agora ultra-rápido rodando puramente em memória)
        for pair in self.watched_pairs:
            # Extrai os dados já calculados sem await e sem tocar na rede!
            cap_data = chains_capital.get(pair.chain)
            if not cap_data:
                continue

            usdc_balance_to_trade = cap_data["usdc_balance_to_trade"]
            total_balance_usdc = cap_data["total_balance_usdc"]
            active_tokens = cap_data["active_tokens"]

            # Verificação rápida se o slot tem fundos mínimos para trabalhar
            if not active_tokens and usdc_balance_to_trade < self.min_usdc_to_trade:
                continue

            symbol_base = pair.symbol_b  # Ex: "HYPE"
            active_position = self.active_positions.get(symbol_base)

            # --- 🛡️ TRAVÃO DE SEGURANÇA: CONTROLO DE SLOTS ISOLADOS ---
            # Se NÃO temos uma posição aberta para este token, significa que seria um NOVO trade.
            # Portanto, temos de validar se esta blockchain ainda tem vagas (slots) disponíveis.
            if not active_position:
                slots_ocupados_na_chain = sum(
                    1 for pos in self.active_positions.values()
                    if getattr(pos, 'chain', None) == pair.chain
                )

                if slots_ocupados_na_chain >= self.max_slots:
                    # Já estoirou o limite de slots desta rede. Ignora novas oportunidades para este par.
                    logging.debug(
                        f"⏳ {pair.chain.value.upper()} sem slots livres ({slots_ocupados_na_chain}/{self.max_slots}). Saltando entrada em {symbol_base}")
                    continue
            # ---------------------------------------------------------

            price_data = all_prices_hl.get(pair.hl_pair)
            if not price_data or price_data.bid is None:
                logging.warning(f"⚠️ Sem dados para {pair.hl_pair}, saltando...")
                continue

            hl_price = price_data.bid

            # Procura oportunidades na DEX correspondente
            opportunity = await self.find_best_dex_opportunity(pair, hl_price, usdc_balance_to_trade, gas_cost_usdc)
            if not opportunity:
                await asyncio.sleep(1.0)
                continue

            # Executa o trade se a oportunidade for viável
            success = await self.manage_orders(
                opportunity, active_position, pair, usdc_balance_to_trade, total_balance_usdc, hl_price
            )

            if success:
                logging.info(
                    f"✅ Transação executada para {pair.symbol_b}. Parando ciclo para atualizar os saldos na próxima ronda.")
                return True
        await asyncio.sleep(1.0)
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
        data_quote = opportunity.data_quote

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
                                                     dex_price, int(dex_fee), data_quote)
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
                    pool_addr, direction, data_quote
                )
                return True

        return False

    def adjust_balance(self, usdc_balance_to_trade: float, dex_price: float, hl_pair: str, symbol_b: str) -> float:
        try:
            # 1. Garantir que os mercados estão carregados
            if hl_pair not in self.hl.markets:
                logging.warning(f"⚠️ Par {hl_pair} não carregado. A tentar usar valor bruto.")
                return usdc_balance_to_trade
    
            market = self.hl.market(hl_pair)
    
            # 2. Calcular quantidade bruta de tokens (Ex: 24.85 / 50.24 = 0.49462)
            raw_qty = usdc_balance_to_trade / dex_price
    
            # 3 e 4. Deixar o CCXT tratar o arredondamento de forma nativa e segura
            # O método 'amount_to_precision' da exchange sabe EXATAMENTE como a HL quer o número.
            # Forçamos a conversão para float para podermos fazer contas matemáticas a seguir.
            clean_qty = float(self.hl.amount_to_precision(hl_pair, raw_qty))
    
            # 5. Calcular o custo em USD para comprar essa quantidade com a margem de 0.3%
            adjust_balance = clean_qty * dex_price * 1.003
    
            # 6. Validação de teto de gastos: se a margem de 0.3% ultrapassou o teu slot disponível
            if adjust_balance > usdc_balance_to_trade:
                logging.warning(f"⚠️ Ajuste excedeu balance original para {symbol_b}. Recalculando...")
                
                # Em vez de inventar o 'step' com o factor, usamos a precisão da própria exchange
                # No CCXT, a variação mínima (tick size do amount) está em market['limits']['amount']['min'] ou market['precision']['amount']
                # Para evitar bugs, vamos apenas reduzir 1% da quantidade para garantir que cabe no orçamento
                clean_qty = float(self.hl.amount_to_precision(hl_pair, clean_qty * 0.99))
                adjust_balance = clean_qty * dex_price * 1.003
    
            # 7. Segurança máxima: Se depois de tudo a quantidade for zero, não podemos operar
            if clean_qty <= 0:
                logging.warning(f"🚫 [PRECISÃO {symbol_b}] Quantidade calculada é zero. Saldo insuficiente para o preço do token.")
                return 0.0
    
            logging.info(
                f"🎯 [PRECISÃO {symbol_b}] Qtd: {clean_qty} | "
                f"USD Original: ${usdc_balance_to_trade:.2f} | USD Ajustado: ${adjust_balance:.4f}"
            )
    
            return adjust_balance
    
        except Exception as e:
            logging.error(f"💥 Erro no adjust_balance para {symbol_b}: {e}")
            return 0.0

    def adjust_balance_old(self, usdc_balance_to_trade: float, dex_price: float, hl_pair: str, symbol_b: str) -> float:
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
                                     dex_fee: int, selected_pool: str, direction: bool, data_quote: dict | None):

        # 1. Validação inicial de segurança
        if amount_usdc_to_trade < 11.0:
            logging.warning(f"🚫 Abortando: Valor ${amount_usdc_to_trade:.2f} inferior ao mínimo CEX.")
            return False

        # 2. Check de Liquidez e Obtenção de Unidades REAIS
        expected_units = amount_usdc_to_trade / dex_price
        viable, real_units = await self.wallet.is_swap_viable(
            token_in=pair.addr_a,
            token_out=pair.addr_b,
            amount_in_usd=amount_usdc_to_trade,
            expected_out_units=expected_units,
            fee=dex_fee,
            tolerance=0.001,
            chain=pair.chain,
            quote_data=data_quote
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

        tx_hash = await self.wallet.send_transaction(
            pools_list=[selected_pool],
            dir_list=[direction],
            tokens_list=[pair.addr_a, pair.addr_b],
            amount_usd=usdc_wei,
            chain=pair.chain,
            quote_data=data_quote
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
            self.force_exit_to_usdc(pair, pair.addr_b, pair.addr_a, real_units, selected_pool, not direction,
                                    data_quote)

            return False

    def force_exit_to_usdc(self, pair: WatchedPair, token_address: str, usdc_addr: str, amount_units: float,
                           pool_to_use: str,
                           direction_to_use: bool, data_quote: dict | None):
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
                amount_usd=amount_in_wei,
                chain=pair.chain,
                quote_data=data_quote
            )

            if tx_hash:
                logging.info(f"✅ [EMERGENCY] Sucesso! Hash: {tx_hash}")
                return True

            return False

        except Exception as e:
            logging.error(f"💀 [EMERGENCY] Falha crítica na saída de emergência: {e}")
            return False

    async def execute_exit_sequence(self, pair: WatchedPair, units, selected_pool: str, direction: bool,
                                    dex_price: float, dex_fee: int, data_quote: dict | None):
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
        viable, real_units_usdc = await self.wallet.is_swap_viable(
            token_in=pair.addr_b,  # Endereço do ARB
            token_out=pair.addr_a,  # Endereço do USDC
            amount_in_usd=units,
            expected_out_units=expected_usdc,
            fee=dex_fee,
            tolerance=0.003,  # 1.5% de tolerância para garantir a saída
            chain=pair.chain,
            quote_data=data_quote
        )

        if not viable:
            logging.warning(f"⏳ Saída adiada para {pair.symbol_b}: Slippage/Liquidez desfavorável no momento.")
            return False

        logging.info(f"💰 Condições ideais detectadas. Iniciando fecho de ciclo...")

        # 2. Venda na DEX (A parte mais sensível)
        # Consultamos o saldo real no contrato para evitar falhas de 'Insufficient Balance'
        units_in_wei = await self.wallet.get_token_balance(pair.addr_b, pair.chain)

        if units_in_wei == 0:
            logging.error(f"❌ Erro: Saldo de {pair.symbol_b} no contrato é 0. O JSON pode estar desalinhado.")
            return False

        logging.info(f"✅ Saldo em carteira: {units_in_wei} Wei. Enviando transação para a DEX...")

        # Definimos a rota e a direção inversa da entrada
        tokens_para_saida = [pair.addr_b, pair.addr_a]
        direction_exit = not direction

        tx_hash = await self.wallet.send_transaction(
            pools_list=[selected_pool],
            dir_list=[direction_exit],
            tokens_list=tokens_para_saida,
            amount_usd=units_in_wei,  # Enviamos o valor inteiro em Wei
            chain=pair.chain,
            quote_data=data_quote
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
        # 1. Usamos o Set para rastrear o que precisamos de consultar (evita duplicados)
        # Guardamos tuplos com (endereço, simbolo, decimais, chain)
        tokens_to_fetch = set()

        for pair in self.watched_pairs:
            tokens_to_fetch.add((pair.addr_a, pair.symbol_a, pair.decimal_a, pair.chain))
            tokens_to_fetch.add((pair.addr_b, pair.symbol_b, pair.decimal_b, pair.chain))

        # 2. Criamos a lista de tarefas assíncronas para disparar tudo em paralelo
        tasks = []
        # Guardamos a ordem para saber quem é quem depois do gather
        metadata = []

        for addr, symbol, decimals, chain in tokens_to_fetch:
            tasks.append(self.wallet.get_token_balance(token_address=addr, chain=chain))
            metadata.append((chain.value, symbol, decimals))

        # Adicionamos a tarefa do saldo da Hyperliquid à festa
        tasks.append(self.exchange.get_available_balance())

        # 3. Disparamos TODOS os pedidos de rede ao mesmo tempo!
        # O gather faz com que o nó RPC da Solana e a API da Hyperliquid respondam em simultâneo
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 4. Construímos o dicionário final garantindo que as chaves existem
        balances = {}

        # Processamos os resultados dos tokens (todos os elementos menos o último)
        for i in range(len(metadata)):
            chain_val, symbol, decimals = metadata[i]
            raw_balance = results[i]

            # Se houve erro na rede para este token específico, pomos a 0 e não crashamos o bot
            if isinstance(raw_balance, Exception):
                logging.error(f"❌ Erro ao obter saldo de {symbol} na chain {chain_val}: {raw_balance}")
                raw_balance = 0

            # Inicializa o sub-dicionário da chain se ele ainda não existir (Resolve o KeyError!)
            if chain_val not in balances:
                balances[chain_val] = {}

            balances[chain_val][symbol] = self.normalize_amount(raw_balance, decimals)

        # O último resultado do gather é o saldo da Hyperliquid
        hl_balance = results[-1]
        balances["hl"] = 0.0 if isinstance(hl_balance, Exception) else float(hl_balance)

        return balances

    def normalize_amount(self, raw_amount, decimals):
        """Converte o valor bruto da blockchain para unidades humanas"""
        if raw_amount == 0:
            return 0.0
        return raw_amount / (10 ** decimals)

    def check_viability_dynamic(self, watched_pair: WatchedPair, net_profit, amount_usdc, is_exit=False,
                                spread_percent=None):
        """
        Validação de viabilidade com metas de 0.5% para entrada e 0.7% para saída.
        """

        # --- 1. LÓGICA DE ENTRADA ---
        if not is_exit:
            # Exigimos 0.5% de lucro líquido para abrir a posição
            min_profit_required = amount_usdc * 0.008
            min_spread_required = 1

            success = net_profit >= min_profit_required and spread_percent >= min_spread_required

            if spread_percent > 0:
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
            roi_target = 0.012
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
