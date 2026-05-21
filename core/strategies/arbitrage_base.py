import asyncio
import itertools
import logging
import socket
import time

import aiohttp
from eth_abi import decode

from core.dclass.chains_enum import Chains
from core.dclass.config_json import Config
from core.dclass.dex_opportunity_dclass import DexOpportunity
from core.dclass.watched_pair_dclass import WatchedPair


class ArbitrageBase:
    def __init__(self, web3_manager, config: Config):
        self.web3_manager = web3_manager
        self.config = config
        self.capital = 100  # Default capital in USD
        self.MIN_LIQUIDITY = 10 ** 17

        self.pool_static_cache = {}

        self.tokens = self.config.tokens

        # Mapping token addresses to decimals for fast lookup
        self.decimal_map = {
            info.address.lower(): info.decimals
            for info in self.config.tokens.values()
        }

        # Mapping addresses to human-readable names for logging
        self.name_map = {
            info.address.lower(): name
            for name, info in self.config.tokens.items()
        }

        self.pool_abi = [
            {"inputs": [], "name": "slot0", "outputs": [{"type": "uint160", "name": "sqrtPriceX96"}],
             "type": "function"},
            {"inputs": [], "name": "token0", "outputs": [{"type": "address"}], "type": "function"},
            {"inputs": [], "name": "token1", "outputs": [{"type": "address"}], "type": "function"},
            {"inputs": [], "name": "fee", "outputs": [{"type": "uint24"}], "type": "function"},
            {"inputs": [], "name": "liquidity", "outputs": [{"type": "uint128"}], "type": "function"}
        ]

        self.low_liquidity_cache = {}  # {addr: timestamp_da_exclusao}
        self.LIQUIDITY_TTL = 3600  # Ignorar por 1 hora (3600 segundos)

        self.session = None
        self.pool_blacklist = {}
        self.last_batch_results = {}

    @property
    def w3(self):
        return self.web3_manager.w3

    def get_token_decimals(self, token_address):
        addr = token_address.lower()
        # Fallback to map or default 18
        return self.decimal_map.get(addr, 18)

    def get_dynamic_routes(self, token_base="USDC", is_triangular=False):
        """
        Gera rotas simples e triangulares baseadas nos tokens disponíveis
        e valida se as pools existem no cache estático.
        """
        tokens_disponiveis = list(self.config.tokens.keys())
        # Lista de tokens excluindo o base (ex: USDC)
        outros_tokens = [t for t in tokens_disponiveis if t != token_base]

        rotas_finais = []

        if is_triangular is not True:
            # --- 1. ROTAS SIMPLES (Base -> Token -> Base) ---
            for t in outros_tokens:
                rota = [token_base, t]
                rotas_finais.append(rota)
                if self._check_route_cache(rota):
                    rotas_finais.append(rota)
        # --- 2. ROTAS TRIANGULARES (Base -> T1 -> T2 -> Base) ---
        # Usamos permutations para testar USDC -> ARB -> ETH -> USDC
        # e também USDC -> ETH -> ARB -> USDC
        if is_triangular is True:
            for combo in itertools.permutations(outros_tokens, 2):
                t1, t2 = combo
                rota = [token_base, t1, t2]
                rotas_finais.append(rota)
                if self._check_route_cache(rota):
                    rotas_finais.append(rota)

        print(f"🧬 Gerador Dinâmico: {len(rotas_finais)} rotas validadas pelo cache.")
        return rotas_finais

    def _check_route_cache(self, lista_tokens):
        """
        Verifica se existe uma pool no cache para cada salto da rota.
        Ex: Para [USDC, ARB, USDC], verifica se existe pool USDC/ARB e ARB/USDC.
        """
        for i in range(len(lista_tokens) - 1):
            t_in = self.config.tokens.get(lista_tokens[i]).address.lower()
            t_out = self.config.tokens.get(lista_tokens[i + 1]).address.lower()

            # Procura no teu pool_static_cache se existe alguma pool com estes dois tokens
            pool_encontrada = False
            for pool_addr, data in self.pool_static_cache.items():
                # Uma pool de Uniswap V3 tem t0 e t1 fixos, mas a rota pode ser em qualquer direção
                if (data['t0'] == t_in and data['t1'] == t_out) or \
                        (data['t0'] == t_out and data['t1'] == t_in):
                    pool_encontrada = True
                    break

            if not pool_encontrada:
                return False  # Se um salto falhar, a rota inteira é inválida

        return True

    def get_quote(self, pool_address, token_in, token_out):
        try:
            pool_contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(pool_address),
                abi=self.pool_abi
            )

            # Multi-call or batching would be better, but keeping it simple for now
            slot0 = pool_contract.functions.slot0().call()
            sqrt_price_x96 = slot0[0] if isinstance(slot0, (list, tuple)) else slot0

            t0 = pool_contract.functions.token0().call().lower()
            fee = pool_contract.functions.fee().call()

            d0 = self.get_token_decimals(t0)
            t1_addr = pool_contract.functions.token1().call().lower()
            d1 = self.get_token_decimals(t1_addr)

            # Core Uniswap V3 Price Formula
            price_base = ((sqrt_price_x96 / (2 ** 96)) ** 2) * (10 ** d0 / 10 ** d1)

            if token_in.lower() == t0:
                return price_base, True, fee  # zeroForOne = True
            else:
                return 1 / price_base, False, fee  # zeroForOne = False

        except Exception as e:
            if "429" in str(e).lower():
                self.web3_manager.rotate_rpc()
                return self.get_quote(pool_address, token_in, token_out)
            return None

    def build_pool_cache(self, pools_to_track):
        """
        Preenche o cache estático com tokens, decimais e fees.
        Executa apenas uma vez no início para economizar milhares de chamadas RPC.
        """
        print(f"📦 [CACHE] A mapear dados estáticos de {len(pools_to_track)} pools...")

        for addr in pools_to_track:
            try:
                addr_c = self.w3.to_checksum_address(addr)
                contract = self.w3.eth.contract(address=addr_c, abi=self.pool_abi)

                # Chamadas únicas ao RPC
                t0 = contract.functions.token0().call().lower()
                t1 = contract.functions.token1().call().lower()
                fee = contract.functions.fee().call()

                # Obtém decimais (usa o teu decimal_map ou consulta a rede)
                d0 = self.get_token_decimals(t0)
                d1 = self.get_token_decimals(t1)

                self.pool_static_cache[addr.lower()] = {
                    "t0": t0,
                    "t1": t1,
                    "d0": d0,
                    "d1": d1,
                    "fee": fee
                }
            except Exception as e:
                print(f"⚠️ Erro ao carregar cache da pool {addr}: {e}")

        print(f"✅ [CACHE] Sucesso! {len(self.pool_static_cache)} pools prontas para cálculo local.")

    def _calculate_quote_local(self, pool_addr, token_in, token_out, sqrt_price_x96):
        """Calcula o quote usando dados do cache e o preço fornecido."""
        if not sqrt_price_x96: return None

        data = self.pool_static_cache.get(pool_addr.lower())
        if not data: return None

        # Fórmula Uniswap V3: price = (sqrtPrice / 2^96)^2 * (10^d0 / 10^d1)
        price_base = ((sqrt_price_x96 / (2 ** 96)) ** 2) * (10 ** data['d0'] / 10 ** data['d1'])

        if token_in.lower() == data['t0']:
            return price_base, True, data['fee'], None  # zeroForOne = True
        else:
            return 1 / price_base, False, data['fee'], None  # zeroForOne = False

    def get_quotes_batch(self, pool_addresses):
        MULTICALL_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"
        MC_ABI = '[{"inputs":[{"components":[{"internalType":"address","name":"target","type":"address"},{"internalType":"bool","name":"allowFailure","type":"bool"},{"internalType":"bytes","name":"callData","type":"bytes"}],"name":"calls","type":"tuple[]"}],"name":"aggregate3","outputs":[{"components":[{"internalType":"bool","name":"success","type":"bool"},{"internalType":"bytes","name":"returnData","type":"bytes"}],"name":"returnData","type":"tuple[]"}],"stateMutability":"view","type":"function"}]'

        slot0_selector = "0x3850c7bd"
        liquidity_selector = "0x1a686502"
        balance_selector = "0x70a08231"  # balanceOf(address)

        current_time = time.time()
        active_pools = []
        for addr in pool_addresses:
            addr_l = addr.lower()
            if addr_l in self.low_liquidity_cache:
                if current_time < self.low_liquidity_cache[addr_l]:
                    continue
                else:
                    del self.low_liquidity_cache[addr_l]
            active_pools.append(addr)

        if not active_pools: return {}

        calls = []
        for addr in active_pools:
            addr_l = addr.lower()
            c_addr = self.w3.to_checksum_address(addr)

            # 1. slot0 (Preço)
            calls.append({'target': c_addr, 'allowFailure': True, 'callData': slot0_selector})

            # 2. liquidity (L)
            calls.append({'target': c_addr, 'allowFailure': True, 'callData': liquidity_selector})

            # 3. balanceOf (Saldo Real na Carteira da Pool)
            # Precisamos do endereço do Token0 que está no nosso cache estático
            static_data = self.pool_static_cache.get(addr_l)
            if static_data:
                token0_addr = self.w3.to_checksum_address(static_data['t0'])
                # Encode manual do parâmetro (address para bytes32)
                # Remove o 0x e preenche com zeros à esquerda até ter 64 caracteres
                addr_padded = addr_l.replace("0x", "").zfill(64)
                balance_call_data = balance_selector + addr_padded

                calls.append({'target': token0_addr, 'allowFailure': True, 'callData': balance_call_data})
            else:
                # Fallback caso a pool não esteja no cache (não deve acontecer)
                calls.append({'target': c_addr, 'allowFailure': True, 'callData': "0x"})

        try:
            mc_contract = self.w3.eth.contract(address=MULTICALL_ADDRESS, abi=MC_ABI)
            raw_results = mc_contract.functions.aggregate3(calls).call()

            decoded_prices = {}
            # Agora o passo do loop é 3
            for i in range(0, len(raw_results), 3):
                res_price = raw_results[i]
                res_liq = raw_results[i + 1]
                res_bal = raw_results[i + 2]
                pool_addr = active_pools[i // 3].lower()
                static = self.pool_static_cache.get(pool_addr)

                if res_price[0] and res_liq[0] and res_bal[0]:
                    liq = decode(['uint128'], res_liq[1][:32])[0]

                    # Descodificar o Saldo Real
                    bal_raw = decode(['uint256'], res_bal[1])[0]
                    bal_human = bal_raw / (10 ** static['d0']) if static else 0

                    """
                    # --- DEBUG REAL DO VALOR EM CARTEIRA ---
                    if "0xbbe36e6f0331c6a36ab44bc8421e28e1a1871c1e" in pool_addr:
                        token_symbol = self.name_map.get(static['t0'], "T0")
                        print(f"🕵️ Pool de $11 detetada! Saldo real de {token_symbol}: {bal_human:.2f}")
                        print(f"📊 Liquidez (L): {liq}")
                    """

                    is_rica = bal_human > 1000
                    is_liquida = liq >= self.MIN_LIQUIDITY

                    # --- FILTRO HÍBRIDO (Saldo < $50 OU Liquidez < 10^17) ---
                    # Se o saldo de Token0 for menor que 50 (ex: 50 USDC), banimos.
                    if not (is_rica or is_liquida):
                        print(f"🚫 Pool {pool_addr} banida. Saldo: {bal_human:.2f} | L: {liq}")
                        self.low_liquidity_cache[pool_addr] = current_time + self.LIQUIDITY_TTL
                        continue

                    price = decode(['uint160'], res_price[1][:32])[0]
                    decoded_prices[pool_addr] = price
                    self.last_batch_results = decoded_prices

            return decoded_prices

        except Exception as e:
            if any(x in str(e) for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                self.web3_manager.rotate_rpc()
                return self.get_quotes_batch(pool_addresses)
            print(f"❌ Erro crítico no envio: {e}")
            return {}

    # --- 1. MÉTODOS DE CÁLCULO CENTRALIZADOS ---

    def calculate_net_metrics(self, price_hl, price_dex, amount_usdc, fee_ppm, gas_usdc):
        """
        A 'Fórmula Única' para ROI e Spread.
        fee_ppm: fee da DEX em partes por milhão (ex: 3000 para 0.3%)
        """
        fee_dex_percent = fee_ppm / 1_000_000

        # 1. Quantos tokens compro na DEX com o capital disponível
        tokens_bought = (amount_usdc * (1 - fee_dex_percent)) / price_dex

        # 2. Valor bruto da venda na Hyperliquid (já com taxas de lá: 0.035% * 2)
        total_recebido_hl = (tokens_bought * price_hl) * (1 - 0.00070)

        # 3. Custos adicionais
        custo_reverter_dex = (tokens_bought * price_dex) * fee_dex_percent
        total_gas = gas_usdc * 2  # Abertura + Fecho (será 0 na Solana)

        # 4. LUCRO REAL LÍQUIDO
        net_profit = total_recebido_hl - amount_usdc - custo_reverter_dex - total_gas
        spread_percent = ((price_hl / price_dex) - 1) * 100

        return net_profit, spread_percent

    # --- 2. CONSULTA DE PREÇOS AGNÓSTICA ---

    async def fetch_dex_price(self, pair: WatchedPair, pool_addr, usdc_balance_to_trade: float):
        """
        Decide se consulta o cache do Multicall (ARB) ou a API da Jupiter (SOL).
        """
        if pair.chain == Chains.SOLANA:
            return await self._get_solana_jupiter_quote(pair, usdc_balance_to_trade)
        else:
            # Consulta o cache preenchido pelo get_quotes_batch
            sqrt_price = self.last_batch_results.get(pool_addr.lower())
            if sqrt_price:
                return self._calculate_quote_local(pool_addr, pair.addr_a, pair.addr_b, sqrt_price)
        return None

    async def _get_solana_jupiter_quote(self, pair: WatchedPair, usdc_balance_to_trade: float):
        """
        Consulta a Jupiter API com proteção de DNS, ritmo controlado e lista de Failover.
        """
        if usdc_balance_to_trade <= 0:
            return None

        # 1. Lista de Endpoints da Jupiter por ordem de preferência
        jupiter_urls = [
            "https://public.jupiterapi.com/quote",  # Principal (Alta Performance)
            "https://quote-api.jup.ag/v6/quote"  # Reserva (Oficial)
        ]

        # 2. Controlador Estático de Ritmo (A tua barreira de 400ms)
        if not hasattr(self, 'last_jup_call'):
            self.last_jup_call = 0.0

        agora = time.time()
        tempo_decorrido = agora - self.last_jup_call
        if tempo_decorrido < 0.4:
            await asyncio.sleep(0.4 - tempo_decorrido)
        self.last_jup_call = time.time()

        # 3. Garantir sessão HTTP
        if self.session is None or self.session.closed:
            # Mantemos o teu conector IPv4 com cache para evitar problemas de rede
            connector = aiohttp.TCPConnector(family=socket.AF_INET, ttl_dns_cache=300)
            self.session = aiohttp.ClientSession(connector=connector)

        amount_in_base = int(usdc_balance_to_trade * (10 ** pair.decimal_a))
        params = {
            "inputMint": pair.addr_a,
            "outputMint": pair.addr_b,
            "amount": str(amount_in_base),
            "slippageBps": 10,
            "restrictIntermediateTokens": "true"
        }
        headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

        # 4. Loop de Failover: Percorre as URLs se houver falha
        for url in jupiter_urls:
            try:
                async with self.session.get(url, params=params, headers=headers, timeout=4) as resp:

                    # Se uma URL der Rate Limit, salta imediatamente para a próxima sem esperar!
                    if resp.status == 429:
                        logging.warning(f"⚠️ Rate Limit (429) na URL: {url}. A tentar rota alternativa...")
                        continue

                    if resp.status == 200:
                        data = await resp.json()
                        out_raw = int(data['outAmount'])
                        amount_out_human = out_raw / (10 ** pair.decimal_b)
                        price_dex = amount_out_human / usdc_balance_to_trade

                        return price_dex, True, 1000, data

                    else:
                        # Se der outro erro HTTP (ex: 500, 502), regista e testa a próxima URL
                        logging.warning(f"⚠️ URL {url} devolveu status {resp.status}. Saltando para alternativa...")
                        continue

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # Captura timeouts ou quedas de DNS específicas desta URL
                logging.warning(f"🌐 Falha de conexão na URL {url}: {type(e).__name__}. A tentar failover...")
                continue

        # Se o loop terminar e nenhuma URL funcionar
        logging.error("❌ Todas as URLs da Jupiter falharam neste ciclo.")
        return None

    # --- 3. O NOVO LOCALIZADOR DE OPORTUNIDADES (Refatorado do teu original) ---

    async def find_best_dex_opportunity(self, pair: WatchedPair, price_hl: float, usdc_balance_to_trade: float,
                                        gas_cost_usdc: float):
        best_opportunity = None

        # Filtro inicial de blacklist por par/pool
        for dex_name, pool_addr in pair.pools_map.items():
            p_addr_l = pool_addr.lower()
            if p_addr_l in self.pool_blacklist:
                if time.time() < self.pool_blacklist[p_addr_l]:
                    continue
                else:
                    del self.pool_blacklist[p_addr_l]

            # Obter cotação (Agnóstico)
            quote = await self.fetch_dex_price(pair, p_addr_l, usdc_balance_to_trade)

            if not quote: continue

            raw_price_dex, direction, fee_dex_ppm, data_quote = quote
            price_dex = 1 / raw_price_dex
            # price_dex = raw_price_dex

            logging.info(
                f"Dex: {dex_name}, Pair: {pair.symbol_a}/{pair.symbol_b}, Price Dex: {price_dex}, Price HL: {price_hl}")

            # Determinar custo de gás baseado na rede
            current_gas = 0.05 if pair.chain == Chains.SOLANA else gas_cost_usdc

            # Cálculo de Métricas Centralizado
            net_profit, spread_percent = self.calculate_net_metrics(
                price_hl, price_dex, usdc_balance_to_trade, fee_dex_ppm, current_gas
            )

            # Criar objeto de oportunidade (DexOpportunity)
            current_opp = DexOpportunity(
                chain=pair.chain,
                strategy='MULTI_CHAIN',
                profit=net_profit,
                spread=spread_percent,
                symbol=pair.symbol_b,
                price_dex=price_dex,
                price_hl=price_hl,
                pool_addr=pool_addr,
                dex_name=dex_name,
                dex_fee=fee_dex_ppm,
                direction=direction,
                data_quote=data_quote
            )

            if best_opportunity is None or current_opp.profit > best_opportunity.profit:
                best_opportunity = current_opp
        return best_opportunity
