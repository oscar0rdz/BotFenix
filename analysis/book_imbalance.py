from typing import List, Tuple


def order_book_imbalance(
    bids: List[Tuple[float, float]],
    asks: List[Tuple[float, float]],
    depth_levels: int = 5,
) -> float:
    """Calculates the order book imbalance.

    (BidVol - AskVol) / (BidVol + AskVol)
    """
    if not bids or not asks:
        return 0.0

    top_bids = bids[:depth_levels]
    top_asks = asks[:depth_levels]

    bid_vol = sum(q for _, q in top_bids)
    ask_vol = sum(q for _, q in top_asks)

    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0

    return (bid_vol - ask_vol) / total
