import json
import math
import os
import time

from dotenv import load_dotenv
from eth_abi import encode
from web3 import Web3
load_dotenv()

import os
import json

w3 = Web3(Web3.HTTPProvider(
    'http://127.0.0.1:8545',
    request_kwargs={'timeout': 120}  # Isto dá 2 minutos de "paciência" ao Python
))


class WalletManager:
    def __init__(self, web3_manager):
        # 1. Recebemos o Manager ÚNICO
        self.web3_manager = web3_manager

        # Carregar chaves (Garante que o .env está carregado antes de instanciar esta classe)
        self.private_key = os.getenv("PRIVATE_KEY")
        if not self.private_key:
            raise ValueError("❌ PRIVATE_KEY não encontrada no ficheiro .env")

        # 2. Configurações de Endereços
        # Nota: Usamos self.w3 (a propriedade dinâmica que criaremos abaixo)
        self.account = self.w3.eth.account.from_key(self.private_key)

        if self._is_fork():
            from brownie import web3 as brownie_web3
            # Força o teu manager a usar o provider do Brownie
            self.web3_manager.w3.provider = brownie_web3.provider
            self.executor_address = self._bootstrap_fork_env()
            self.web3_manager.allow_rotation = False
        else:
            # Em produção, usa o endereço do .env
            self.executor_address = self.w3.to_checksum_address(os.getenv("CONTRACT_ADDRESS"))

        self._current_nonce = None

        #self.executor_address = self.w3.to_checksum_address(os.getenv("CONTRACT_ADDRESS"))
        self.usdc_address = self.w3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

        # 3. Carregar ABI do Contrato
        try:
            with open("abi.json", "r") as f:
                self.executor_abi = json.load(f)
        except FileNotFoundError:
            print("❌ Erro: O ficheiro abi.json não foi encontrado!")
            raise

        # ABI mínima para o Approve do USDC
        self.erc20_abi = [
            {"constant": False,
             "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
             "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
            {"constant": True,
             "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
             "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}
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

    def check_and_approve_executor(self, amount_usd):
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
            if "429" in str(e):
                self.web3_manager.rotate_rpc()
                return self.check_and_approve_executor(amount_usd)  # Tenta de novo com novo RPC
            print(f"❌ Erro no Approve: {e}")
            return False

    def executar_arbitragem(self, lista_pools, lista_direcoes, lista_tokens, amount_in_usd):
        # 1. TRATAMENTO DO VALOR (Garante que não multiplicamos o que já está multiplicado)
        # Se o valor for maior que 1 milhão, assumimos que já está em Wei
        if amount_in_usd > 1000000:
            val_in_wei = int(amount_in_usd)
        else:
            val_in_wei = int(amount_in_usd * 10 ** 6)

        # --- DEBUG ATUALIZADO ---
        usdc_address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
        usdc_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
                     "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]
        usdc_contract = self.w3.eth.contract(address=self.w3.to_checksum_address(usdc_address), abi=usdc_abi)

        saldo_no_contrato = usdc_contract.functions.balanceOf(self.executor_address).call()

        print(f"\n--- 🕵️ RELATÓRIO DE EXECUÇÃO ---")
        print(f"📍 Contrato: {self.executor_address}")
        print(f"💰 Saldo Real no Contrato: {saldo_no_contrato / 10 ** 6:.2f} USDC")
        print(f"📉 Pedido p/ Arbitragem: {val_in_wei / 10 ** 6:.2f} USDC")
        print(f"🔢 Valor em Wei: {val_in_wei}")

        if saldo_no_contrato < val_in_wei:
            print(f"❌ ERRO: Saldo insuficiente! Faltam {(val_in_wei - saldo_no_contrato) / 10 ** 6:.2f} USDC")
        print(f"---------------------------------\n")
        # --- FIM DEBUG ---

        """Executa a arbitragem atómica usando o RPC do momento"""
        try:
            # Formatação de endereços
            pools_ck = [self.w3.to_checksum_address(p) for p in lista_pools]
            tokens_ck = [self.w3.to_checksum_address(t) for t in lista_tokens]

            # 1. Simulação (Dry Run)
            try:
                self.executor_contract.functions.startArbitrage(
                    val_in_wei, pools_ck, lista_direcoes, tokens_ck
                ).call({'from': self.account.address})
                print("✅ Simulação passou com sucesso!")
            except Exception as sim_err:
                if "429" in str(sim_err):
                    self.web3_manager.rotate_rpc()
                    return self.executar_arbitragem(lista_pools, lista_direcoes, lista_tokens, amount_in_usd)
                print(f"⚠️ Simulação falhou: {sim_err}")
                return None

            # 2. Envio Real
            latest_block = self.w3.eth.get_block('latest')
            base_fee = latest_block.get('baseFeePerGas', self.w3.to_wei('0.1', 'gwei'))

            real_nonce = self.w3.eth.get_transaction_count(self.account.address)

            # Se o nosso contador interno for menor que o real, corrigimos
            if self._current_nonce is None or  self._current_nonce < real_nonce:
                self._current_nonce = real_nonce

            tx = self.executor_contract.functions.startArbitrage(
                val_in_wei, pools_ck, lista_direcoes, tokens_ck
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
            saldo_final = usdc_contract.functions.balanceOf(self.executor_address).call()

            print(f"💰 Saldo Real final no Contrato: {saldo_final / 10 ** 6:.2f} USDC")
            return self.w3.to_hex(tx_hash)

        except Exception as e:
            if "nonce" in str(e).lower():
                self._current_nonce = self.w3.eth.get_transaction_count(self.account.address)
                print(f"🔄 Erro de sincronização. Novo Nonce base: {self._current_nonce}")

            if "429" in str(e):
                self.web3_manager.rotate_rpc()
                return self.executar_arbitragem(lista_pools, lista_direcoes, lista_tokens, amount_in_usd)
            print(f"❌ Erro crítico no envio: {e}")
            return None

    def forcar_execucao_teste(self):

        # Dados da rota do teu log (LINK)
        pools = [
            self.w3.to_checksum_address("0xC473e2aEE3441BF9240Be85eb122aBB059A3B57c"),
            self.w3.to_checksum_address("0x92c63d0e701CAAe670C9415d91C474F686298f00"),
            self.w3.to_checksum_address("0xaEBDcA1Bc8d89177EbE2308d62af5e74885DcCc3")
        ]

        # Direções (True se tokenIn < tokenOut no endereço, depende da pool)
        # Podes extrair estas do teu log ou colocar as que o bot calculou
        direcoes = [True, True, False]

        tokens = [
            self.w3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831"),  # USDC
            self.w3.to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"),  # WETH
            self.w3.to_checksum_address("0xf97f4df75117a78c1A5a0DBb814Af92458539FB4")  # LINK
        ]

        print("🛠️ Iniciando transação de teste real...")
        # Usamos um valor pequeno, ex: 10 USDC para o teste

        tx_hash = self.executar_arbitragem(pools, direcoes, tokens, 0.05)

        if tx_hash:
            print(f"🚀 SUCESSO! Transação enviada: https://arbiscan.io/tx/{tx_hash}")
        else:
            print("❌ A transação falhou antes de ser enviada.")

    def _is_fork(self):
        try:
            # Tenta importar apenas se necessário
            from brownie import network
            # Verifica se o brownie está ativo e se a rede é um fork
            return network.is_connected() and "fork" in network.show_active()
        except (ImportError, Exception):
            # Se o brownie não existir ou não estiver inicializado,
            # estamos num script Python puro (Mainnet)
            return False

    def _bootstrap_fork_env(self):
        # Importa aqui dentro para manter o contexto do Brownie isolado
        from brownie import accounts, network, web3, Contract

        temp_file = "deployed_address_fork.txt"

        # 1. Limpeza de Cache (Garante que cada run é um deploy novo no Fork)
        if os.path.exists(temp_file):
            os.remove(temp_file)
            print("🧹 Cache de endereços do Fork limpa. Pronto para novo deploy.")

        # 2. Configuração de Contas (A parte CRÍTICA para o erro 'OWN')
        # Adicionamos a tua chave privada ao Brownie para ele assinar como Owner oficial
        bot_account = accounts.add(self.private_key)

        USDC_ADDR = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
        WETH_ADDR = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
        ROUTER_ADDR = "0xE592427A0AEce92De3Edee1F18E0157C05861564"

        # 3. MODO DEUS: Dar ETH à tua conta no Fork para pagar o Deploy e Gas
        web3.provider.make_request("evm_setAccountBalance", [bot_account.address, hex(20 * 10 ** 18)])

        # 4. CARREGAR O ARTIFACT (ABI/Bytecode)
        with open('./data/v7_final.json', 'r') as f:
            data = json.load(f)

        path = "contracts_backup/ArbitrageV7.sol:ArbitrageExecutorV7"
        abi = data["contracts"][path]["abi"]
        bytecode = data["contracts"][path]["bin"]

        # 5. DEPLOY MANUAL (Agora assinado pela bot_account)
        print(f"🚀 A preparar deploy manual do V7. Owner: {bot_account.address}")
        v7_factory = web3.eth.contract(abi=abi, bytecode=bytecode)

        # Construímos a transação sem enviar ainda
        construct_txn = v7_factory.constructor(USDC_ADDR).build_transaction({
            'from': bot_account.address,
            'nonce': web3.eth.get_transaction_count(bot_account.address),
            'gas': 5000000,  # Valor alto para o fork
            'gasPrice': web3.eth.gas_price  # No fork costuma ser 0 ou fixo
        })

        # ASSINATURA MANUAL: Usamos a private key para assinar fora do nó
        signed_txn = web3.eth.account.sign_transaction(construct_txn, private_key=self.private_key)

        # ENVIAR: Agora enviamos o bytecode já assinado
        print("📡 A enviar transação assinada...")
        tx_hash = web3.eth.send_raw_transaction(signed_txn.raw_transaction)
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        v7 = Contract.from_abi("ArbitrageExecutorV7", receipt.contractAddress, abi)
        print(f"✅ Contrato V7 vivo em: {v7.address}")

        # 6. COMPRAR USDC PARA TESTES (Usando a conta do bot)
        router_abi = [{"inputs": [{"components": [{"internalType": "address", "name": "tokenIn", "type": "address"},
                                                  {"internalType": "address", "name": "tokenOut", "type": "address"},
                                                  {"internalType": "uint24", "name": "fee", "type": "uint24"},
                                                  {"internalType": "address", "name": "recipient", "type": "address"},
                                                  {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                                                  {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                                                  {"internalType": "uint256", "name": "amountOutMinimum",
                                                   "type": "uint256"},
                                                  {"internalType": "uint160", "name": "sqrtPriceLimitX96",
                                                   "type": "uint160"}], "name": "params", "type": "tuple"}],
                       "name": "exactInputSingle",
                       "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
                       "stateMutability": "payable", "type": "function"}]
        router = Contract.from_abi("Router", ROUTER_ADDR, router_abi)

        print("🛒 A comprar USDC na Uniswap para o Contrato...")
        # Trocamos 10 ETH por USDC e enviamos para a conta do bot
        router.exactInputSingle([
            WETH_ADDR, USDC_ADDR, 500, bot_account.address, 9999999999, 10 * 10 ** 18, 0, 0
        ], {'from': bot_account, 'value': 10 * 10 ** 18})

        # 7. TRANSFERIR SALDO DA CARTEIRA PARA O CONTRATO
        usdc_abi = [
            {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
             "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
            {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
             "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]
        usdc = Contract.from_abi("USDC", USDC_ADDR, usdc_abi)

        # Passa todo o USDC comprado para o contrato V7
        saldo_bot = usdc.balanceOf(bot_account)
        usdc.transfer(v7.address, saldo_bot, {'from': bot_account})

        print("-" * 30)
        print(f"🏁 SETUP FINALIZADO NO FORK")
        print(f"Dono do Contrato: {v7.owner()}")
        print(f"Saldo no Contrato: {usdc.balanceOf(v7.address) / 1e6} USDC")
        print("-" * 30)

        # 8. Gravar endereço para as outras instâncias do Bot
        with open(temp_file, "w") as f:
            f.write(v7.address)

        return v7.address