import asyncio
import time
from typing import List

import orjson
import websockets

from core.models import TradeEvent, OrderBookSnapshot, MarketSnapshot
from config import EMIT_INTERVAL


class BinanceConnector:
    """Simplified WebSocket client for Binance Futures (public data)."""

    def __init__(self, symbol: str, emit_interval: float = EMIT_INTERVAL) -> None:
        self.symbol = symbol.lower()
        self.emit_interval = emit_interval
        self._depth_stream = f"{self.symbol}@depth10@100ms"
        self._trades_stream = f"{self.symbol}@aggTrade"
        self._ws_url = (
            "wss://fstream.binance.com/stream?streams="
            f"{self._depth_stream}/{self._trades_stream}"
        )

        self._bids: List[tuple[float, float]] = []
        self._asks: List[tuple[float, float]] = []
        self._trades_buffer: List[TradeEvent] = []

    def _handle_depth(self, data: dict) -> None:
        bids = [(float(p), float(q)) for p, q in data.get("b", []) if float(q) > 0]
        asks = [(float(p), float(q)) for p, q in data.get("a", []) if float(q) > 0]

        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])

        self._bids = bids
        self._asks = asks

    def _handle_trade(self, data: dict) -> None:
        price = float(data["p"])
        qty = float(data["q"])
        is_buyer_maker = data["m"]  # True = buyer is maker => aggressor is seller
        is_buy = not is_buyer_maker

        event_time = data.get("T") or data.get("E") or int(time.time() * 1000)
        ts = event_time / 1000.0

        trade = TradeEvent(ts=ts, price=price, qty=qty, is_buy=is_buy)
        self._trades_buffer.append(trade)

    async def stream(self):
        """Async generator that emits MarketSnapshot every `emit_interval` seconds."""
        while True:
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    last_emit = time.time()
                    self._bids = []
                    self._asks = []
                    self._trades_buffer = []

                    async for msg in ws:
                        payload = orjson.loads(msg)
                        stream = payload.get("stream")
                        data = payload.get("data", {})

                        if stream == self._depth_stream:
                            self._handle_depth(data)
                        elif stream == self._trades_stream:
                            self._handle_trade(data)

                        now = time.time()
                        if now - last_emit >= self.emit_interval:
                            if not self._bids or not self._asks:
                                last_emit = now
                                self._trades_buffer.clear()
                                continue

                            best_bid = self._bids[0][0]
                            best_ask = self._asks[0][0]
                            mid_price = (best_bid + best_ask) / 2.0

                            ob_snapshot = OrderBookSnapshot(
                                ts=now,
                                bids=list(self._bids),
                                asks=list(self._asks),
                            )

                            snapshot = MarketSnapshot(
                                ts=now,
                                mid_price=mid_price,
                                order_book=ob_snapshot,
                                trades=list(self._trades_buffer),
                            )

                            yield snapshot

                            last_emit = now
                            self._trades_buffer.clear()
            except Exception as exc:
                print(f"WebSocket disconnected: {exc}. Retrying in 3s...")
                await asyncio.sleep(3)
