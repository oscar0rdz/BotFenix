import time
from collections import deque
from typing import Deque, Tuple, Optional, Iterable

from core.models import TradeEvent


class CVDCalculator:
    """Calculates the Cumulative Volume Delta (CVD) and maintains a recent history."""

    def __init__(self, maxlen: int = 3600) -> None:
        # Stores (ts, cvd)
        self._history: Deque[Tuple[float, float]] = deque(maxlen=maxlen)
        self._cvd: float = 0.0

    @property
    def current(self) -> float:
        return self._cvd

    def update_from_trades(self, trades: Iterable[TradeEvent]) -> float:
        """Updates the CVD with a list of aggressive trades."""
        delta = 0.0
        trades = list(trades)
        for t in trades:
            if t.is_buy:
                delta += t.qty
            else:
                delta -= t.qty

        self._cvd += delta
        ts = time.time() if not trades else trades[-1].ts
        self._history.append((ts, self._cvd))
        return self._cvd

    def get_recent_min(self, window_sec: int, now: Optional[float] = None) -> Optional[float]:
        """Returns the minimum CVD in the given time window."""
        if now is None:
            now = time.time()

        vals = [cvd for ts, cvd in self._history if ts >= now - window_sec]
        return min(vals) if vals else None

    def get_recent_max(self, window_sec: int, now: Optional[float] = None) -> Optional[float]:
        """Returns the maximum CVD in the given time window."""
        if now is None:
            now = time.time()

        vals = [cvd for ts, cvd in self._history if ts >= now - window_sec]
        return max(vals) if vals else None
