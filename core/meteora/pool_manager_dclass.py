from dataclasses import dataclass
from typing import Dict


@dataclass
class TokenInfo:
    address: str
    symbol: str
    decimals: int


@dataclass
class PoolConfig:
    name: str
    address: str
    binStep: int
    feePct: float
    tokenX: TokenInfo
    tokenY: TokenInfo


RAW_DATA = {
    "SOL/USDC": {
        "address": "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6",
        "binStep": 4,
        "feePct": 0.0020,
        "tokenX": {"symbol": "SOL", "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "decimals": 9},
        "tokenY": {"symbol": "USDC", "address": "So11111111111111111111111111111111111111112", "decimals": 6}
    }
}


class PoolManager:
    def __init__(self):
        # Armazenamos como um dicionário para busca rápida pela chave
        self._pools: Dict[str, PoolConfig] = {}

        for name, data in RAW_DATA.items():
            self._pools[name] = PoolConfig(
                name=name,
                address=data["address"],
                binStep=data["binStep"],
                feePct=data["feePct"],
                tokenX=TokenInfo(**data["tokenX"]),
                tokenY=TokenInfo(**data["tokenY"])
            )

    def __getitem__(self, key: str) -> PoolConfig:
        """Permite aceder como manager['SOL/USDC']"""
        return self._pools[key]

    def get(self, key: str) -> PoolConfig:
        """Método explícito para obter a pool"""
        return self._pools[key]


"""
# --- Exemplo de Uso ---
RAW_DATA = {
    "SOL/USDC": {
        "address": "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6",
        "binStep": 4,
        "feePct": 0.0020,
        "tokenX": {"symbol": "SOL", "decimals": 9},
        "tokenY": {"symbol": "USDC", "decimals": 6}
    }
}

manager = PoolManager(RAW_DATA)

# Acesso direto pela chave:
pool = manager["SOL/USDC"]
print(f"Token X da SOL/USDC: {pool.tokenX.symbol}")  # Acesso aos filhos imediato
"""
