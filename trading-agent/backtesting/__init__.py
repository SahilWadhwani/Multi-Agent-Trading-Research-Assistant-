"""
Backtesting Module for QUANT-1 Trading Agent.

Test strategies on historical data before risking real money.
"""

from .historical_data import (
    HistoricalDataManager,
    get_historical_data_manager,
    HistoricalCandle,
    SimulatedOptionPrice,
)

from .simulator import (
    MarketSimulator,
    get_market_simulator,
    SimulatedTrade,
    SimulatedDay,
    TradeAction,
)

from .backtester import (
    Backtester,
    BacktestResult,
    run_quick_backtest,
    strategy_simple_momentum,
    strategy_mean_reversion,
    strategy_trend_following,
)

__all__ = [
    "HistoricalDataManager",
    "get_historical_data_manager",
    "HistoricalCandle",
    "SimulatedOptionPrice",
    "MarketSimulator",
    "get_market_simulator",
    "SimulatedTrade",
    "SimulatedDay",
    "TradeAction",
    "Backtester",
    "BacktestResult",
    "run_quick_backtest",
    "strategy_simple_momentum",
    "strategy_mean_reversion",
    "strategy_trend_following",
]
