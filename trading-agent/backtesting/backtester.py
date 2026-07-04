"""
Backtesting Engine.

Runs the trading agent against historical data to validate strategies.
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.historical_data import get_historical_data_manager, HistoricalCandle
from backtesting.simulator import get_market_simulator, SimulatedTrade, SimulatedDay


@dataclass
class BacktestResult:
    """Complete backtest result."""
    symbol: str
    start_date: datetime
    end_date: datetime
    starting_capital: float
    ending_capital: float
    total_return_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    max_win: float
    max_loss: float
    max_drawdown: float
    sharpe_ratio: float
    trades: List[SimulatedTrade]
    daily_pnl: List[Dict]
    by_strategy: Dict[str, Dict]


class Backtester:
    """
    Main backtesting engine.
    
    Features:
    - Runs trading strategies against historical data
    - Simulates realistic market conditions
    - Tracks detailed performance metrics
    - Supports custom strategy functions
    """
    
    def __init__(self, starting_capital: float = 20000):
        self.data_manager = get_historical_data_manager()
        self.simulator = get_market_simulator()
        self.starting_capital = starting_capital
    
    def run_backtest(
        self,
        symbol: str,
        strategy_fn: Callable,
        days: int = 30,
        expiry_cycle_days: int = 7,
    ) -> BacktestResult:
        """
        Run backtest with a given strategy.
        
        Args:
            symbol: NIFTY, BANKNIFTY, FINNIFTY
            strategy_fn: Function(day: SimulatedDay, simulator: MarketSimulator) -> Optional[trade]
            days: Number of historical days to test
            expiry_cycle_days: Weekly expiry cycle (7 for weekly)
        
        Returns:
            BacktestResult with full analysis
        """
        print(f"\n{'='*60}")
        print(f"BACKTESTING: {symbol}")
        print(f"Period: Last {days} days")
        print(f"Starting Capital: Rs {self.starting_capital:,}")
        print(f"{'='*60}\n")
        
        # Reset simulator
        self.simulator.reset(self.starting_capital)
        
        # Fetch historical data
        candles = self.data_manager.fetch_spot_history(symbol, days)
        
        if not candles:
            print("ERROR: Could not fetch historical data")
            return self._empty_result(symbol)
        
        print(f"Loaded {len(candles)} trading days")
        
        daily_pnl = []
        capital_curve = [self.starting_capital]
        
        # Run through each day
        for i, candle in enumerate(candles):
            # Calculate days to expiry (weekly cycle)
            days_to_expiry = expiry_cycle_days - (i % expiry_cycle_days)
            if days_to_expiry == 0:
                days_to_expiry = expiry_cycle_days
            
            # Load day into simulator
            day = self.simulator.load_day(
                symbol=symbol,
                candle=candle,
                days_to_expiry=days_to_expiry,
            )
            
            # Let strategy decide
            trade = strategy_fn(day, self.simulator)
            
            # Simulate outcome if trade was made
            if trade:
                trade = self.simulator.simulate_day_outcome(trade)
                
                daily_pnl.append({
                    "date": candle.timestamp.strftime("%Y-%m-%d"),
                    "spot_close": candle.close,
                    "trade": trade.trade_id,
                    "pnl": trade.pnl,
                    "status": trade.status,
                    "capital": self.simulator.current_capital,
                })
                
                status_emoji = "✅" if trade.pnl > 0 else "❌"
                print(f"  {candle.timestamp.strftime('%Y-%m-%d')}: {trade.strike} {trade.option_type} "
                      f"| Entry: {trade.entry_price:.1f} | Exit: {trade.exit_price:.1f} "
                      f"| P&L: {trade.pnl:+.0f} {status_emoji} | {trade.status}")
            else:
                daily_pnl.append({
                    "date": candle.timestamp.strftime("%Y-%m-%d"),
                    "spot_close": candle.close,
                    "trade": None,
                    "pnl": 0,
                    "status": "NO_TRADE",
                    "capital": self.simulator.current_capital,
                })
            
            capital_curve.append(self.simulator.current_capital)
        
        # Calculate metrics
        summary = self.simulator.get_summary()
        max_drawdown = self._calculate_max_drawdown(capital_curve)
        sharpe = self._calculate_sharpe([d["pnl"] for d in daily_pnl if d["pnl"] != 0])
        
        # Group by strategy type
        by_strategy = self._group_by_strategy()
        
        result = BacktestResult(
            symbol=symbol,
            start_date=candles[0].timestamp,
            end_date=candles[-1].timestamp,
            starting_capital=self.starting_capital,
            ending_capital=summary["final_capital"],
            total_return_pct=summary["return_pct"],
            total_trades=summary["total_trades"],
            winning_trades=summary["winning_trades"],
            losing_trades=summary["losing_trades"],
            win_rate=summary["win_rate"],
            total_pnl=summary["total_pnl"],
            avg_win=summary["avg_win"],
            avg_loss=summary["avg_loss"],
            max_win=summary["max_win"],
            max_loss=summary["max_loss"],
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe,
            trades=self.simulator.trades,
            daily_pnl=daily_pnl,
            by_strategy=by_strategy,
        )
        
        self._print_summary(result)
        
        return result
    
    def _calculate_max_drawdown(self, capital_curve: List[float]) -> float:
        """Calculate maximum drawdown percentage."""
        if len(capital_curve) < 2:
            return 0
        
        peak = capital_curve[0]
        max_dd = 0
        
        for capital in capital_curve:
            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak * 100
            max_dd = max(max_dd, dd)
        
        return round(max_dd, 2)
    
    def _calculate_sharpe(self, returns: List[float], risk_free_rate: float = 0.065) -> float:
        """Calculate Sharpe ratio."""
        if len(returns) < 5:
            return 0
        
        import math
        
        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        
        if std_dev == 0:
            return 0
        
        # Annualize (assuming daily)
        annual_return = avg_return * 252
        annual_std = std_dev * math.sqrt(252)
        
        sharpe = (annual_return - risk_free_rate * self.starting_capital / 100) / annual_std
        
        return round(sharpe, 2)
    
    def _group_by_strategy(self) -> Dict[str, Dict]:
        """Group trades by strategy type."""
        strategies = {}
        
        for trade in self.simulator.trades:
            # Determine strategy from option type and reasoning
            strategy = trade.option_type  # Simple: CE or PE
            
            if strategy not in strategies:
                strategies[strategy] = {
                    "total": 0,
                    "wins": 0,
                    "losses": 0,
                    "pnl": 0,
                }
            
            strategies[strategy]["total"] += 1
            if trade.pnl > 0:
                strategies[strategy]["wins"] += 1
            else:
                strategies[strategy]["losses"] += 1
            strategies[strategy]["pnl"] += trade.pnl
        
        # Calculate win rates
        for strategy in strategies:
            s = strategies[strategy]
            s["win_rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] > 0 else 0
            s["pnl"] = round(s["pnl"], 2)
        
        return strategies
    
    def _empty_result(self, symbol: str) -> BacktestResult:
        """Return empty result on error."""
        return BacktestResult(
            symbol=symbol,
            start_date=datetime.now(),
            end_date=datetime.now(),
            starting_capital=self.starting_capital,
            ending_capital=self.starting_capital,
            total_return_pct=0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            total_pnl=0,
            avg_win=0,
            avg_loss=0,
            max_win=0,
            max_loss=0,
            max_drawdown=0,
            sharpe_ratio=0,
            trades=[],
            daily_pnl=[],
            by_strategy={},
        )
    
    def _print_summary(self, result: BacktestResult):
        """Print backtest summary."""
        print(f"\n{'='*60}")
        print("BACKTEST RESULTS")
        print(f"{'='*60}")
        print(f"Symbol: {result.symbol}")
        print(f"Period: {result.start_date.strftime('%Y-%m-%d')} to {result.end_date.strftime('%Y-%m-%d')}")
        print()
        print(f"Starting Capital: Rs {result.starting_capital:,.0f}")
        print(f"Ending Capital:   Rs {result.ending_capital:,.0f}")
        print(f"Total P&L:        Rs {result.total_pnl:+,.0f}")
        print(f"Return:           {result.total_return_pct:+.1f}%")
        print()
        print(f"Total Trades:     {result.total_trades}")
        print(f"Winning Trades:   {result.winning_trades}")
        print(f"Losing Trades:    {result.losing_trades}")
        print(f"Win Rate:         {result.win_rate:.1f}%")
        print()
        print(f"Average Win:      Rs {result.avg_win:+,.0f}")
        print(f"Average Loss:     Rs {result.avg_loss:+,.0f}")
        print(f"Max Win:          Rs {result.max_win:+,.0f}")
        print(f"Max Loss:         Rs {result.max_loss:+,.0f}")
        print()
        print(f"Max Drawdown:     {result.max_drawdown:.1f}%")
        print(f"Sharpe Ratio:     {result.sharpe_ratio:.2f}")
        print()
        
        if result.by_strategy:
            print("Performance by Type:")
            for strategy, stats in result.by_strategy.items():
                print(f"  {strategy}: {stats['total']} trades, "
                      f"{stats['win_rate']:.0f}% win rate, "
                      f"Rs {stats['pnl']:+,.0f}")
        
        print(f"{'='*60}")
        
        # Verdict
        if result.total_return_pct > 0 and result.win_rate > 50:
            print("VERDICT: PROFITABLE - Strategy validated!")
        elif result.total_return_pct > 0:
            print("VERDICT: PROFITABLE but low win rate - needs refinement")
        elif result.win_rate > 50:
            print("VERDICT: Good win rate but negative P&L - position sizing issue")
        else:
            print("VERDICT: NOT PROFITABLE - Strategy needs work")


# ============== BUILT-IN STRATEGIES ==============

def strategy_simple_momentum(day: SimulatedDay, simulator) -> Optional[SimulatedTrade]:
    """
    Simple momentum strategy:
    - Buy CE if gap up > 0.3%
    - Buy PE if gap down > 0.3%
    """
    if not day.intraday_path or len(day.intraday_path) < 2:
        return None
    
    # Calculate gap from previous close (use open as proxy)
    gap_pct = (day.spot_open - day.spot_close) / day.spot_close * 100
    
    # Find ATM strike
    strike_interval = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(day.symbol, 50)
    atm_strike = round(day.spot_open / strike_interval) * strike_interval
    
    # Determine trade
    if gap_pct > 0.3:
        # Gap up - buy CE
        return simulator.enter_trade(
            strike=atm_strike,
            option_type="CE",
            lots=1,
            stop_loss_pct=40,
            target_pct=50,
            reasoning="Gap up momentum - buying CE",
        )
    elif gap_pct < -0.3:
        # Gap down - buy PE
        return simulator.enter_trade(
            strike=atm_strike,
            option_type="PE",
            lots=1,
            stop_loss_pct=40,
            target_pct=50,
            reasoning="Gap down momentum - buying PE",
        )
    
    return None


def strategy_mean_reversion(day: SimulatedDay, simulator) -> Optional[SimulatedTrade]:
    """
    Mean reversion strategy:
    - Buy PE if gap up > 0.5% (expect pullback)
    - Buy CE if gap down > 0.5% (expect bounce)
    """
    gap_pct = (day.spot_open - day.spot_close) / day.spot_close * 100
    
    strike_interval = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(day.symbol, 50)
    atm_strike = round(day.spot_open / strike_interval) * strike_interval
    
    if gap_pct > 0.5:
        # Gap up - expect pullback, buy PE
        return simulator.enter_trade(
            strike=atm_strike,
            option_type="PE",
            lots=1,
            stop_loss_pct=35,
            target_pct=40,
            reasoning="Mean reversion - gap up pullback expected",
        )
    elif gap_pct < -0.5:
        # Gap down - expect bounce, buy CE
        return simulator.enter_trade(
            strike=atm_strike,
            option_type="CE",
            lots=1,
            stop_loss_pct=35,
            target_pct=40,
            reasoning="Mean reversion - gap down bounce expected",
        )
    
    return None


def strategy_trend_following(day: SimulatedDay, simulator) -> Optional[SimulatedTrade]:
    """
    Trend following with volatility filter:
    - Buy CE if open > previous close and within trending range
    - Buy PE if open < previous close and within trending range
    """
    # Simple trend check using day's range expectation
    expected_range = day.spot_open * 0.01  # 1% expected range
    actual_range = day.spot_high - day.spot_low
    
    # Only trade if day has decent range (trending)
    if actual_range < expected_range * 0.5:
        return None  # Range-bound day, skip
    
    strike_interval = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(day.symbol, 50)
    atm_strike = round(day.spot_open / strike_interval) * strike_interval
    
    # Determine trend direction from open vs midpoint of range
    mid = (day.spot_high + day.spot_low) / 2
    
    if day.spot_close > mid:
        # Bullish trend
        return simulator.enter_trade(
            strike=atm_strike + strike_interval,  # Slightly OTM
            option_type="CE",
            lots=1,
            stop_loss_pct=50,
            target_pct=80,
            reasoning="Trend following - bullish trend detected",
        )
    else:
        # Bearish trend
        return simulator.enter_trade(
            strike=atm_strike - strike_interval,  # Slightly OTM
            option_type="PE",
            lots=1,
            stop_loss_pct=50,
            target_pct=80,
            reasoning="Trend following - bearish trend detected",
        )


def run_quick_backtest(symbol: str = "NIFTY", days: int = 30, capital: float = 20000):
    """Convenience function to run a quick backtest."""
    backtester = Backtester(starting_capital=capital)
    return backtester.run_backtest(
        symbol=symbol,
        strategy_fn=strategy_simple_momentum,
        days=days,
    )


# Module test
if __name__ == "__main__":
    print("Running backtest on NIFTY with momentum strategy...")
    result = run_quick_backtest("NIFTY", 30, 20000)
