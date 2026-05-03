from dataclasses import dataclass


@dataclass
class DexOpportunityTriangular:
    strategy: str
    profit: float
    route_name: str
    dex_path: str
    route_id: str
    amount_in: float
    pools: list[str]
    zero_for_one: list[bool]
    tokens: list[str]
