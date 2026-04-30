from dataclasses import dataclass

from core.dclass.watched_pair_dclass import WatchedPair


@dataclass
class ProfitInfo:
    entry_spread: float
    entry_price_spot: float
    entry_price_perp: float
    amount: float
    total_fees_in: float
    funding_accumulated: float
    timestamp_opened: float
    pair_data: WatchedPair
