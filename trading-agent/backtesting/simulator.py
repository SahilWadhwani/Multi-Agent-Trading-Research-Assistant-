"""
Market Simulator for Backtesting.

Replays historical market conditions as if they were happening now.
Allows the trading agent to make decisions on past data.
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.historical_data import (
    get_historical_data_manager,
    HistoricalCandle,
    SimulatedOptionPrice,
)


class TradeAction(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class SimulatedTrade:
    """A simulated trade for backtesting."""
    trade_id: str
    symbol: str
    strike: float
    option_type: str  # CE or PE
    action: TradeAction
    quantity: int  # In lots
    entry_price: float
    entry_time: datetime
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    pnl: float = 0
    status: str = "OPEN"  # OPEN, CLOSED, STOPPED_OUT, TARGET_HIT
    reasoning: str = ""


@dataclass
class SimulatedDay:
    """Simulated market data for a single trading day."""
    date: datetime
    symbol: str
    spot_open: float
    spot_high: float
    spot_low: float
    spot_close: float
    option_chain: Dict[str, List[SimulatedOptionPrice]]
    intraday_path: List[float] = field(default_factory=list)  # Simulated intraday prices
    days_to_expiry: int = 7


class MarketSimulator:
    """
    Simulates market conditions for backtesting.
    
    Features:
    - Replays historical spot prices
    - Generates simulated option chains
    - Simulates intraday price movement
    - Tracks P&L of simulated trades
    """
    
    def __init__(self):
        self.data_manager = get_historical_data_manager()
        self.current_day: Optional[SimulatedDay] = None
        self.trades: List[SimulatedTrade] = []
        self.trade_counter = 0
        self.starting_capital = 20000
        self.current_capital = 20000
        self.lot_sizes = {
            "NIFTY": 50,
            "BANKNIFTY": 15,
            "FINNIFTY": 40,
        }
    
    def reset(self, starting_capital: float = 20000):
        """Reset simulator state."""
        self.trades = []
        self.trade_counter = 0
        self.starting_capital = starting_capital
        self.current_capital = starting_capital
        self.current_day = None
    
    def load_day(
        self,
        symbol: str,
        candle: HistoricalCandle,
        days_to_expiry: int = 7,
    ) -> SimulatedDay:
        """
        Load a historical day for simulation.
        
        Args:
            symbol: NIFTY, BANKNIFTY, etc.
            candle: Historical OHLCV data
            days_to_expiry: Days until nearest expiry
        
        Returns:
            SimulatedDay object ready for trading
        """
        # Generate option chain at day's open
        option_chain = self.data_manager.simulate_option_chain(
            symbol=symbol,
            spot_price=candle.open,
            days_to_expiry=days_to_expiry,
        )
        
        # Generate intraday price path
        intraday_path = self._generate_intraday_path(candle)
        
        self.current_day = SimulatedDay(
            date=candle.timestamp,
            symbol=symbol,
            spot_open=candle.open,
            spot_high=candle.high,
            spot_low=candle.low,
            spot_close=candle.close,
            option_chain=option_chain,
            intraday_path=intraday_path,
            days_to_expiry=days_to_expiry,
        )
        
        return self.current_day
    
    def _generate_intraday_path(self, candle: HistoricalCandle, points: int = 75) -> List[float]:
        """
        Generate a realistic intraday price path.
        
        Creates ~75 price points (approx 5-minute intervals in 6.25 hr day).
        Path moves from open through high/low to close.
        """
        import random
        random.seed(int(candle.timestamp.timestamp()) % 10000)
        
        path = [candle.open]
        current = candle.open
        
        # Determine if we hit high or low first
        hit_high_first = random.random() > 0.5
        
        # First third: move toward first extreme
        target1 = candle.high if hit_high_first else candle.low
        for _ in range(points // 3):
            step = (target1 - current) / (points // 3) + random.gauss(0, candle.open * 0.001)
            current += step
            current = max(candle.low, min(candle.high, current))
            path.append(round(current, 2))
        
        # Second third: move toward other extreme
        target2 = candle.low if hit_high_first else candle.high
        for _ in range(points // 3):
            step = (target2 - current) / (points // 3) + random.gauss(0, candle.open * 0.001)
            current += step
            current = max(candle.low, min(candle.high, current))
            path.append(round(current, 2))
        
        # Final third: move toward close
        remaining = points - len(path)
        for i in range(remaining):
            step = (candle.close - current) / (remaining - i + 1) + random.gauss(0, candle.open * 0.0005)
            current += step
            current = max(candle.low, min(candle.high, current))
            path.append(round(current, 2))
        
        # Ensure we end at close
        path[-1] = candle.close
        
        return path
    
    def get_option_price(
        self,
        strike: float,
        option_type: str,
        at_spot: float = None,
    ) -> Optional[float]:
        """
        Get option price at current or specified spot.
        
        Args:
            strike: Option strike price
            option_type: "CE" or "PE"
            at_spot: Spot price (default: current day's open)
        
        Returns:
            Option premium or None if not found
        """
        if not self.current_day:
            return None
        
        spot = at_spot or self.current_day.spot_open
        
        # If using different spot, recalculate
        if at_spot and at_spot != self.current_day.spot_open:
            chain = self.data_manager.simulate_option_chain(
                symbol=self.current_day.symbol,
                spot_price=spot,
                days_to_expiry=self.current_day.days_to_expiry,
            )
        else:
            chain = self.current_day.option_chain
        
        options = chain["calls"] if option_type.upper() == "CE" else chain["puts"]
        
        for opt in options:
            if opt.strike == strike:
                return opt.premium
        
        return None
    
    def enter_trade(
        self,
        strike: float,
        option_type: str,
        lots: int = 1,
        stop_loss_pct: float = 40,
        target_pct: float = 50,
        reasoning: str = "",
    ) -> Optional[SimulatedTrade]:
        """
        Enter a simulated trade.
        
        Args:
            strike: Option strike
            option_type: "CE" or "PE"
            lots: Number of lots to buy
            stop_loss_pct: Stop loss as % of premium
            target_pct: Target as % of premium
            reasoning: Why this trade
        
        Returns:
            SimulatedTrade object or None if cannot enter
        """
        if not self.current_day:
            return None
        
        entry_price = self.get_option_price(strike, option_type)
        if not entry_price or entry_price <= 0:
            return None
        
        lot_size = self.lot_sizes.get(self.current_day.symbol, 50)
        trade_cost = entry_price * lot_size * lots
        
        # Check capital
        if trade_cost > self.current_capital:
            return None
        
        self.trade_counter += 1
        trade_id = f"BT_{self.current_day.date.strftime('%Y%m%d')}_{self.trade_counter}"
        
        stop_loss = entry_price * (1 - stop_loss_pct / 100)
        target = entry_price * (1 + target_pct / 100)
        
        trade = SimulatedTrade(
            trade_id=trade_id,
            symbol=self.current_day.symbol,
            strike=strike,
            option_type=option_type,
            action=TradeAction.BUY,
            quantity=lots,
            entry_price=entry_price,
            entry_time=self.current_day.date.replace(hour=9, minute=20),
            stop_loss=stop_loss,
            target=target,
            reasoning=reasoning,
        )
        
        self.trades.append(trade)
        self.current_capital -= trade_cost
        
        return trade
    
    def simulate_day_outcome(self, trade: SimulatedTrade) -> SimulatedTrade:
        """
        Simulate trade outcome through the day.
        
        Checks if SL or target was hit during intraday movement.
        If neither, exits at day close or expiry value.
        """
        if not self.current_day or trade.status != "OPEN":
            return trade
        
        lot_size = self.lot_sizes.get(trade.symbol, 50)
        
        # Simulate through intraday path
        for i, spot in enumerate(self.current_day.intraday_path):
            # Calculate option price at this spot
            option_price = self._estimate_intraday_option_price(
                spot=spot,
                strike=trade.strike,
                option_type=trade.option_type,
                entry_spot=self.current_day.spot_open,
                entry_premium=trade.entry_price,
            )
            
            # Check stop loss
            if trade.stop_loss and option_price <= trade.stop_loss:
                trade.exit_price = trade.stop_loss
                trade.exit_time = self.current_day.date.replace(
                    hour=9 + (i * 5 // 60),
                    minute=(15 + i * 5) % 60
                )
                trade.status = "STOPPED_OUT"
                trade.pnl = (trade.exit_price - trade.entry_price) * lot_size * trade.quantity
                self.current_capital += (trade.exit_price * lot_size * trade.quantity)
                return trade
            
            # Check target
            if trade.target and option_price >= trade.target:
                trade.exit_price = trade.target
                trade.exit_time = self.current_day.date.replace(
                    hour=9 + (i * 5 // 60),
                    minute=(15 + i * 5) % 60
                )
                trade.status = "TARGET_HIT"
                trade.pnl = (trade.exit_price - trade.entry_price) * lot_size * trade.quantity
                self.current_capital += (trade.exit_price * lot_size * trade.quantity)
                return trade
        
        # Exit at close
        close_price = self._estimate_intraday_option_price(
            spot=self.current_day.spot_close,
            strike=trade.strike,
            option_type=trade.option_type,
            entry_spot=self.current_day.spot_open,
            entry_premium=trade.entry_price,
        )
        
        trade.exit_price = close_price
        trade.exit_time = self.current_day.date.replace(hour=15, minute=30)
        trade.status = "CLOSED"
        trade.pnl = (trade.exit_price - trade.entry_price) * lot_size * trade.quantity
        self.current_capital += (trade.exit_price * lot_size * trade.quantity)
        
        return trade
    
    def _estimate_intraday_option_price(
        self,
        spot: float,
        strike: float,
        option_type: str,
        entry_spot: float,
        entry_premium: float,
    ) -> float:
        """
        Estimate option price at a given spot during the day.
        
        Uses delta approximation: option_change ≈ delta × spot_change
        With decay for time passing during the day.
        """
        # Get delta from original chain
        chain = self.current_day.option_chain
        options = chain["calls"] if option_type.upper() == "CE" else chain["puts"]
        
        delta = 0.5  # Default
        for opt in options:
            if opt.strike == strike:
                delta = abs(opt.delta)
                break
        
        # Calculate price change
        spot_change = spot - entry_spot
        option_change = delta * spot_change
        
        # Apply small time decay (about 1-2% for intraday)
        time_decay = entry_premium * 0.015
        
        new_price = entry_premium + option_change - time_decay
        
        # Floor at intrinsic value
        if option_type.upper() == "CE":
            intrinsic = max(0, spot - strike)
        else:
            intrinsic = max(0, strike - spot)
        
        return max(intrinsic, new_price, 0.5)  # Minimum 0.5 to avoid zero
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all simulated trades."""
        if not self.trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "total_pnl": 0,
                "win_rate": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "max_win": 0,
                "max_loss": 0,
                "final_capital": self.current_capital,
                "return_pct": 0,
            }
        
        closed_trades = [t for t in self.trades if t.status != "OPEN"]
        winning = [t for t in closed_trades if t.pnl > 0]
        losing = [t for t in closed_trades if t.pnl <= 0]
        
        total_pnl = sum(t.pnl for t in closed_trades)
        
        return {
            "total_trades": len(closed_trades),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(len(winning) / len(closed_trades) * 100, 1) if closed_trades else 0,
            "avg_win": round(sum(t.pnl for t in winning) / len(winning), 2) if winning else 0,
            "avg_loss": round(sum(t.pnl for t in losing) / len(losing), 2) if losing else 0,
            "max_win": round(max((t.pnl for t in winning), default=0), 2),
            "max_loss": round(min((t.pnl for t in losing), default=0), 2),
            "final_capital": round(self.current_capital, 2),
            "return_pct": round((self.current_capital - self.starting_capital) / self.starting_capital * 100, 2),
            "by_status": {
                "TARGET_HIT": len([t for t in closed_trades if t.status == "TARGET_HIT"]),
                "STOPPED_OUT": len([t for t in closed_trades if t.status == "STOPPED_OUT"]),
                "CLOSED": len([t for t in closed_trades if t.status == "CLOSED"]),
            }
        }


# Singleton
_simulator = None

def get_market_simulator() -> MarketSimulator:
    """Get or create market simulator singleton."""
    global _simulator
    if _simulator is None:
        _simulator = MarketSimulator()
    return _simulator
