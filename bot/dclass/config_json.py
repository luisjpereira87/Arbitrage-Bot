import json
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class Config:
    file_path: str
    # Marcamos como init=False porque não os passamos ao criar a instância
    tokens: Dict[str, 'TokenInfo'] = field(init=False)
    fees: List[int] = field(init=False)
    triangles: List[Tuple[str, str, str]] = field(init=False)
    simple_pairs: List[Tuple[str, str]] = field(init=False)

    def __post_init__(self):
        # O post_init corre logo após o objeto ser criado com o file_path
        self.load_from_json()

    def load_from_json(self):
        with open(self.file_path, 'r') as f:
            data = json.load(f)

        # 1. Mapear Tokens
        self.tokens = {
            sym: TokenInfo(symbol=sym, address=info["addr"], decimals=info["dec"])
            for sym, info in data["tokens"].items()
        }

        # 2. Mapear Fees
        self.fees = data["fees"]

        # 3. Mapear Triângulos (Garante tipagem Tuple[str, str, str])
        self.triangles = [
            (str(t[0]), str(t[1]), str(t[2]))
            for t in data.get("triangles", [])
        ]

        # 4. Mapear Simple Pairs (Garante tipagem Tuple[str, str])
        self.simple_pairs = [
            (str(p[0]), str(p[1]))
            for p in data.get("simple_pairs", [])
        ]

        print(f"⚙️ Configuração carregada via __post_init__ ({len(self.tokens)} tokens)")


@dataclass(frozen=True)
class TokenInfo:
    symbol: str
    address: str
    decimals: int