from dataclasses import dataclass


@dataclass
class DexOpportunitySimple:
    strategy: str
    profit: float
    amount_in: float
    pools: list[str]
    zero_for_one: list[bool]
    tokens: list[str]
