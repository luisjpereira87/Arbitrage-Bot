import json
import math
import os

from dotenv import load_dotenv
from web3 import Web3

from scanner import ArbitrageScanner

# Configuração de Conexão
RPC_URL = "https://arb-mainnet.g.alchemy.com/v2/_9L_3ItqtYDAibU5GqR5W"  # Ou o teu provider (Alchemy/Infura)
w3 = Web3(Web3.HTTPProvider(RPC_URL))

# ABIs Mínimas necessárias
POOL_ABI = [
    {"inputs": [], "name": "slot0", "outputs": [{"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
                                                {"internalType": "int24", "name": "tick", "type": "int24"},
                                                {"internalType": "uint16", "name": "observationIndex",
                                                 "type": "uint16"},
                                                {"internalType": "uint16", "name": "observationCardinality",
                                                 "type": "uint16"},
                                                {"internalType": "uint16", "name": "observationCardinalityNext",
                                                 "type": "uint16"},
                                                {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
                                                {"internalType": "bool", "name": "unlocked", "type": "bool"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token0", "outputs": [{"internalType": "address", "name": "", "type": "address"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"internalType": "address", "name": "", "type": "address"}],
     "stateMutability": "view", "type": "function"}
]

ERC20_ABI = [
    {"inputs": [], "name": "decimals", "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"internalType": "string", "name": "", "type": "string"}],
     "stateMutability": "view", "type": "function"}
]


class QuoteDebuggerLive:
    def get_token_info(self, address):
        contract = w3.eth.contract(address=w3.to_checksum_address(address), abi=ERC20_ABI)
        return {
            "symbol": contract.functions.symbol().call(),
            "decimals": contract.functions.decimals().call()
        }

    def debug_pool_live(self, pool_address, token_in_address):
        print(f"\n--- 🌐 CONSULTA LIVE BLOCKCHAIN: {pool_address} ---")

        pool = w3.eth.contract(address=w3.to_checksum_address(pool_address), abi=POOL_ABI)

        # 1. Dados da Pool
        t0_addr = pool.functions.token0().call()
        t1_addr = pool.functions.token1().call()
        slot0 = pool.functions.slot0().call()
        sqrtPriceX96 = slot0[0]

        # 2. Dados dos Tokens
        info0 = self.get_token_info(t0_addr)
        info1 = self.get_token_info(t1_addr)

        d0, s0 = info0['decimals'], info0['symbol']
        d1, s1 = info1['decimals'], info1['symbol']

        print(f"Token 0: {s0} ({d0} decimais) - {t0_addr}")
        print(f"Token 1: {s1} ({d1} decimais) - {t1_addr}")
        print(f"SqrtPriceX96: {sqrtPriceX96}")

        # 3. Cálculo de Preço (A Matemática Final)
        price_raw = (sqrtPriceX96 / (2 ** 96)) ** 2
        price_adjusted = price_raw * (10 ** d0 / 10 ** d1)

        print(f"\n--- 📈 ANÁLISE DE MERCADO ---")
        print(f"Preço de 1 {s0} em {s1}: {price_adjusted:.6f}")
        print(f"Preço de 1 {s1} em {s0}: {1 / price_adjusted:.10f}")

        # 4. Simulação de Swap
        token_in_address = w3.to_checksum_address(token_in_address)
        if token_in_address == w3.to_checksum_address(t0_addr):
            print(f"Simulando: 1 {s0} -> {s1}")
            resultado = price_adjusted
        else:
            print(f"Simulando: 1 {s1} -> {s0}")
            resultado = 1 / price_adjusted

        print(f"✅ RESULTADO: Recebes {resultado:.10f}")
        return resultado


load_dotenv()

# Agora podes capturá-las assim:
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")

def load_config():
    with open("../config.json", "r") as f:
        return json.load(f)

# --- EXECUÇÃO ---
if __name__ == '__main__':
    config = load_config()
    scanner = ArbitrageScanner(RPC_URL, PRIVATE_KEY, config)

    # --- TESTE DE FOGO ---
    # Endereços validados para Arbitrum
    pool_teste = "0xC6F780497A95e246EB9449f5e4770916DCd6396A"
    usdc_addr = "0x912CE59144191C1204E64559FE8253a0e49E6548"
    weth_addr = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"

    print("\n🧪 INICIANDO TESTE DE COTAÇÃO DENTRO DO BOT...")

    # Configuração do teste
    quantidade_entrada = 30.0  # 30 USDC
    token_a = "ARB"
    token_b = "WETH"

    # Chama o get_quote que já tem a correção interna
    resultado = scanner.get_quote(pool_teste, usdc_addr, weth_addr)

    if resultado:
        preco, direcao, fee = resultado

        # Como o preco já vem corrigido pelo get_quote, basta multiplicar
        quantidade_saida = quantidade_entrada * preco

        print(f"--- 📊 RESULTADO DA SIMULAÇÃO ---")
        print(f"Entrada: {quantidade_entrada} {token_a}")
        print(f"Saída:   {quantidade_saida:.10f} {token_b}")
        print(f"Taxa da Pool: {fee / 10000}%")

        # Prova Real: Se 1 USDC vale X WETH, então 1/X é o preço do ETH em dólares
        if preco > 0:
            preco_eth = 1 / preco
            print(f"💰 Preço do ETH detetado: ${preco_eth:.2f}")

            if 1500 < preco_eth < 5000:
                print("✅ SUCESSO: O bot está a ler preços reais de mercado!")
            else:
                print("⚠️ ATENÇÃO: O preço parece irreal. Verifica os decimais no config.")
    else:
        print("❌ Erro ao obter quote. Verifica a conexão com o RPC ou o endereço da Pool.")