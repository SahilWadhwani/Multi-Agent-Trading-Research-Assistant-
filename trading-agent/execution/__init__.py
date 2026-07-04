"""
Execution Module for QUANT-1 Trading Agent.

Real-time price feed + trigger-based execution for options trading.
"""

from .trigger_engine import (
    TriggerEngine,
    get_trigger_engine,
    TriggerType,
    TriggerStatus,
    TradeTrigger,
    ExecutedOrder,
    create_momentum_triggers,
    create_breakout_triggers,
)

from .price_hub import PriceHub, get_price_hub

from .websocket_feed import (
    PriceFeedManager,
    get_price_feed_manager,
    start_price_feed,
    stop_price_feed,
)

from .exit_ticker import (
    ExitTicker,
    get_exit_ticker,
    enable_exit_ticker,
    disable_exit_ticker,
)

__all__ = [
    "TriggerEngine",
    "get_trigger_engine",
    "TriggerType",
    "TriggerStatus",
    "TradeTrigger",
    "ExecutedOrder",
    "create_momentum_triggers",
    "create_breakout_triggers",
    "PriceHub",
    "get_price_hub",
    "PriceFeedManager",
    "get_price_feed_manager",
    "start_price_feed",
    "stop_price_feed",
    "ExitTicker",
    "get_exit_ticker",
    "enable_exit_ticker",
    "disable_exit_ticker",
]
