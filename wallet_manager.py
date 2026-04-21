import json
import os
import time

from dotenv import load_dotenv
from eth_abi import encode
from web3 import Web3

load_dotenv()

import os
import json


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
        self.executor_address = self.w3.to_checksum_address(os.getenv("CONTRACT_ADDRESS"))
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
        """Executa a arbitragem atómica usando o RPC do momento"""
        try:
            val_in_wei = int(amount_in_usd * 10 ** 6)

            # Formatação de endereços
            pools_ck = [self.w3.to_checksum_address(p) for p in lista_pools]
            tokens_ck = [self.w3.to_checksum_address(t) for t in lista_tokens]

            # 1. Simulação (Dry Run) - Se o RPC falhar aqui, ele troca
            try:
                self.executor_contract.functions.startArbitrage(
                    pools_ck, lista_direcoes, tokens_ck, val_in_wei
                ).call({'from': self.account.address})
            except Exception as sim_err:
                if "429" in str(sim_err):
                    self.web3_manager.rotate_rpc()
                    return self.executar_arbitragem(lista_pools, lista_direcoes, lista_tokens, amount_in_usd)
                print(f"⚠️ Simulação falhou: {sim_err}")
                return None

            # 2. Envio Real
            latest_block = self.w3.eth.get_block('latest')
            base_fee = latest_block.get('baseFeePerGas', self.w3.to_wei('0.1', 'gwei'))

            tx = self.executor_contract.functions.startArbitrage(
                pools_ck, lista_direcoes, tokens_ck, val_in_wei
            ).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gas': 800000,
                'maxFeePerGas': int(base_fee * 1.35) + self.w3.to_wei('0.01', 'gwei'),
                'maxPriorityFeePerGas': self.w3.to_wei('0.01', 'gwei'),
                'chainId': 42161
            })

            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            return self.w3.to_hex(tx_hash)

        except Exception as e:
            if "429" in str(e):
                self.web3_manager.rotate_rpc()
                return self.executar_arbitragem(lista_pools, lista_direcoes, lista_tokens, amount_in_usd)
            print(f"❌ Erro crítico: {e}")
            return None