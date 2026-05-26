import itertools
import time
from typing import Any

from eth_abi import decode

from core.dclass.chains_enum import Chains
from core.dclass.config_json import Config
from core.dclass.dex_quote_dclass import DexQuote
from core.dclass.watched_pair_dclass import WatchedPair
from core.pools.pool_finder import PoolFinder


class UniswapClient:
    def __init__(self, web3_manager, config: Config):
        self.web3_manager = web3_manager
        self.config = config

        self.finder = PoolFinder(self.web3_manager)

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

        self.pool_blacklist = {}
        self.last_batch_results = {}

        all_pools_for_cache, all_pool_addrs = self.init_pools()

        self.build_pool_cache(all_pools_for_cache)

        # if all_pool_addrs:
        self.get_quotes_batch(all_pool_addrs)

    def init_pools(self) -> tuple[list[Any], list[str]]:
        all_pools_for_cache = set()
        fee_tiers = self.config.fees
        watched_pairs = []

        for symbol_a, symbol_b, hl_pair, chain in self.config.multi_chain:

            if chain != Chains.ARBITRUM.value:
                continue

            # 1. Obter dados do token (mantendo case-sensitive para Solana)
            token_a_data = self.config.tokens.get(symbol_a)
            token_b_data = self.config.tokens.get(symbol_b)

            if token_a_data is None or token_b_data is None:
                continue

            addr_a = token_a_data.address
            addr_b = token_b_data.address
            dec_a = token_a_data.decimals
            dec_b = token_b_data.decimals

            pair_pools: dict = {}
            z4o = int(addr_a, 16) < int(addr_b, 16)

            # LÓGICA ARBITRUM (EVM)
            addr_a_l = addr_a.lower()
            addr_b_l = addr_b.lower()

            # Ordenar para a Uniswap (t0 é o menor hexadecimal)
            t0, t1 = sorted([addr_a_l, addr_b_l])

            for fee in fee_tiers:
                pool_found = self.finder.get_pools(t0, t1, fee)
                if pool_found:
                    for dex_name, addr in pool_found.items():
                        unique_key = f"{dex_name}_{fee}"
                        pair_pools[unique_key] = addr.lower()
                        all_pools_for_cache.add(addr.lower())

            watched_pairs.append(
                WatchedPair(
                    addr_a=addr_a,
                    addr_b=addr_b,
                    symbol_a=symbol_a,
                    symbol_b=symbol_b,
                    decimal_a=dec_a,
                    decimal_b=dec_b,
                    hl_pair=hl_pair,
                    pools_map=pair_pools,
                    z4o=z4o,
                    chain=Chains.from_str(chain),
                ))

        all_pool_addrs = [
            addr for p in watched_pairs
            if p.chain == Chains.ARBITRUM and getattr(p, 'pools_map', None)
            for addr in p.pools_map.values()
        ]

        return list(all_pools_for_cache), all_pool_addrs

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

        tokens_disponiveis = [
            symbol for symbol, info in self.config.tokens.items()
            if info.chain == Chains.ARBITRUM.value
            # .value se 'pair.chain' for um Enum, ou apenas 'pair.chain' se for string
        ]

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

    def calculate_quote_local(self, pool_addr, token_in, token_out) -> (DexQuote | None):
        """Calcula o quote usando dados do cache e o preço fornecido."""

        sqrt_price_x96 = self.last_batch_results.get(pool_addr.lower())

        if not sqrt_price_x96: return None

        data = self.pool_static_cache.get(pool_addr.lower())
        if not data: return None

        # Fórmula Uniswap V3: price = (sqrtPrice / 2^96)^2 * (10^d0 / 10^d1)
        price_base = ((sqrt_price_x96 / (2 ** 96)) ** 2) * (10 ** data['d0'] / 10 ** data['d1'])

        if token_in.lower() == data['t0']:
            return DexQuote(price_base, None, True, data['fee'], None)
            # return price_base, True, data['fee'], None  # zeroForOne = True
        else:
            return DexQuote(1 / price_base, None, False, data['fee'], None)
            # return 1 / price_base, False, data['fee'], None  # zeroForOne = False

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
