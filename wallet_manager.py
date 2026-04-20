import json
import os
import time

from dotenv import load_dotenv
from eth_abi import encode
from web3 import Web3

load_dotenv()

class WalletManager:
    def __init__(self, web3_instance):
        self.w3 = web3_instance

        # 1. Carregar chaves e endereços do .env
        self.private_key = os.getenv("PRIVATE_KEY")
        self.account = self.w3.eth.account.from_key(self.private_key)
        self.executor_address = self.w3.to_checksum_address(os.getenv("CONTRACT_ADDRESS"))
        self.usdc_address = self.w3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

        # 2. Carregar o ABI do teu contrato (Recomendo ter um ficheiro abi.json)
        # Se preferires manter no código, cola a lista do Blockscout aqui:

        try:
            with open("abi.json", "r") as f:
                self.executor_abi = json.load(f)
        except FileNotFoundError:
            print("❌ Erro: O ficheiro abi.json não foi encontrado na pasta!")
            raise

        # 3. Inicializar o contrato do Executor
        self.executor_contract = self.w3.eth.contract(
            address=self.executor_address,
            abi=self.executor_abi
        )

        # ABI mínima para o Approve do USDC
        self.erc20_abi = [
            {"constant": False,
             "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
             "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
            {"constant": True,
             "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
             "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}
        ]

        print(f"✅ WalletManager Conectado!")
        print(f"💳 Carteira: {self.account.address}")
        print(f"🤖 Contrato Executor: {self.executor_address}")

    def check_and_approve_executor(self, amount_usd):
        """Dá permissão ao TEU contrato para usar o teu USDC"""
        token_contract = self.w3.eth.contract(address=self.usdc_address, abi=self.erc20_abi)
        amount_wei = int(amount_usd * 10 ** 6)

        allowance = token_contract.functions.allowance(self.account.address, self.executor_address).call()

        if allowance < amount_wei:
            print(f"🔓 Autorizando o contrato executor a usar USDC...")

            # --- NOVO CÁLCULO DE TAXAS ---
            latest_block = self.w3.eth.get_block('latest')
            base_fee = latest_block['baseFeePerGas']

            # Priority Fee (Gorjeta ao minerador) - 0.1 gwei é suficiente na Arbitrum
            priority_fee = self.w3.to_wei('0.1', 'gwei')

            # Max Fee = Base Fee + Margem de 20% + Priority Fee
            max_fee = int(base_fee * 1.2) + priority_fee
            # -----------------------------

            max_amount = 2 ** 256 - 1
            tx = token_contract.functions.approve(self.executor_address, max_amount).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gas': 100000,  # Aumentado para segurança
                'maxFeePerGas': max_fee,
                'maxPriorityFeePerGas': priority_fee,
                'chainId': 42161
            })

            # Nota: Garante que é self.private_key ou self.account.key conforme o teu __init__
            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)

            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

            print(f"✅ Approve enviado: https://arbiscan.io/tx/{self.w3.to_hex(tx_hash)}")

            # Esperar confirmação real (opcional mas recomendado)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            if receipt.status == 1:
                print("💎 Approve confirmado com sucesso!")
                return True
            else:
                print("❌ Falha na confirmação do Approve.")
                return False

        print("✅ Contrato já tem permissão suficiente.")
        return True

    def executar_arbitragem(self, lista_pools, lista_direcoes, amount_in_usd):
        try:

            val_in_wei = int(amount_in_usd * 10 ** 6)
            # --- 1. SIMULAÇÃO (DRY RUN) ---
            # Isto testa se o contrato vai dar "Revert" (prejuízo ou erro) antes de gastar gás.
            try:
                self.executor_contract.functions.startArbitrage(
                    lista_pools,
                    lista_direcoes,
                    val_in_wei
                ).call({'from': self.account.address})
                print("✅ Simulação passou: Lucro real detectado pelo contrato!")
            except Exception as sim_error:
                # Se a simulação falhar, nem perdemos tempo com o resto
                print(f"⚠️ Abortado: Simulação falhou (Prejuízo ou Erro): {sim_error}")
                return None


            nonce = self.w3.eth.get_transaction_count(self.account.address)

            # --- CÁLCULO DE TAXAS DINÂMICO E ROBUSTO ---
            latest_block = self.w3.eth.get_block('latest')
            base_fee = latest_block['baseFeePerGas']

            # Na Arbitrum, aumentar a base_fee em 35% garante que a TX entra no próximo bloco
            # sem quase nenhum custo extra real (são frações de cêntimo).
            max_fee = int(base_fee * 1.35)
            priority_fee = self.w3.to_wei('0.1', 'gwei')

            tx = self.executor_contract.functions.startArbitrage(
                lista_pools,
                lista_direcoes,
                val_in_wei
            ).build_transaction({
                'from': self.account.address,
                'nonce': nonce,
                'gas': 1000000,
                'maxFeePerGas': max_fee + priority_fee,
                'maxPriorityFeePerGas': priority_fee,
                'chainId': 42161
            })

            signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)

            return self.w3.to_hex(tx_hash)

        except Exception as e:
            print(f"❌ Erro na execução do contrato: {e}")
            return None