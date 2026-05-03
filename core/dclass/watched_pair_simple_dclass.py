from dataclasses import dataclass


@dataclass
class WatchedPairSimple:
    addr_a: str
    addr_b: str
    pools_map: dict[str, str]
