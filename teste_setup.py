from wallet_manager import WalletManager
from web3 import Web3
import os
from dotenv import load_dotenv

load_dotenv()

w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
wallet = WalletManager(w3)

# Vamos autorizar o contrato a usar até 100 USDC
print("Iniciando Approve...")
wallet.check_and_approve_executor(100.0)
print("Verifica o Arbiscan. Se a transação foi confirmada, o contrato já pode operar!")