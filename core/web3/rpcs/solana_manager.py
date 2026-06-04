import os

from dotenv import load_dotenv
from solana.rpc.async_api import AsyncClient

load_dotenv()

# Agora podes capturá-las assim:
RPC_ALCHEMY_SOLANA_URL = os.getenv("RPC_ALCHEMY_SOLANA_URL")
RPC_ANKR_SOLANA_URL = os.getenv("RPC_ANKR_SOLANA_URL")
RPC_INFURA_SOLANA_URL = os.getenv("RPC_INFURA_SOLANA_URL")
RPC_MAINNET_BETA_SOLANA_URL = os.getenv("RPC_MAINNET_BETA_SOLANA_URL")
RPC_HELIUS_SOLANA_URL = os.getenv("RPC_HELIUS_SOLANA_URL")


class SolanaManager:
    def __init__(self):
        self.rpcs = [
            RPC_HELIUS_SOLANA_URL,
            RPC_ALCHEMY_SOLANA_URL,
            RPC_INFURA_SOLANA_URL,
            RPC_MAINNET_BETA_SOLANA_URL,
        ]
        self.current_index = 0
        self.solana = AsyncClient(self.rpcs[self.current_index])
        self.allow_rotation = True

    def rotate_rpc(self):
        if not self.allow_rotation:
            print("🔒 Rotação bloqueada. Mantendo RPC atual.")
            return self.solana  # Retorna o atual sem mudar

        self.current_index = (self.current_index + 1) % len(self.rpcs)
        print(f"🔄 Mudando para RPC: {self.rpcs[self.current_index]}")
        self.solana = AsyncClient(self.rpcs[self.current_index])
        return self.solana
