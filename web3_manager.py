import os

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

# Agora podes capturá-las assim:
RPC_ALCHEMY_ARBITRUM_URL = os.getenv("RPC_ALCHEMY_ARBITRUM_URL")
RPC_ANKR_ARBITRUM_URL = os.getenv("RPC_ANKR_ARBITRUM_URL")
RPC_INFURA_ARBITRUM_URL = os.getenv("RPC_INFURA_ARBITRUM_URL")

class Web3Manager:
    def __init__(self):
        self.rpcs = [
            RPC_ALCHEMY_ARBITRUM_URL,
            RPC_ANKR_ARBITRUM_URL,
            RPC_INFURA_ARBITRUM_URL
        ]
        self.current_index = 0
        self.w3 = Web3(Web3.HTTPProvider(self.rpcs[self.current_index]))

    def rotate_rpc(self):
        self.current_index = (self.current_index + 1) % len(self.rpcs)
        print(f"🔄 Mudando para RPC: {self.rpcs[self.current_index]}")
        self.w3 = Web3(Web3.HTTPProvider(self.rpcs[self.current_index]))
        return self.w3