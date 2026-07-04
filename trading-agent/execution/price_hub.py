"""
Thread-safe real-time price cache.

Serves as a single source of truth for latest prices across the system.
Fed by WebSocket (primary) or REST polling (fallback).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set

import pytz

IST = pytz.timezone("Asia/Kolkata")


@dataclass
class PriceTick:
    ltp: float
    timestamp: float  # monotonic time for staleness check
    ist_time: datetime = field(default_factory=lambda: datetime.now(IST))
    volume: int = 0
    oi: float = 0.0


class PriceHub:
    """
    Thread-safe price cache singleton.

    - Updated by WebSocket feed or REST fallback
    - Read by exit_manager and position_tracker for instant price lookups
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._prices: Dict[str, PriceTick] = {}
        self._subscriptions: Set[str] = set()
        self._callbacks: List = []

    def update(self, instrument_key: str, ltp: float, volume: int = 0, oi: float = 0.0) -> None:
        """Update price for an instrument (called by WS feed or REST poller)."""
        tick = PriceTick(
            ltp=ltp,
            timestamp=time.monotonic(),
            ist_time=datetime.now(IST),
            volume=volume,
            oi=oi,
        )
        with self._lock:
            self._prices[instrument_key] = tick

        for cb in self._callbacks:
            try:
                cb(instrument_key, ltp)
            except Exception:
                pass

    def get_ltp(self, instrument_key: str) -> Optional[float]:
        """Get latest traded price. Returns None if not available."""
        with self._lock:
            tick = self._prices.get(instrument_key)
        if tick is None:
            return None
        return tick.ltp

    def get_tick(self, instrument_key: str) -> Optional[PriceTick]:
        """Get full tick data."""
        with self._lock:
            return self._prices.get(instrument_key)

    def is_stale(self, instrument_key: str, max_age_s: float = 30.0) -> bool:
        """Check if price data is older than max_age_s seconds."""
        with self._lock:
            tick = self._prices.get(instrument_key)
        if tick is None:
            return True
        return (time.monotonic() - tick.timestamp) > max_age_s

    def add_subscription(self, instrument_key: str) -> None:
        """Mark an instrument key as needing price updates."""
        with self._lock:
            self._subscriptions.add(instrument_key)

    def remove_subscription(self, instrument_key: str) -> None:
        """Remove an instrument from active subscriptions."""
        with self._lock:
            self._subscriptions.discard(instrument_key)

    def get_subscriptions(self) -> Set[str]:
        """Get all instrument keys that need price updates."""
        with self._lock:
            return set(self._subscriptions)

    def add_callback(self, callback) -> None:
        """Register a callback for price updates: callback(instrument_key, ltp)."""
        self._callbacks.append(callback)

    def get_all_prices(self) -> Dict[str, float]:
        """Snapshot of all current prices."""
        with self._lock:
            return {k: v.ltp for k, v in self._prices.items()}

    def clear(self) -> None:
        """Clear all cached prices (for testing/reset)."""
        with self._lock:
            self._prices.clear()


_hub: Optional[PriceHub] = None
_hub_lock = threading.Lock()


def get_price_hub() -> PriceHub:
    """Get or create the global PriceHub singleton."""
    global _hub
    if _hub is None:
        with _hub_lock:
            if _hub is None:
                _hub = PriceHub()
    return _hub
