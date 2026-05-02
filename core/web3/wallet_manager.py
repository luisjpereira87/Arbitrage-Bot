from core.config.properties_base import PropertiesBase
from core.web3.wallet_base import WalletBase


class WalletManager(WalletBase):
    def __init__(self, web3_manager, properties: PropertiesBase):
        self.web3_manager = web3_manager

        # Carregar chaves (Garante que o .env está carregado antes de instanciar esta classe)
        self.private_key = properties.PRIVATE_KEY

        if not self.private_key:
            raise ValueError("❌ PRIVATE_KEY não encontrada no ficheiro .env")

        # 2. Configurações de Endereços
        # Nota: Usamos self.w3 (a propriedade dinâmica que criaremos abaixo)
        self.account = self.w3.eth.account.from_key(self.private_key)

        print(self.account.address)

        self.executor_address = self.w3.to_checksum_address(properties.CONTRACT_ADDRESS)

        self._current_nonce = None

        # self.executor_address = self.w3.to_checksum_address(os.getenv("CONTRACT_ADDRESS"))
        self.usdc_address = self.w3.to_checksum_address(properties.USDC_ADDRESS_ARBITRUM)

        self.executor_abi = properties.CONTRACT_ABI

        # ABI mínima para o Approve do USDC
        self.erc20_abi = properties.ERC20_ABI

        print(f"✅ WalletManager Conectado via {self.web3_manager.rpcs[self.web3_manager.current_index]}")
        print(f"💳 Carteira: {self.account.address}")

    @property
    def w3(self):
        """Acesso dinâmico ao RPC ativo no Manager"""
        return self.web3_manager.w3

    # Propriedade para o contrato sempre usar o w3 atual
    @property
    def executor_contract(self):
        return self.w3.eth.contract(address=self.executor_address, abi=self.executor_abi)

    def check_and_approve_executor(self, amount_usd: float = 100.0):
        """Dá permissão ao contrato para gastar USDC usando o RPC atual"""
        try:
            token_contract = self.w3.eth.contract(address=self.usdc_address, abi=self.erc20_abi)
            amount_wei = int(amount_usd * 10 ** 6)

            allowance = token_contract.functions.allowance(self.account.address, self.executor_address).call()

            if allowance < amount_wei:
                print(f"🔓 Autorizando contrato...")

                # Cálculo de taxas usando o w3 atual
                latest_block = self.w3.eth.get_block('latest')
                base_fee = latest_block['baseFeePerGas']
                priority_fee = self.w3.to_wei('0.1', 'gwei')
                max_fee = int(base_fee * 1.2) + priority_fee

                tx = token_contract.functions.approve(self.executor_address, 2 ** 256 - 1).build_transaction({
                    'from': self.account.address,
                    'nonce': self.w3.eth.get_transaction_count(self.account.address),
                    'gas': 100000,
                    'maxFeePerGas': max_fee,
                    'maxPriorityFeePerGas': priority_fee,
                    'chainId': 42161
                })

                signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
                tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

                print(f"✅ Approve enviado: {self.w3.to_hex(tx_hash)}")
                self.w3.eth.wait_for_transaction_receipt(tx_hash)
                return True
            return True
        except Exception as e:
            if any(x in str(e) for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                self.web3_manager.rotate_rpc()
                return self.check_and_approve_executor(amount_usd)  # Tenta de novo com novo RPC
            print(f"❌ Erro no Approve: {e}")
            return False

    """
    def send_transaction(self, pools_list: list[str], dir_list: list[bool], tokens_list: list[str], amount_usd: float):
        # 1. TRATAMENTO DO VALOR (Garante que não multiplicamos o que já está multiplicado)
        # Se o valor for maior que 1 milhão, assumimos que já está em Wei
        if amount_usd > 1000000:
            val_in_wei = int(amount_usd)
        else:
            val_in_wei = int(amount_usd * 10 ** 6)

        # --- DEBUG ATUALIZADO ---
        # usdc_address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
        usdc_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
                     "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]
        usdc_contract = self.w3.eth.contract(address=self.w3.to_checksum_address(self.usdc_address), abi=usdc_abi)

        contract_balance = usdc_contract.functions.balanceOf(self.executor_address).call()

        print(f"\n--- 🕵️ RELATÓRIO DE EXECUÇÃO ---")
        print(f"📍 Contrato: {self.executor_address}")
        print(f"💰 Saldo Real no Contrato: {contract_balance / 10 ** 6:.2f} USDC")
        print(f"📉 Pedido p/ Arbitragem: {val_in_wei / 10 ** 6:.2f} USDC")
        print(f"🔢 Valor em Wei: {val_in_wei}")

        if contract_balance < val_in_wei:
            print(f"❌ ERRO: Saldo insuficiente! Faltam {(val_in_wei - contract_balance) / 10 ** 6:.2f} USDC")
        print(f"---------------------------------\n")
        # --- FIM DEBUG ---

        #Executa a arbitragem atómica usando o RPC do momento
        try:
            # Formatação de endereços
            pools_ck = [self.w3.to_checksum_address(p) for p in pools_list]
            tokens_ck = [self.w3.to_checksum_address(t) for t in tokens_list]

            # 1. Simulação (Dry Run)
            try:
                self.executor_contract.functions.startArbitrage(
                    val_in_wei, pools_ck, dir_list, tokens_ck
                ).call({'from': self.account.address})
                print("✅ Simulação passou com sucesso!")
            except Exception as sim_err:
                if "429" in str(sim_err):
                    self.web3_manager.rotate_rpc()
                    return self.send_transaction(pools_list, dir_list, tokens_list, amount_usd)
                print(f"⚠️ Simulação falhou: {sim_err}")
                return None

            # 2. Envio Real
            latest_block = self.w3.eth.get_block('latest')
            base_fee = latest_block.get('baseFeePerGas', self.w3.to_wei('0.1', 'gwei'))

            real_nonce = self.w3.eth.get_transaction_count(self.account.address)

            # Se o nosso contador interno for menor que o real, corrigimos
            if self._current_nonce is None or self._current_nonce < real_nonce:
                self._current_nonce = real_nonce

            tx = self.executor_contract.functions.startArbitrage(
                val_in_wei, pools_ck, dir_list, tokens_ck
            ).build_transaction({
                'from': self.account.address,
                'nonce': self._current_nonce,
                'gas': 1000000,
                # Vamos simplificar o gás para o Ganache não reclamar do EIP-1559
                'gasPrice': self.w3.eth.gas_price,
                'chainId': self.w3.eth.chain_id  # <--- DINÂMICO: Ele vai ler o ID do Ganache
            })

            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)

            self._current_nonce += 1
            final_balance_contract = usdc_contract.functions.balanceOf(self.executor_address).call()

            print(f"💰 Saldo Real final no Contrato: {final_balance_contract / 10 ** 6:.2f} USDC")
            return self.w3.to_hex(tx_hash)

        except Exception as e:
            if "nonce" in str(e).lower():
                self._current_nonce = self.w3.eth.get_transaction_count(self.account.address)
                print(f"🔄 Erro de sincronização. Novo Nonce base: {self._current_nonce}")

            if any(x in str(e) for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                self.web3_manager.rotate_rpc()
                return self.send_transaction(pools_list, dir_list, tokens_list, amount_usd)
            print(f"❌ Erro crítico no envio: {e}")
            return None
    """

    def send_transaction(self, pools_list: list[str], dir_list: list[bool], tokens_list: list[str], amount_usd: float):
        # 1. TRATAMENTO DO VALOR
        # Se amount_usd já for o valor em WEI (ex: vindo da sequência de saída), não multiplicamos
        if amount_usd > 1000000:
            val_in_wei = int(amount_usd)
        else:
            val_in_wei = int(amount_usd * 10 ** 6)

        # --- DEBUG/REPORTE ATUALIZADO ---
        # Forçamos o checksum aqui para evitar leituras nulas
        t_address = self.w3.to_checksum_address(tokens_list[0])
        e_address = self.w3.to_checksum_address(self.executor_address)

        usdc_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
                     "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]

        token_contract = self.w3.eth.contract(address=t_address, abi=usdc_abi)

        # Chamada direta e limpa
        contract_balance = token_contract.functions.balanceOf(e_address).call()

        # Debug de emergência: imprime o valor bruto para descartar erros de divisão
        print(f"\n--- 🕵️ RELATÓRIO DE EXECUÇÃO MAINNET ---")
        print(f"📍 Contrato: {e_address}")
        print(f"🪙 Token: {t_address}")
        print(f"🔢 Saldo Bruto (Wei): {contract_balance}")
        print(f"💰 Saldo Formatado: {contract_balance / 10 ** 6:.4f}")
        print(f"📉 Pedido p/ Swap (Wei): {val_in_wei}")

        if contract_balance < val_in_wei:
            print(f"❌ ERRO: Saldo insuficiente no contrato! ({contract_balance} < {val_in_wei})")
            return None
        print(f"---------------------------------\n")

        try:
            # Formatação de endereços (Checksum é vital na Mainnet)
            pools_ck = [self.w3.to_checksum_address(p) for p in pools_list]
            tokens_ck = [self.w3.to_checksum_address(t) for t in tokens_list]

            # 1. Simulação (Dry Run)
            try:
                self.executor_contract.functions.startArbitrage(
                    val_in_wei, pools_ck, dir_list, tokens_ck
                ).call({'from': self.account.address})
                print("✅ Simulação passou!")
            except Exception as sim_err:
                print(f"⚠️ Simulação falhou: {sim_err}")
                return None

            # 2. Gestão de Nonce
            real_nonce = self.w3.eth.get_transaction_count(self.account.address)
            if self._current_nonce is None or self._current_nonce < real_nonce:
                self._current_nonce = real_nonce

            # 3. Construção da Transação
            # Usamos parâmetros EIP-1559 recomendados para Arbitrum
            # 3. Construção da Transação
            # Pegamos o base_fee atual do bloco e adicionamos uma margem de 20%
            latest_block = self.w3.eth.get_block('latest')
            base_fee = latest_block.get('baseFeePerGas', self.w3.eth.gas_price)
            # Margem de segurança de 20% para garantir que entra no bloco
            max_fee = int(base_fee * 1.2)
            # Priority fee (gorjeta para o validador) - Arbitrum costuma aceitar 0.01 a 0.1 gwei
            priority_fee = self.w3.to_wei('0.01', 'gwei')

            tx = self.executor_contract.functions.startArbitrage(
                val_in_wei, pools_ck, dir_list, tokens_ck
            ).build_transaction({
                'from': self.account.address,
                'nonce': self._current_nonce,
                'gas': 1200000,
                'maxFeePerGas': max_fee + priority_fee,
                'maxPriorityFeePerGas': priority_fee,
                'chainId': self.w3.eth.chain_id
            })

            # 4. Assinatura e Envio
            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)

            self._current_nonce += 1
            print(f"🚀 Enviado! Hash: {self.w3.to_hex(tx_hash)}")

            return self.w3.to_hex(tx_hash)

        except Exception as e:
            # Rotação de RPC em caso de erro de rede
            if any(x in str(e) for x in ["401", "429", "403", "500", "503", "timeout"]):
                print("🔄 Erro de RPC detectado. Rotacionando...")
                self.web3_manager.rotate_rpc()
                return self.send_transaction(pools_list, dir_list, tokens_list, amount_usd)

            print(f"❌ Erro crítico no envio: {e}")
            return None

    def get_usdc_balance(self) -> (float | None):
        try:
            usdc_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
                         "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]
            usdc_contract = self.w3.eth.contract(address=self.w3.to_checksum_address(self.usdc_address), abi=usdc_abi)
            return usdc_contract.functions.balanceOf(self.executor_address).call()
        except Exception as e:
            if any(x in str(e) for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                self.web3_manager.rotate_rpc()
                return self.get_usdc_balance()
            print(f"❌ Erro crítico no envio: {e}")
            return None

    def get_token_balance(self, token_address) -> (float | None):
        """
        Consulta o saldo de qualquer token ERC-20.
        :param token_address: Endereço do contrato do token (WETH, ARB, etc)
        :param owner_address: Opcional - Endereço a ser consultado (se None, usa o self.executor_address)
        """
        if not self.executor_address:
            raise ValueError("❌ executor_address não encontrada no ficheiro .env")
        owner_address = self.executor_address

        try:
            # ABI Mínimo para poupar memória e processamento
            erc20_abi = [{
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "balance", "type": "uint256"}],
                "type": "function"
            }]

            # Criar o contrato dinamicamente
            contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(token_address),
                abi=erc20_abi
            )

            # Chamar a função
            return contract.functions.balanceOf(
                self.w3.to_checksum_address(owner_address)
            ).call()

        except Exception as e:
            # Lógica de rotação de RPC que já tinhas
            if any(x in str(e).lower() for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                print("🔄 RPC instável, a rodar...")
                self.web3_manager.rotate_rpc()
                return self.get_token_balance(token_address)

            print(f"❌ Erro ao consultar balanço do token {token_address}: {e}")
            return 0  # Retornar 0 em vez de None facilita cálculos matemáticos depois

    def get_gas_cost_usd(self, eth_price: float) -> float:
        # 1. Pega o preço do gás em Wei (unidade mínima do ETH)
        gas_price_wei = self.w3.eth.gas_price

        # 2. Unidades de gás que um swap gasta na Arbitrum (~150k)
        gas_units = 150000

        # 3. Conversão para USD: (Preço em Wei * Unidades / 10^18) * Preço do ETH
        cost_eth = (gas_price_wei * gas_units) / 10 ** 18
        return cost_eth * eth_price
