# This is a sample Python script.
import json
import os

from dotenv import load_dotenv

from scanner import ArbitrageScanner
from wallet_manager import WalletManager
from web3_manager import Web3Manager

load_dotenv()

# Agora podes capturá-las assim:
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")

def load_config():
    with open("./config.json", "r") as f:
        return json.load(f)
"""
def load_abi():
    with open("abi.json", "r") as f:
        return json.load(f)
"""

def main():
    config = load_config()
    # abi = load_abi()

    # --- EXECUÇÃO ---
    scanner = ArbitrageScanner(config, 200)
    # scanner.run_triangular()

    scanner.run_parallel()
