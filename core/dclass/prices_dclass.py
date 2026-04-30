from dataclasses import dataclass


@dataclass
class Prices:
    bid: float
    ask: float
    last: float
