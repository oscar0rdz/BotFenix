import csv
from pathlib import Path
from typing import Optional


class FeaturesLogger:
    def __init__(self, path: str):
        self.path = Path(path)
        self._ensure_header()

    def _ensure_header(self):
        if not self.path.exists():
            with self.path.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "symbol",
                        "ts",
                        "price",
                        "cvd",
                        "imbalance",
                        "smooth_imbalance",
                        "vol",
                        "vol_norm",
                        "cvd_slope",
                        "has_position",
                        "position_side",
                        "signal_side",
                        "signal_score",
                        "equity",
                    ]
                )

    def log_snapshot(
        self,
        symbol: str,
        ts: float,
        price: float,
        cvd: float,
        imbalance: float,
        vol: Optional[float],
        has_position: bool,
        position_side: str,
        signal_side: Optional[str],
        signal_score: Optional[float],
        equity: float,
        smooth_imbalance: Optional[float] = None,
        vol_norm: Optional[float] = None,
        cvd_slope: Optional[float] = None,
    ):
        with self.path.open("a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    symbol.upper(),
                    f"{ts:.2f}",
                    f"{price:.4f}",
                    f"{cvd:.2f}",
                    f"{imbalance:.4f}",
                    f"{smooth_imbalance:.4f}" if smooth_imbalance is not None else "",
                    f"{vol:.8f}" if vol is not None else "",
                    f"{vol_norm:.4f}" if vol_norm is not None else "",
                    f"{cvd_slope:.2f}" if cvd_slope is not None else "",
                    int(has_position),
                    position_side or "",
                    signal_side or "",
                    f"{signal_score:.2f}" if signal_score is not None else "",
                    f"{equity:.6f}",
                ]
            )
