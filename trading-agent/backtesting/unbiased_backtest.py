"""
UNBIASED BACKTESTING ENGINE

Fixes the lookahead bias problem:
- At decision time, only use data that was available at that moment
- Previous day's OHLC: KNOWN
- Today's OPEN: KNOWN (just happened)
- Today's HIGH/LOW/CLOSE: UNKNOWN (future data)

The strategy decides based on what it would have known at 9:20 AM.
Then we simulate through the day to see what would have happened.
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass
import math
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class DayData:
    """OHLCV data for a single day."""
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass
class DecisionContext:
    """
    What the strategy knows at decision time (9:20 AM).
    
    DOES NOT include today's high/low/close - that's the future!
    """
    date: datetime
    symbol: str
    
    # Previous day (fully known)
    prev_open: float
    prev_high: float
    prev_low: float
    prev_close: float
    
    # Today (only open is known at 9:20 AM)
    today_open: float
    
    # Derived (can calculate from known data)
    gap_pct: float  # (today_open - prev_close) / prev_close * 100
    prev_range_pct: float  # (prev_high - prev_low) / prev_close * 100
    prev_trend: str  # BULLISH if prev_close > prev_open else BEARISH
    
    # Historical volatility (known from past data)
    avg_daily_range_pct: float  # Average of last N days' ranges
    
    # Simulated IV (would come from option chain in real trading)
    simulated_iv: float


@dataclass
class BacktestTrade:
    """A trade made during backtest."""
    trade_id: str
    date: datetime
    symbol: str
    
    # Entry
    strike: float
    option_type: str  # CE or PE
    entry_premium: float
    lots: int
    lot_size: int
    
    # Risk management
    stop_loss_pct: float
    target_pct: float
    stop_loss_price: float
    target_price: float
    
    # Exit
    exit_premium: float = 0
    exit_reason: str = ""  # TARGET, STOP_LOSS, EOD
    
    # P&L
    pnl: float = 0
    pnl_pct: float = 0
    
    # Context at entry
    reasoning: str = ""
    confidence: float = 0


@dataclass
class BacktestResult:
    """Complete backtest results."""
    symbol: str
    period_start: datetime
    period_end: datetime
    starting_capital: float
    ending_capital: float
    
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    
    total_pnl: float
    avg_win: float
    avg_loss: float
    max_win: float
    max_loss: float
    
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    
    trades: List[BacktestTrade]
    daily_equity: List[Tuple[datetime, float]]


class UnbiasedBacktester:
    """
    Backtester that prevents lookahead bias.
    
    Key principle: At decision time, only use data available at that moment.
    """
    
    LOT_SIZES = {
        "NIFTY": 65,
        "BANKNIFTY": 30,
        "FINNIFTY": 40,
    }
    
    STRIKE_INTERVALS = {
        "NIFTY": 50,
        "BANKNIFTY": 100,
        "FINNIFTY": 50,
    }
    
    # Average IV by symbol (for premium estimation)
    AVG_IV = {
        "NIFTY": 65.13,
        "BANKNIFTY": 30.18,
        "FINNIFTY": 0.15,
    }
    
    def __init__(self, starting_capital: float = 20000):
        self.starting_capital = starting_capital
        self.current_capital = starting_capital
        self.trades: List[BacktestTrade] = []
        self.daily_equity: List[Tuple[datetime, float]] = []
        self.trade_counter = 0
    
    def reset(self):
        """Reset backtester state."""
        self.current_capital = self.starting_capital
        self.trades = []
        self.daily_equity = []
        self.trade_counter = 0
    
    def run(
        self,
        symbol: str,
        historical_data: List[DayData],
        strategy_fn: Callable[[DecisionContext], Optional[Dict]],
        days_to_expiry: int = 7,
    ) -> BacktestResult:
        """
        Run backtest with a strategy.
        
        Args:
            symbol: NIFTY, BANKNIFTY, etc.
            historical_data: List of daily OHLCV data (oldest first)
            strategy_fn: Function(context) -> {direction, stop_loss_pct, target_pct, confidence} or None
            days_to_expiry: Assumed days to expiry
        
        Returns:
            BacktestResult
        """
        self.reset()
        symbol = symbol.upper()
        
        print(f"\n{'='*60}")
        print(f"UNBIASED BACKTEST: {symbol}")
        print(f"Period: {len(historical_data)} trading days")
        print(f"Capital: Rs {self.starting_capital:,.0f}")
        print(f"{'='*60}\n")
        
        if len(historical_data) < 10:
            print("ERROR: Need at least 10 days of data")
            return self._empty_result(symbol)
        
        lot_size = self.LOT_SIZES.get(symbol, 50)
        strike_interval = self.STRIKE_INTERVALS.get(symbol, 50)
        avg_iv = self.AVG_IV.get(symbol, 0.15)
        
        # Calculate historical volatility from first 5 days
        recent_ranges = []
        
        # Process each day (skip first 5 for historical context)
        for i in range(5, len(historical_data)):
            today = historical_data[i]
            prev = historical_data[i - 1]
            
            # Calculate recent range for volatility
            if len(recent_ranges) >= 5:
                recent_ranges.pop(0)
            recent_ranges.append((prev.high - prev.low) / prev.close * 100)
            avg_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 1.0
            
            # Build decision context (NO LOOKAHEAD!)
            context = DecisionContext(
                date=today.date,
                symbol=symbol,
                prev_open=prev.open,
                prev_high=prev.high,
                prev_low=prev.low,
                prev_close=prev.close,
                today_open=today.open,  # This is the ONLY thing known about today!
                gap_pct=(today.open - prev.close) / prev.close * 100,
                prev_range_pct=(prev.high - prev.low) / prev.close * 100,
                prev_trend="BULLISH" if prev.close > prev.open else "BEARISH",
                avg_daily_range_pct=avg_range,
                simulated_iv=avg_iv * 100 * (1 + abs(today.open - prev.close) / prev.close),
            )
            
            # Get strategy decision
            decision = strategy_fn(context)
            
            if decision is None:
                # No trade today
                self.daily_equity.append((today.date, self.current_capital))
                continue
            
            # Extract decision details
            direction = decision.get("direction", "BUY_CE")
            stop_loss_pct = decision.get("stop_loss_pct", 40)
            target_pct = decision.get("target_pct", 50)
            confidence = decision.get("confidence", 0.5)
            
            # Determine strike and option type
            option_type = "CE" if "CE" in direction else "PE"
            atm_strike = round(today.open / strike_interval) * strike_interval
            
            # Slight OTM for better leverage
            if option_type == "CE":
                strike = atm_strike + strike_interval
            else:
                strike = atm_strike - strike_interval
            
            # Estimate entry premium using Black-Scholes
            entry_premium = self._estimate_premium(
                spot=today.open,
                strike=strike,
                option_type=option_type,
                iv=context.simulated_iv / 100,
                days_to_expiry=days_to_expiry,
            )
            
            if entry_premium < 10:
                continue  # Skip if premium too low
            
            # Calculate position size
            max_trade_value = min(self.current_capital * 0.5, 12000)
            lots = max(1, int(max_trade_value / (entry_premium * lot_size)))
            trade_value = entry_premium * lot_size * lots
            
            if trade_value > self.current_capital:
                continue  # Can't afford
            
            # Calculate stop loss and target prices
            sl_price = entry_premium * (1 - stop_loss_pct / 100)
            target_price = entry_premium * (1 + target_pct / 100)
            
            # Now simulate through the day using ACTUAL data
            exit_premium, exit_reason = self._simulate_intraday(
                today=today,
                strike=strike,
                option_type=option_type,
                entry_premium=entry_premium,
                stop_loss_price=sl_price,
                target_price=target_price,
            )
            
            # Calculate P&L
            pnl = (exit_premium - entry_premium) * lot_size * lots
            pnl_pct = (exit_premium - entry_premium) / entry_premium * 100
            
            # Record trade
            self.trade_counter += 1
            trade = BacktestTrade(
                trade_id=f"BT_{today.date.strftime('%Y%m%d')}_{self.trade_counter}",
                date=today.date,
                symbol=symbol,
                strike=strike,
                option_type=option_type,
                entry_premium=entry_premium,
                lots=lots,
                lot_size=lot_size,
                stop_loss_pct=stop_loss_pct,
                target_pct=target_pct,
                stop_loss_price=sl_price,
                target_price=target_price,
                exit_premium=exit_premium,
                exit_reason=exit_reason,
                pnl=pnl,
                pnl_pct=pnl_pct,
                reasoning=decision.get("reasoning", ""),
                confidence=confidence,
            )
            
            self.trades.append(trade)
            self.current_capital += pnl
            self.daily_equity.append((today.date, self.current_capital))
            
            # Print trade
            emoji = "✅" if pnl > 0 else "❌"
            print(f"  {today.date.strftime('%Y-%m-%d')}: {strike} {option_type} | "
                  f"Entry: {entry_premium:.1f} → Exit: {exit_premium:.1f} | "
                  f"P&L: {pnl:+,.0f} {emoji} | {exit_reason}")
        
        # Calculate final metrics
        result = self._calculate_metrics(symbol)
        self._print_summary(result)
        
        return result
    
    def _estimate_premium(
        self,
        spot: float,
        strike: float,
        option_type: str,
        iv: float,
        days_to_expiry: int,
    ) -> float:
        """Estimate option premium using Black-Scholes approximation."""
        time_to_expiry = days_to_expiry / 365
        
        # Simplified Black-Scholes
        d1 = (math.log(spot / strike) + (0.05 + iv**2 / 2) * time_to_expiry) / (iv * math.sqrt(time_to_expiry))
        d2 = d1 - iv * math.sqrt(time_to_expiry)
        
        # Approximate N(d) using logistic function
        def norm_cdf(x):
            return 1 / (1 + math.exp(-1.7 * x))
        
        if option_type == "CE":
            price = spot * norm_cdf(d1) - strike * math.exp(-0.05 * time_to_expiry) * norm_cdf(d2)
        else:
            price = strike * math.exp(-0.05 * time_to_expiry) * norm_cdf(-d2) - spot * norm_cdf(-d1)
        
        return max(price, 5)  # Minimum premium
    
    def _simulate_intraday(
        self,
        today: DayData,
        strike: float,
        option_type: str,
        entry_premium: float,
        stop_loss_price: float,
        target_price: float,
    ) -> Tuple[float, str]:
        """
        Simulate intraday price movement and determine exit.
        
        Uses today's OHLC to simulate what could have happened.
        """
        spot_at_open = today.open
        spot_at_high = today.high
        spot_at_low = today.low
        spot_at_close = today.close
        
        # Calculate option price at various spot levels
        price_at_high = self._option_price_at_spot(spot_at_high, strike, option_type, entry_premium, spot_at_open)
        price_at_low = self._option_price_at_spot(spot_at_low, strike, option_type, entry_premium, spot_at_open)
        price_at_close = self._option_price_at_spot(spot_at_close, strike, option_type, entry_premium, spot_at_open)
        
        # Determine what happened first (random order for high/low)
        # In reality, we don't know if high or low came first
        hit_high_first = random.random() > 0.5
        
        if hit_high_first:
            check_order = [(price_at_high, "HIGH"), (price_at_low, "LOW")]
        else:
            check_order = [(price_at_low, "LOW"), (price_at_high, "HIGH")]
        
        for price, level in check_order:
            # Check if target was hit
            if price >= target_price:
                return target_price, "TARGET"
            # Check if stop loss was hit
            if price <= stop_loss_price:
                return stop_loss_price, "STOP_LOSS"
        
        # Neither hit, exit at close
        # Apply time decay (~1-2% for intraday)
        exit_price = max(price_at_close * 0.985, stop_loss_price * 0.5)
        
        return exit_price, "EOD"
    
    def _option_price_at_spot(
        self,
        new_spot: float,
        strike: float,
        option_type: str,
        entry_premium: float,
        entry_spot: float,
    ) -> float:
        """
        Estimate option price at a given spot level.
        Uses delta approximation.
        """
        # Approximate delta
        moneyness = (entry_spot - strike) / entry_spot
        
        if option_type == "CE":
            if moneyness > 0.02:  # ITM
                delta = 0.7
            elif moneyness < -0.02:  # OTM
                delta = 0.3
            else:  # ATM
                delta = 0.5
        else:  # PE
            if moneyness < -0.02:  # ITM for put
                delta = -0.7
            elif moneyness > 0.02:  # OTM for put
                delta = -0.3
            else:  # ATM
                delta = -0.5
        
        # Price change = delta * spot change
        spot_change = new_spot - entry_spot
        option_change = delta * spot_change
        
        new_price = entry_premium + option_change
        
        # Floor at intrinsic value
        if option_type == "CE":
            intrinsic = max(0, new_spot - strike)
        else:
            intrinsic = max(0, strike - new_spot)
        
        return max(new_price, intrinsic, 1)
    
    def _calculate_metrics(self, symbol: str) -> BacktestResult:
        """Calculate final backtest metrics."""
        if not self.trades:
            return self._empty_result(symbol)
        
        winning = [t for t in self.trades if t.pnl > 0]
        losing = [t for t in self.trades if t.pnl <= 0]
        
        total_pnl = sum(t.pnl for t in self.trades)
        avg_win = sum(t.pnl for t in winning) / len(winning) if winning else 0
        avg_loss = sum(t.pnl for t in losing) / len(losing) if losing else 0
        
        # Calculate max drawdown
        peak = self.starting_capital
        max_dd = 0
        for _, equity in self.daily_equity:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)
        
        # Calculate Sharpe ratio
        returns = [t.pnl / self.starting_capital for t in self.trades]
        if len(returns) >= 2:
            avg_return = sum(returns) / len(returns)
            variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
            std_dev = math.sqrt(variance)
            sharpe = (avg_return * 252 - 0.065) / (std_dev * math.sqrt(252)) if std_dev > 0 else 0
        else:
            sharpe = 0
        
        # Profit factor
        gross_profit = sum(t.pnl for t in winning)
        gross_loss = abs(sum(t.pnl for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        return BacktestResult(
            symbol=symbol,
            period_start=self.trades[0].date,
            period_end=self.trades[-1].date,
            starting_capital=self.starting_capital,
            ending_capital=self.current_capital,
            total_trades=len(self.trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=len(winning) / len(self.trades) * 100,
            total_pnl=total_pnl,
            avg_win=avg_win,
            avg_loss=avg_loss,
            max_win=max(t.pnl for t in self.trades),
            max_loss=min(t.pnl for t in self.trades),
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            profit_factor=profit_factor,
            trades=self.trades,
            daily_equity=self.daily_equity,
        )
    
    def _empty_result(self, symbol: str) -> BacktestResult:
        """Return empty result."""
        return BacktestResult(
            symbol=symbol,
            period_start=datetime.now(),
            period_end=datetime.now(),
            starting_capital=self.starting_capital,
            ending_capital=self.starting_capital,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0,
            total_pnl=0,
            avg_win=0,
            avg_loss=0,
            max_win=0,
            max_loss=0,
            max_drawdown_pct=0,
            sharpe_ratio=0,
            profit_factor=0,
            trades=[],
            daily_equity=[],
        )
    
    def _print_summary(self, result: BacktestResult):
        """Print backtest summary."""
        print(f"\n{'='*60}")
        print("BACKTEST RESULTS (UNBIASED)")
        print(f"{'='*60}")
        print(f"Symbol: {result.symbol}")
        print(f"Period: {result.period_start.strftime('%Y-%m-%d')} to {result.period_end.strftime('%Y-%m-%d')}")
        print()
        print(f"Starting Capital: Rs {result.starting_capital:,.0f}")
        print(f"Ending Capital:   Rs {result.ending_capital:,.0f}")
        print(f"Total P&L:        Rs {result.total_pnl:+,.0f}")
        print(f"Return:           {(result.ending_capital/result.starting_capital - 1)*100:+.1f}%")
        print()
        print(f"Total Trades:     {result.total_trades}")
        print(f"Winning:          {result.winning_trades}")
        print(f"Losing:           {result.losing_trades}")
        print(f"Win Rate:         {result.win_rate:.1f}%")
        print()
        print(f"Avg Win:          Rs {result.avg_win:+,.0f}")
        print(f"Avg Loss:         Rs {result.avg_loss:+,.0f}")
        print(f"Profit Factor:    {result.profit_factor:.2f}")
        print()
        print(f"Max Drawdown:     {result.max_drawdown_pct:.1f}%")
        print(f"Sharpe Ratio:     {result.sharpe_ratio:.2f}")
        print(f"{'='*60}")
        
        # Verdict
        if result.total_pnl > 0 and result.win_rate > 45 and result.profit_factor > 1.2:
            print("VERDICT: PROFITABLE - Strategy shows edge")
        elif result.total_pnl > 0:
            print("VERDICT: Slightly profitable - needs optimization")
        else:
            print("VERDICT: NOT PROFITABLE - Review strategy")


# ============== SAMPLE STRATEGIES (UNBIASED) ==============

def strategy_gap_momentum(context: DecisionContext) -> Optional[Dict]:
    """
    Gap momentum strategy (UNBIASED version).
    
    Only uses: prev_close, today_open (known at 9:20 AM)
    """
    gap = context.gap_pct
    
    # Need significant gap
    if abs(gap) < 0.3:
        return None  # No trade
    
    # Gap up: expect continuation (buy CE)
    # Gap down: expect continuation (buy PE)
    if gap > 0.3:
        return {
            "direction": "BUY_CE",
            "stop_loss_pct": 40,
            "target_pct": 50,
            "confidence": min(0.5 + abs(gap) / 10, 0.8),
            "reasoning": f"Gap up {gap:.1f}% - momentum continuation",
        }
    elif gap < -0.3:
        return {
            "direction": "BUY_PE",
            "stop_loss_pct": 40,
            "target_pct": 50,
            "confidence": min(0.5 + abs(gap) / 10, 0.8),
            "reasoning": f"Gap down {gap:.1f}% - momentum continuation",
        }
    
    return None


def strategy_gap_reversal(context: DecisionContext) -> Optional[Dict]:
    """
    Gap reversal strategy (UNBIASED version).
    
    Expects gaps to fill (mean reversion).
    """
    gap = context.gap_pct
    
    # Need significant gap
    if abs(gap) < 0.5:
        return None
    
    # Gap up: expect pullback (buy PE)
    # Gap down: expect bounce (buy CE)
    if gap > 0.5:
        return {
            "direction": "BUY_PE",
            "stop_loss_pct": 35,
            "target_pct": 45,
            "confidence": min(0.5 + abs(gap) / 15, 0.75),
            "reasoning": f"Gap up {gap:.1f}% - expecting fill/pullback",
        }
    elif gap < -0.5:
        return {
            "direction": "BUY_CE",
            "stop_loss_pct": 35,
            "target_pct": 45,
            "confidence": min(0.5 + abs(gap) / 15, 0.75),
            "reasoning": f"Gap down {gap:.1f}% - expecting bounce",
        }
    
    return None


def strategy_trend_continuation(context: DecisionContext) -> Optional[Dict]:
    """
    Trend continuation strategy (UNBIASED version).
    
    If previous day was trending, expect continuation.
    """
    # Previous day must have been a trending day
    if context.prev_range_pct < context.avg_daily_range_pct * 0.8:
        return None  # Previous day was range-bound
    
    # Gap should align with previous trend
    if context.prev_trend == "BULLISH" and context.gap_pct > 0:
        return {
            "direction": "BUY_CE",
            "stop_loss_pct": 45,
            "target_pct": 60,
            "confidence": 0.6,
            "reasoning": f"Bullish continuation - prev trend + gap up",
        }
    elif context.prev_trend == "BEARISH" and context.gap_pct < 0:
        return {
            "direction": "BUY_PE",
            "stop_loss_pct": 45,
            "target_pct": 60,
            "confidence": 0.6,
            "reasoning": f"Bearish continuation - prev trend + gap down",
        }
    
    return None


def generate_synthetic_data(symbol: str, days: int = 60) -> List[DayData]:
    """Generate synthetic historical data for testing with realistic gaps."""
    base_prices = {
        "NIFTY": 65,
        "BANKNIFTY": 30,
        "FINNIFTY": 22000,
    }
    
    random.seed(42)  # Reproducible
    
    base = base_prices.get(symbol.upper(), 24000)
    data = []
    prev_close = base
    
    for i in range(days):
        date = datetime.now() - timedelta(days=days - i)
        
        # Gap: difference between today's open and yesterday's close
        # Typical gaps are -1% to +1%, occasionally larger
        gap_pct = random.gauss(0, 0.005)  # ~0.5% std dev
        if random.random() < 0.15:  # 15% chance of larger gap
            gap_pct = random.gauss(0, 0.012)  # ~1.2% std dev
        
        open_price = prev_close * (1 + gap_pct)
        
        # Intraday movement
        daily_return = random.gauss(0.0001, 0.008)
        close_price = open_price * (1 + daily_return)
        
        # High/low with some randomness
        intraday_vol = abs(random.gauss(0, 0.006))
        high_price = max(open_price, close_price) * (1 + intraday_vol)
        low_price = min(open_price, close_price) * (1 - intraday_vol)
        
        data.append(DayData(
            date=date,
            open=round(open_price, 2),
            high=round(high_price, 2),
            low=round(low_price, 2),
            close=round(close_price, 2),
            volume=random.randint(100000, 500000),
        ))
        
        prev_close = close_price
    
    return data


def run_backtest(
    symbol: str = "NIFTY",
    days: int = 60,
    strategy: str = "gap_momentum",
    capital: float = 20000,
) -> BacktestResult:
    """Run a quick backtest with specified strategy."""
    # Get data
    data = generate_synthetic_data(symbol, days)
    
    # Select strategy
    strategies = {
        "gap_momentum": strategy_gap_momentum,
        "gap_reversal": strategy_gap_reversal,
        "trend_continuation": strategy_trend_continuation,
    }
    
    strategy_fn = strategies.get(strategy, strategy_gap_momentum)
    
    # Run backtest
    backtester = UnbiasedBacktester(starting_capital=capital)
    return backtester.run(symbol, data, strategy_fn)


# Test
if __name__ == "__main__":
    print("Testing UNBIASED backtest with gap momentum strategy...")
    result = run_backtest("NIFTY", 60, "gap_momentum", 20000)
    
    print("\n" + "="*60)
    print("Testing gap reversal strategy...")
    result2 = run_backtest("NIFTY", 60, "gap_reversal", 20000)
    
    print("\n" + "="*60)
    print("Testing trend continuation strategy...")
    result3 = run_backtest("NIFTY", 60, "trend_continuation", 20000)
