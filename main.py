# This is a sample Python script.
import json
import os

from dotenv import load_dotenv

from scanner import ArbitrageScanner

load_dotenv()

# Agora podes capturá-las assim:
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")
CONTRACT_ADDRESS = os.getenv("CONTRACT_ADDRESS")

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)
"""
def load_abi():
    with open("abi.json", "r") as f:
        return json.load(f)
"""

if __name__ == '__main__':
    config = load_config()
    #abi = load_abi()

    # --- EXECUÇÃO ---
    scanner = ArbitrageScanner(RPC_URL, PRIVATE_KEY, config)
    scanner.run_triangular()

