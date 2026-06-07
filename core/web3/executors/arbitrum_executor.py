import logging

from core.config.properties_base import PropertiesBase
from core.dclass.chains_enum import Chains
from core.web3.executors.executor_base import ExecutorBase


class ArbitrumExecutor(ExecutorBase):
    def __init__(self, web3_manager, properties: PropertiesBase):
        self.web3_manager = web3_manager

        # Carregar chaves (Garante que o .env está carregado antes de instanciar esta classe)
        self.private_key = properties.PRIVATE_KEY

        self.config = properties.CONFIG

        if not self.private_key:
            raise ValueError("❌ PRIVATE_KEY não encontrada no ficheiro .env")

        # 2. Configurações de Endereços
        # Nota: Usamos self.w3 (a propriedade dinâmica que criaremos abaixo)
        self.account = self.w3.eth.account.from_key(self.private_key)

        self.executor_address = self.w3.to_checksum_address(properties.CONTRACT_ADDRESS)

        self._current_nonce = None

        # self.executor_address = self.w3.to_checksum_address(os.getenv("CONTRACT_ADDRESS"))
        self.usdc_address = self.w3.to_checksum_address(properties.USDC_ADDRESS_ARBITRUM)

        self.executor_abi = properties.CONTRACT_ABI

        # ABI mínima para o Approve do USDC
        self.erc20_abi = properties.ERC20_ABI

        # --- NOVA CONFIGURAÇÃO UNISWAP V3 QUOTER ---
        # Endereço do QuoterV2 no Arbitrum
        self.quoter_address = self.w3.to_checksum_address("0x61fFE014bA17989E743c5F6cB21bF9697530B21e")
        # ABI mínima necessária para o quoteExactInputSingle
        self.quoter_abi = [
            {
                "inputs": [
                    {
                        "components": [
                            {"internalType": "address", "name": "tokenIn", "type": "address"},
                            {"internalType": "address", "name": "tokenOut", "type": "address"},
                            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                            {"internalType": "uint24", "name": "fee", "type": "uint24"},
                            {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
                        ],
                        "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                        "name": "params",
                        "type": "tuple"
                    }
                ],
                "name": "quoteExactInputSingle",
                "outputs": [
                    {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
                    {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
                    {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"}
                ],
                "stateMutability": "nonpayable",
                "type": "function"
            }
        ]

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

    @property
    def quoter_contract(self):
        return self.w3.eth.contract(address=self.quoter_address, abi=self.quoter_abi)

    def check_and_approve_executor(self, amount_usd: float = 100.0, chain=Chains.ARBITRUM):
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

    async def send_transaction(self, pools_list: list[str], dir_list: list[bool], tokens_list: list[str],
                               amount_usd: float, chain=Chains.ARBITRUM, quote_data: dict | None = None):
        # 1. TRATAMENTO DO VALOR
        # Se amount_usd já for o valor em WEI (ex: vindo da sequência de saída), não multiplicamos
        """
        if amount_usd > 1000000:
            val_in_wei = int(amount_usd)
        else:
            val_in_wei = int(amount_usd * 10 ** 6)
        """
        val_in_wei = int(amount_usd)
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

        # Ajuste automático de "Dust" (Poeira)
        if contract_balance < val_in_wei:
            diff = val_in_wei - contract_balance
            if diff < 100000:  # Se faltar uma quantia ínfima, vende tudo o que tem
                print(f"⚠️ Diferença mínima ({diff} wei). Ajustando para saldo total.")
                val_in_wei = contract_balance
            else:
                print(f"❌ ERRO: Saldo insuficiente real! ({contract_balance} < {val_in_wei})")
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
                return await self.send_transaction(pools_list, dir_list, tokens_list, amount_usd)

            print(f"❌ Erro crítico no envio: {e}")
            return None

    async def get_usdc_balance(self, chain=Chains.ARBITRUM) -> int:
        try:
            usdc_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
                         "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]
            usdc_contract = self.w3.eth.contract(address=self.w3.to_checksum_address(self.usdc_address), abi=usdc_abi)
            return usdc_contract.functions.balanceOf(self.executor_address).call()
        except Exception as e:
            if any(x in str(e) for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                self.web3_manager.rotate_rpc()
                return await self.get_usdc_balance()
            print(f"❌ Erro crítico no envio: {e}")
            return 0

    async def get_token_balance(self, token_address, chain=Chains.ARBITRUM) -> int:
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
            balance = contract.functions.balanceOf(
                self.w3.to_checksum_address(owner_address)
            ).call()

            return int(balance)

        except Exception as e:
            # Lógica de rotação de RPC que já tinhas
            if any(x in str(e).lower() for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                print("🔄 RPC instável, a rodar...")
                self.web3_manager.rotate_rpc()
                return await self.get_token_balance(token_address)

            print(f"❌ Erro ao consultar balanço do token {token_address}: {e}")
            return 0  # Retornar 0 em vez de None facilita cálculos matemáticos depois

    async def get_gas_cost_usd(self, eth_price: (float | None), chain: Chains) -> float:
        if eth_price is None:
            eth_price = self._get_eth_price_chainlink()

        # 1. Pega o preço do gás em Wei (unidade mínima do ETH)
        gas_price_wei = self.w3.eth.gas_price

        # 2. Unidades de gás que um swap gasta na Arbitrum (~150k)
        gas_units = 150000

        # 3. Conversão para USD: (Preço em Wei * Unidades / 10^18) * Preço do ETH
        cost_eth = (gas_price_wei * gas_units) / 10 ** 18
        return cost_eth * eth_price

    def _get_eth_price_chainlink(self):
        try:
            # ABI mínima para o Chainlink
            abi = [{"inputs": [], "name": "latestRoundData",
                    "outputs": [{"internalType": "uint80", "name": "roundId", "type": "uint256"},
                                {"internalType": "int256", "name": "answer", "type": "int256"},
                                {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
                                {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
                                {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"}],
                    "stateMutability": "view", "type": "function"}]

            addr = "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612"
            contract = self.w3.eth.contract(address=self.w3.to_checksum_address(addr), abi=abi)

            # O valor vem com 8 casas decimais
            price = contract.functions.latestRoundData().call()[1]
            return float(price) / 10 ** 8

        except Exception as e:
            # Lógica de rotação de RPC que já tinhas
            if any(x in str(e).lower() for x in ["401", "429", "403", "500", "503", "timeout", "unauthorized"]):
                print("🔄 RPC instável, a rodar...")
                self.web3_manager.rotate_rpc()
                return self._get_eth_price_chainlink()

            print(f"❌ Erro ao consultar preço de eth : {e}")
            return 0  # Retornar 0 em vez de None facilita cálculos matemáticos depois

    async def is_swap_viable(self, token_in: str, token_out: str, amount_in_usd: float, expected_out_units: float,
                             fee: int, tolerance: float, chain: Chains, quote_data: dict | None, is_exit: bool) -> \
    tuple[bool, float]:
        """
        Simula o swap na Uniswap V3 de forma dinâmica usando os decimais do Config.
        """
        try:
            # 1. Procurar metadados dos tokens no Config pelo endereço
            t_in_info = self.config.tokens_by_address.get(token_in.lower())
            t_out_info = self.config.tokens_by_address.get(token_out.lower())

            # Fallback para 18 decimais caso o token não esteja no config (padrão ERC20)
            dec_in = t_in_info.decimals if t_in_info else 18
            dec_out = t_out_info.decimals if t_out_info else 18

            # 2. Converter a entrada para a unidade base (Wei/Raw) correta
            # Se amount_in_usd for 100 e dec_in for 18, teremos 100 * 10^18
            amount_in_raw = int(amount_in_usd * 10 ** dec_in)

            params = (
                self.w3.to_checksum_address(token_in),
                self.w3.to_checksum_address(token_out),
                amount_in_raw,
                fee,
                0
            )

            # 3. Chamar o Quoter (Simulação On-Chain)
            # Retorna: (amountOut, sqrtPriceX96After, initializedTicksCrossed, gasEstimate)
            quote_data = self.quoter_contract.functions.quoteExactInputSingle(params).call()

            # 4. Converter a saída de Wei para unidades humanas
            amount_out_real = quote_data[0] / 10 ** dec_out

            # 5. Validação de Slippage
            min_acceptable = expected_out_units * (1 - tolerance)

            if amount_out_real < min_acceptable:
                # Usamos 4 casas decimais para tokens caros como WBTC ou ETH
                logging.warning(
                    f"⚠️ Swap REJEITADO: Real {amount_out_real:.4f} < Min {min_acceptable:.4f} (Fee: {fee})")
                return False, amount_out_real

            logging.info(
                f"✅ Swap validado: Receberás aprox. {amount_out_real:.4f} {t_out_info.symbol if t_out_info else ''}")
            return True, amount_out_real

        except Exception as e:
            # Lógica de rotação de RPC em caso de erro de rede
            if any(x in str(e).lower() for x in ["401", "429", "500", "timeout"]):
                logging.warning("🔄 RPC Error detectado no Quoter. Rotacionando...")
                self.web3_manager.rotate_rpc()
                return self.is_swap_viable(token_in, token_out, amount_in_usd, expected_out_units, fee, tolerance,
                                           chain, quote_data)

            logging.error(f"❌ Erro na simulação do Quoter: {e}")
            return False, 0
