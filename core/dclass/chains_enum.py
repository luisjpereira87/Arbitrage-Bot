from enum import Enum


class Chains(Enum):
    ARBITRUM = "arbitrum"
    SOLANA = "solana"

    @staticmethod
    def from_str(value: (str | None)):
        if not value:
            raise ValueError("⚠️ Valor nulo ou vazio recebido em Signal.from_str")

        value = value.lower()
        mapping = {
            "arbitrum": Chains.ARBITRUM,
            "solana": Chains.SOLANA
        }

        if value in mapping:
            return mapping[value]

        raise NotImplementedError(f"⚠️ Valor desconhecido em Signal.from_str: {value}")
