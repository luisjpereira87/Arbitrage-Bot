class PoolFinder:
    def __init__(self, web3_manager):
        self.web3_manager = web3_manager
        self.cache = {}  # Onde guardamos as pools já encontradas
        self.factories = {
            "UNI_V3": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
            "SUSHI_V3": "0x1af415a1EbA07a4986a52B6f2e7dE7003D82231e",
            "PAN_V3": "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
            # "CAMELOT_V3": "0x1a3c9B1d2F0529D97f2afC5136Cc23e58f1FD35B",
            # "CAMELOT_V4": "0xBefC4b405041c5833f53412fF997ed2f697a2f37"
        }

        self.abi = [
            {"inputs": [{"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"},
                        {"name": "fee", "type": "uint24"}], "name": "getPool",
             "outputs": [{"name": "pool", "type": "address"}], "type": "function"}
        ]

        self.abi_pool = [
            # Para verificar se a pool tem dinheiro
            {
                "inputs": [],
                "name": "liquidity",
                "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
                "stateMutability": "view",
                "type": "function"
            },
            # Para Uniswap V3 (Preço e Tick)
            {
                "inputs": [],
                "name": "slot0",
                "outputs": [
                    {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
                    {"internalType": "int24", "name": "tick", "type": "int24"},
                    {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
                    {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
                    {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
                    {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
                    {"internalType": "bool", "name": "unlocked", "type": "bool"}
                ],
                "stateMutability": "view",
                "type": "function"
            },
            # Para Camelot/Algebra (Preço e Tick)
            {
                "inputs": [],
                "name": "globalState",
                "outputs": [
                    {"internalType": "uint160", "name": "price", "type": "uint160"},
                    {"internalType": "int24", "name": "tick", "type": "int24"},
                    {"internalType": "uint16", "name": "fee", "type": "uint16"},
                    {"internalType": "uint16", "name": "timepointIndex", "type": "uint16"},
                    {"internalType": "uint16", "name": "communityFeeToken0", "type": "uint16"},
                    {"internalType": "uint16", "name": "communityFeeToken1", "type": "uint16"},
                    {"internalType": "bool", "name": "unlocked", "type": "bool"}
                ],
                "stateMutability": "view",
                "type": "function"
            }
        ]

        self.abi_uniswap_factory = [
            {
                "inputs": [
                    {"internalType": "address", "name": "tokenA", "type": "address"},
                    {"internalType": "address", "name": "tokenB", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"}
                ],
                "name": "getPool",
                "outputs": [{"internalType": "address", "name": "", "type": "address"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]

        self.abi_algebra_factory = [
            {
                "inputs": [
                    {"internalType": "address", "name": "tokenA", "type": "address"},
                    {"internalType": "address", "name": "tokenB", "type": "address"}
                ],
                "name": "poolByPair",
                "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]

    @property
    def w3(self):
        # Sempre que o PoolFinder usar "self.w3", ele pega no RPC ativo do momento
        return self.web3_manager.w3

    """
    def get_pools(self, token_a, token_b, fee=500):
        addr_a = self.w3.to_checksum_address(token_a)
        addr_b = self.w3.to_checksum_address(token_b)

        pair_key = f"{addr_a}-{addr_b}-{fee}"
        if pair_key in self.cache: return self.cache[pair_key]

        pool_map = {}
        print(f"🔎 A procurar pools para Fee: {fee}...")

        for name, factory_addr in self.factories.items():
            try:
                f_addr = self.w3.to_checksum_address(factory_addr)
                contract = self.w3.eth.contract(address=f_addr, abi=self.abi)

                pool_addr = contract.functions.getPool(addr_a, addr_b, fee).call()

                # O segredo está aqui: ver o que a Factory responde
                if pool_addr == "0x0000000000000000000000000000000000000000":
                    # --- NOVO: VALIDAÇÃO DE LIQUIDEZ MÍNIMA ---
                    # Criamos o contrato da pool para ver se ela tem liquidez ativa
                    pool_contract = self.w3.eth.contract(address=pool_addr, abi=self.abi_pool)
                    liquidez = pool_contract.functions.liquidity().call()

                    if liquidez < 10 ** 14:  # Filtro agressivo (podes ajustar para 10**15)
                        print(f"⚠️ {name}: Pool ignorada (Liquidez insuficiente: {liquidez})")
                        continue
                    print(f"❌ {name}: Não tem pool para esta fee.")
                else:
                    print(f"✅ {name}: Pool encontrada em {pool_addr}")
                    pool_map[name] = pool_addr


            except Exception as e:
                # --- A INJEÇÃO DE SEGURANÇA AQUI ---
                if "429" in str(e) or "limit" in str(e).lower():
                    print(f"🛑 [PoolFinder] Limite atingido no {name}. Rodando RPC...")
                    self.web3_manager.rotate_rpc()

                    # Tentativa de recuperação imediata para esta Factory específica
                    try:
                        # Agora usando o novo RPC
                        f_addr = self.w3.to_checksum_address(factory_addr)
                        pool_addr = self.w3.eth.contract(address=f_addr, abi=self.abi).functions.getPool(addr_a, addr_b,
                                                                                                         fee).call()
                        if pool_addr != "0x0000000000000000000000000000000000000000":
                            pool_map[name] = pool_addr
                    except:
                        pass
                else:
                    print(f"⚠️ {name}: Erro técnico -> {e}")

        self.cache[pair_key] = pool_map
        return pool_map
        """

    def get_pools(self, token_a, token_b, fee=500):
        addr_a = self.w3.to_checksum_address(token_a)
        addr_b = self.w3.to_checksum_address(token_b)

        pair_key = f"{addr_a}-{addr_b}-{fee}"
        if pair_key in self.cache: return self.cache[pair_key]

        pool_map = {}

        # Ordenar tokens para algumas factories que exigem ordem (ex: Algebra/Camelot)
        t_min, t_max = (addr_a, addr_b) if int(addr_a, 16) < int(addr_b, 16) else (addr_b, addr_a)

        for name, factory_addr in self.factories.items():
            try:
                f_addr = self.w3.to_checksum_address(factory_addr)

                # --- DIFERENCIAÇÃO DE ARQUITETURA ---
                if "CAMELOT" in name or "ALGEBRA" in name:
                    # Algebra (Camelot v3/v4) usa poolByPair(tokenA, tokenB)
                    # Nota: Elas ignoram o parâmetro 'fee' no getPool, as taxas são dinâmicas
                    contract = self.w3.eth.contract(address=f_addr, abi=self.abi_algebra_factory)
                    pool_addr = contract.functions.poolByPair(addr_a, addr_b).call()
                else:
                    # Uniswap / Pancake / Sushi usam getPool(tokenA, tokenB, fee)
                    contract = self.w3.eth.contract(address=f_addr, abi=self.abi_uniswap_factory)
                    pool_addr = contract.functions.getPool(addr_a, addr_b, fee).call()

                # Validação do endereço retornado
                if pool_addr == "0x0000000000000000000000000000000000000000":
                    # print(f"❌ {name}: Sem pool para {fee}")
                    continue

                # --- VALIDAÇÃO DE LIQUIDEZ ---
                # Só entramos aqui se pool_addr for um endereço válido (diferente de 0x0)
                try:
                    pool_contract = self.w3.eth.contract(address=pool_addr, abi=self.abi_pool)
                    liquidez = pool_contract.functions.liquidity().call()

                    if liquidez < 10 ** 15:  # Ajustado para ser menos restritivo inicialmente
                        # print(f"⚠️ {name}: Liquidez Baixa ({liquidez})")
                        continue

                    print(f"✅ {name}: Pool Ativa em {pool_addr}")
                    pool_map[name] = pool_addr
                except:
                    continue

            except Exception as e:
                if any(x in str(e) for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                    self.web3_manager.rotate_rpc()
                print(f"⚠️ {name} Erro: {e}")

        self.cache[pair_key] = pool_map
        return pool_map
