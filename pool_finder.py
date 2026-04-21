class PoolFinder:
    def __init__(self, web3_manager):
        self.web3_manager = web3_manager
        self.cache = {}  # Onde guardamos as pools já encontradas
        self.factories = {
            "UNI_V3": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
            "SUSHI_V3": "0x1af415a1EbA07a4986a52B6f2e7dE7003D82231e",
            "PAN_V3": "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
        }
        self.abi = [
            {"inputs": [{"name": "tokenA", "type": "address"}, {"name": "tokenB", "type": "address"},
                        {"name": "fee", "type": "uint24"}], "name": "getPool",
             "outputs": [{"name": "pool", "type": "address"}], "type": "function"}
        ]

    @property
    def w3(self):
        # Sempre que o PoolFinder usar "self.w3", ele pega no RPC ativo do momento
        return self.web3_manager.w3

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