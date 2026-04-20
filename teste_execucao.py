import os
import time
from web3 import Web3
from wallet_manager import WalletManager
from dotenv import load_dotenv

load_dotenv()

w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
wallet = WalletManager(w3)

# --- DADOS DE TESTE (Exemplo de uma rota USDC -> WETH -> ARB -> USDC) ---
# Substitui pelos endereços que o teu scanner costuma mostrar
pools_teste = [
    "0x89A4026E9aDE251C67b7fb38054931a39936D9C5", # Pool 1
    "0x011cc642ae74315001eeb541C1d2D225e92Ece3d",
    "0x11d53EC50bc8F54B9357fbFe2A7dE034FC00f8b3"
]
# Direções que o teu get_quote calcularia
direcoes_teste = [True, True, True]

print("🚀 Enviando trade de teste de $1.00...")
print("Nota: Esperamos que isto dê REVERT no Arbiscan (Status: Fail) porque não haverá lucro.")

tx_hash = wallet.executar_arbitragem(pools_teste, direcoes_teste, 1.0)

if tx_hash:
    print(f"🔥 Transação enviada: https://arbiscan.io/tx/{tx_hash}")
    print("Aguardando confirmação...")
    # Espera para ver se a rede aceita ou rejeita
    time.sleep(10)