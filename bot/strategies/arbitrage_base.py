import time

from eth_abi import decode


class ArbitrageBase:
    def __init__(self, web3_manager, config):
        self.web3_manager = web3_manager
        self.config = config
        self.capital = 100  # Default capital in USD
        self.MIN_LIQUIDITY = 10 ** 14

        self.pool_static_cache = {}

        # Mapping token addresses to decimals for fast lookup
        self.decimal_map = {
            info["addr"].lower(): info["dec"]
            for info in self.config["tokens"].values()
        }

        # Mapping addresses to human-readable names for logging
        self.name_map = {
            info["addr"].lower(): name
            for name, info in self.config["tokens"].items()
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
        self.LIQUIDITY_TTL = 300  # Ignorar por 1 hora (3600 segundos)

    @property
    def w3(self):
        return self.web3_manager.w3

    def get_token_decimals(self, token_address):
        addr = token_address.lower()
        # Fallback to map or default 18
        return self.decimal_map.get(addr, 18)

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
            return price_base, True, data['fee']  # zeroForOne = True
        else:
            return 1 / price_base, False, data['fee']  # zeroForOne = False

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

            return decoded_prices

        except Exception as e:
            if "429" in str(e):
                self.web3_manager.rotate_rpc()
                return self.get_quotes_batch(pool_addresses)
            print(f"❌ Erro crítico no envio: {e}")
            return {}
