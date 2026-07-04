"""
Options Strategy Engine for Indian Markets.

Supports:
- Directional: Long Call, Long Put, Synthetic Long/Short
- Spreads: Bull/Bear Call/Put Spreads, Calendar Spreads
- Neutral: Straddles, Strangles, Iron Condors, Iron Butterflies
- Hedging: Protective Put, Covered Call

Calculates:
- Max profit/loss
- Breakeven points
- Risk-reward ratio
- Probability of profit (using IV)
- Margin requirements
"""

import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
import math
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_feeds.options_greeks import get_greeks_calculator, OptionType


class StrategyType(Enum):
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    SHORT_CALL = "short_call"
    SHORT_PUT = "short_put"
    BULL_CALL_SPREAD = "bull_call_spread"
    BEAR_CALL_SPREAD = "bear_call_spread"
    BULL_PUT_SPREAD = "bull_put_spread"
    BEAR_PUT_SPREAD = "bear_put_spread"
    LONG_STRADDLE = "long_straddle"
    SHORT_STRADDLE = "short_straddle"
    LONG_STRANGLE = "long_strangle"
    SHORT_STRANGLE = "short_strangle"
    IRON_CONDOR = "iron_condor"
    IRON_BUTTERFLY = "iron_butterfly"
    CALENDAR_SPREAD = "calendar_spread"
    PROTECTIVE_PUT = "protective_put"
    COVERED_CALL = "covered_call"


@dataclass
class StrategyLeg:
    """Individual leg of an option strategy."""
    action: str  # BUY or SELL
    option_type: str  # CE or PE
    strike: float
    premium: float
    quantity: int  # In lots
    expiry: str


@dataclass
class StrategyResult:
    """Complete strategy analysis result."""
    name: str
    type: StrategyType
    legs: List[StrategyLeg]
    net_premium: float  # Positive = credit, Negative = debit
    max_profit: float
    max_loss: float
    breakeven_points: List[float]
    risk_reward_ratio: float
    probability_of_profit: Optional[float]
    margin_required: float
    lot_size: int
    total_lots: int
    greeks: Dict[str, float]
    payoff_at_expiry: Dict[float, float]  # Price -> P&L
    recommendation: str


class OptionsStrategyEngine:
    """
    Options Strategy Builder and Analyzer.
    
    Features:
    - Build multi-leg strategies
    - Calculate risk/reward
    - Estimate probability of profit
    - Generate payoff diagrams
    - Margin estimation
    """
    
    # Standard lot sizes
    LOT_SIZES = {
        "NIFTY": 50,
        "BANKNIFTY": 15,
        "FINNIFTY": 40,
        "MIDCPNIFTY": 75,
    }
    
    # Margin multipliers (approximate)
    MARGIN_MULTIPLIERS = {
        "NIFTY": 0.10,  # ~10% of notional for options
        "BANKNIFTY": 0.12,
        "FINNIFTY": 0.10,
    }
    
    def __init__(self):
        self.greeks_calc = get_greeks_calculator()
    
    def get_lot_size(self, symbol: str) -> int:
        """Get lot size for symbol."""
        return self.LOT_SIZES.get(symbol.upper(), 50)
    
    def build_long_call(
        self,
        symbol: str,
        strike: float,
        premium: float,
        spot_price: float,
        expiry: str,
        lots: int = 1,
        iv: float = 0.15,
        days_to_expiry: int = 7,
    ) -> StrategyResult:
        """Build Long Call strategy."""
        lot_size = self.get_lot_size(symbol)
        total_qty = lots * lot_size
        
        # Cost = Premium paid
        net_premium = -premium * total_qty
        
        # Max profit = Unlimited
        max_profit = float('inf')
        
        # Max loss = Premium paid
        max_loss = abs(net_premium)
        
        # Breakeven = Strike + Premium
        breakeven = strike + premium
        
        # Calculate Greeks
        greeks = self._calculate_position_greeks(
            spot_price, strike, days_to_expiry, iv, OptionType.CALL, total_qty, is_long=True
        )
        
        # Probability of profit
        pop = self._calculate_pop(spot_price, breakeven, iv, days_to_expiry)
        
        # Payoff at expiry
        payoff = self._generate_payoff(
            spot_price, [(strike, premium, "CE", "BUY", total_qty)]
        )
        
        return StrategyResult(
            name=f"Long {symbol} {strike} CE",
            type=StrategyType.LONG_CALL,
            legs=[StrategyLeg("BUY", "CE", strike, premium, lots, expiry)],
            net_premium=net_premium,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_points=[breakeven],
            risk_reward_ratio=0,  # Unlimited reward
            probability_of_profit=pop,
            margin_required=abs(net_premium),  # Only premium for buying
            lot_size=lot_size,
            total_lots=lots,
            greeks=greeks,
            payoff_at_expiry=payoff,
            recommendation=self._get_recommendation("LONG_CALL", spot_price, strike, iv),
        )
    
    def build_long_put(
        self,
        symbol: str,
        strike: float,
        premium: float,
        spot_price: float,
        expiry: str,
        lots: int = 1,
        iv: float = 0.15,
        days_to_expiry: int = 7,
    ) -> StrategyResult:
        """Build Long Put strategy."""
        lot_size = self.get_lot_size(symbol)
        total_qty = lots * lot_size
        
        net_premium = -premium * total_qty
        max_profit = (strike - premium) * total_qty  # If spot goes to 0
        max_loss = abs(net_premium)
        breakeven = strike - premium
        
        greeks = self._calculate_position_greeks(
            spot_price, strike, days_to_expiry, iv, OptionType.PUT, total_qty, is_long=True
        )
        
        pop = self._calculate_pop(spot_price, breakeven, iv, days_to_expiry, direction="down")
        
        payoff = self._generate_payoff(
            spot_price, [(strike, premium, "PE", "BUY", total_qty)]
        )
        
        return StrategyResult(
            name=f"Long {symbol} {strike} PE",
            type=StrategyType.LONG_PUT,
            legs=[StrategyLeg("BUY", "PE", strike, premium, lots, expiry)],
            net_premium=net_premium,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_points=[breakeven],
            risk_reward_ratio=max_profit / max_loss if max_loss > 0 else 0,
            probability_of_profit=pop,
            margin_required=abs(net_premium),
            lot_size=lot_size,
            total_lots=lots,
            greeks=greeks,
            payoff_at_expiry=payoff,
            recommendation=self._get_recommendation("LONG_PUT", spot_price, strike, iv),
        )
    
    def build_bull_call_spread(
        self,
        symbol: str,
        buy_strike: float,
        sell_strike: float,
        buy_premium: float,
        sell_premium: float,
        spot_price: float,
        expiry: str,
        lots: int = 1,
        iv: float = 0.15,
        days_to_expiry: int = 7,
    ) -> StrategyResult:
        """
        Build Bull Call Spread (Debit Spread).
        Buy lower strike call, Sell higher strike call.
        """
        lot_size = self.get_lot_size(symbol)
        total_qty = lots * lot_size
        
        # Net debit = Buy premium - Sell premium
        net_premium = -(buy_premium - sell_premium) * total_qty
        
        # Max profit = Spread width - Net debit (per share)
        spread_width = sell_strike - buy_strike
        max_profit = (spread_width - (buy_premium - sell_premium)) * total_qty
        
        # Max loss = Net debit
        max_loss = abs(net_premium)
        
        # Breakeven = Lower strike + Net debit
        breakeven = buy_strike + (buy_premium - sell_premium)
        
        # Combined Greeks
        buy_greeks = self._calculate_position_greeks(
            spot_price, buy_strike, days_to_expiry, iv, OptionType.CALL, total_qty, True
        )
        sell_greeks = self._calculate_position_greeks(
            spot_price, sell_strike, days_to_expiry, iv, OptionType.CALL, total_qty, False
        )
        greeks = self._combine_greeks([buy_greeks, sell_greeks])
        
        pop = self._calculate_pop(spot_price, breakeven, iv, days_to_expiry)
        
        payoff = self._generate_payoff(spot_price, [
            (buy_strike, buy_premium, "CE", "BUY", total_qty),
            (sell_strike, sell_premium, "CE", "SELL", total_qty),
        ])
        
        return StrategyResult(
            name=f"Bull Call Spread {symbol} {buy_strike}/{sell_strike}",
            type=StrategyType.BULL_CALL_SPREAD,
            legs=[
                StrategyLeg("BUY", "CE", buy_strike, buy_premium, lots, expiry),
                StrategyLeg("SELL", "CE", sell_strike, sell_premium, lots, expiry),
            ],
            net_premium=net_premium,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_points=[breakeven],
            risk_reward_ratio=max_profit / max_loss if max_loss > 0 else 0,
            probability_of_profit=pop,
            margin_required=abs(net_premium),  # Debit spread - margin = cost
            lot_size=lot_size,
            total_lots=lots,
            greeks=greeks,
            payoff_at_expiry=payoff,
            recommendation=self._get_recommendation("BULL_CALL_SPREAD", spot_price, buy_strike, iv),
        )
    
    def build_bear_put_spread(
        self,
        symbol: str,
        buy_strike: float,
        sell_strike: float,
        buy_premium: float,
        sell_premium: float,
        spot_price: float,
        expiry: str,
        lots: int = 1,
        iv: float = 0.15,
        days_to_expiry: int = 7,
    ) -> StrategyResult:
        """
        Build Bear Put Spread (Debit Spread).
        Buy higher strike put, Sell lower strike put.
        """
        lot_size = self.get_lot_size(symbol)
        total_qty = lots * lot_size
        
        # Net debit = Buy premium - Sell premium
        net_premium = -(buy_premium - sell_premium) * total_qty
        
        # Max profit = Spread width - Net debit
        spread_width = buy_strike - sell_strike
        max_profit = (spread_width - (buy_premium - sell_premium)) * total_qty
        
        # Max loss = Net debit
        max_loss = abs(net_premium)
        
        # Breakeven = Higher strike - Net debit
        breakeven = buy_strike - (buy_premium - sell_premium)
        
        buy_greeks = self._calculate_position_greeks(
            spot_price, buy_strike, days_to_expiry, iv, OptionType.PUT, total_qty, True
        )
        sell_greeks = self._calculate_position_greeks(
            spot_price, sell_strike, days_to_expiry, iv, OptionType.PUT, total_qty, False
        )
        greeks = self._combine_greeks([buy_greeks, sell_greeks])
        
        pop = self._calculate_pop(spot_price, breakeven, iv, days_to_expiry, direction="down")
        
        payoff = self._generate_payoff(spot_price, [
            (buy_strike, buy_premium, "PE", "BUY", total_qty),
            (sell_strike, sell_premium, "PE", "SELL", total_qty),
        ])
        
        return StrategyResult(
            name=f"Bear Put Spread {symbol} {sell_strike}/{buy_strike}",
            type=StrategyType.BEAR_PUT_SPREAD,
            legs=[
                StrategyLeg("BUY", "PE", buy_strike, buy_premium, lots, expiry),
                StrategyLeg("SELL", "PE", sell_strike, sell_premium, lots, expiry),
            ],
            net_premium=net_premium,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_points=[breakeven],
            risk_reward_ratio=max_profit / max_loss if max_loss > 0 else 0,
            probability_of_profit=pop,
            margin_required=abs(net_premium),
            lot_size=lot_size,
            total_lots=lots,
            greeks=greeks,
            payoff_at_expiry=payoff,
            recommendation=self._get_recommendation("BEAR_PUT_SPREAD", spot_price, buy_strike, iv),
        )
    
    def build_long_straddle(
        self,
        symbol: str,
        strike: float,
        call_premium: float,
        put_premium: float,
        spot_price: float,
        expiry: str,
        lots: int = 1,
        iv: float = 0.15,
        days_to_expiry: int = 7,
    ) -> StrategyResult:
        """
        Build Long Straddle.
        Buy ATM Call and ATM Put at same strike.
        """
        lot_size = self.get_lot_size(symbol)
        total_qty = lots * lot_size
        
        total_premium = call_premium + put_premium
        net_premium = -total_premium * total_qty
        
        # Max profit = Unlimited (either direction)
        max_profit = float('inf')
        
        # Max loss = Total premium paid
        max_loss = abs(net_premium)
        
        # Two breakevens
        upper_breakeven = strike + total_premium
        lower_breakeven = strike - total_premium
        
        call_greeks = self._calculate_position_greeks(
            spot_price, strike, days_to_expiry, iv, OptionType.CALL, total_qty, True
        )
        put_greeks = self._calculate_position_greeks(
            spot_price, strike, days_to_expiry, iv, OptionType.PUT, total_qty, True
        )
        greeks = self._combine_greeks([call_greeks, put_greeks])
        
        # POP = move beyond either breakeven
        pop_up = self._calculate_pop(spot_price, upper_breakeven, iv, days_to_expiry)
        pop_down = self._calculate_pop(spot_price, lower_breakeven, iv, days_to_expiry, "down")
        pop = pop_up + pop_down
        
        payoff = self._generate_payoff(spot_price, [
            (strike, call_premium, "CE", "BUY", total_qty),
            (strike, put_premium, "PE", "BUY", total_qty),
        ])
        
        return StrategyResult(
            name=f"Long Straddle {symbol} {strike}",
            type=StrategyType.LONG_STRADDLE,
            legs=[
                StrategyLeg("BUY", "CE", strike, call_premium, lots, expiry),
                StrategyLeg("BUY", "PE", strike, put_premium, lots, expiry),
            ],
            net_premium=net_premium,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_points=[lower_breakeven, upper_breakeven],
            risk_reward_ratio=0,
            probability_of_profit=min(1.0, pop),
            margin_required=abs(net_premium),
            lot_size=lot_size,
            total_lots=lots,
            greeks=greeks,
            payoff_at_expiry=payoff,
            recommendation=self._get_recommendation("LONG_STRADDLE", spot_price, strike, iv),
        )
    
    def build_short_straddle(
        self,
        symbol: str,
        strike: float,
        call_premium: float,
        put_premium: float,
        spot_price: float,
        expiry: str,
        lots: int = 1,
        iv: float = 0.15,
        days_to_expiry: int = 7,
    ) -> StrategyResult:
        """
        Build Short Straddle.
        Sell ATM Call and ATM Put at same strike.
        HIGH RISK - Unlimited loss potential.
        """
        lot_size = self.get_lot_size(symbol)
        total_qty = lots * lot_size
        
        total_premium = call_premium + put_premium
        net_premium = total_premium * total_qty  # Credit
        
        # Max profit = Premium received
        max_profit = net_premium
        
        # Max loss = Unlimited
        max_loss = float('inf')
        
        # Two breakevens
        upper_breakeven = strike + total_premium
        lower_breakeven = strike - total_premium
        
        call_greeks = self._calculate_position_greeks(
            spot_price, strike, days_to_expiry, iv, OptionType.CALL, total_qty, False
        )
        put_greeks = self._calculate_position_greeks(
            spot_price, strike, days_to_expiry, iv, OptionType.PUT, total_qty, False
        )
        greeks = self._combine_greeks([call_greeks, put_greeks])
        
        # POP = stay between breakevens
        pop = 1.0 - self._calculate_pop(spot_price, upper_breakeven, iv, days_to_expiry)
        pop -= self._calculate_pop(spot_price, lower_breakeven, iv, days_to_expiry, "down")
        
        # Margin for naked options
        margin = self._estimate_naked_margin(symbol, spot_price, total_qty)
        
        payoff = self._generate_payoff(spot_price, [
            (strike, call_premium, "CE", "SELL", total_qty),
            (strike, put_premium, "PE", "SELL", total_qty),
        ])
        
        return StrategyResult(
            name=f"Short Straddle {symbol} {strike}",
            type=StrategyType.SHORT_STRADDLE,
            legs=[
                StrategyLeg("SELL", "CE", strike, call_premium, lots, expiry),
                StrategyLeg("SELL", "PE", strike, put_premium, lots, expiry),
            ],
            net_premium=net_premium,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_points=[lower_breakeven, upper_breakeven],
            risk_reward_ratio=0,
            probability_of_profit=max(0, pop),
            margin_required=margin,
            lot_size=lot_size,
            total_lots=lots,
            greeks=greeks,
            payoff_at_expiry=payoff,
            recommendation="⚠️ HIGH RISK: Unlimited loss potential. Use strict stop-loss.",
        )
    
    def build_iron_condor(
        self,
        symbol: str,
        put_buy_strike: float,
        put_sell_strike: float,
        call_sell_strike: float,
        call_buy_strike: float,
        put_buy_premium: float,
        put_sell_premium: float,
        call_sell_premium: float,
        call_buy_premium: float,
        spot_price: float,
        expiry: str,
        lots: int = 1,
        iv: float = 0.15,
        days_to_expiry: int = 7,
    ) -> StrategyResult:
        """
        Build Iron Condor.
        Sell OTM put spread + Sell OTM call spread.
        Defined risk neutral strategy.
        """
        lot_size = self.get_lot_size(symbol)
        total_qty = lots * lot_size
        
        # Net credit
        net_credit = (put_sell_premium - put_buy_premium + call_sell_premium - call_buy_premium)
        net_premium = net_credit * total_qty
        
        # Max profit = Net credit
        max_profit = net_premium
        
        # Max loss = Wider spread width - Net credit
        put_spread_width = put_sell_strike - put_buy_strike
        call_spread_width = call_buy_strike - call_sell_strike
        max_spread_width = max(put_spread_width, call_spread_width)
        max_loss = (max_spread_width - net_credit) * total_qty
        
        # Breakevens
        lower_breakeven = put_sell_strike - net_credit
        upper_breakeven = call_sell_strike + net_credit
        
        greeks = {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}  # Simplified
        
        # POP = stay between inner strikes
        pop = 1.0 - self._calculate_pop(spot_price, call_sell_strike, iv, days_to_expiry)
        pop -= self._calculate_pop(spot_price, put_sell_strike, iv, days_to_expiry, "down")
        
        # Margin = Max of either spread
        margin = max_spread_width * total_qty
        
        payoff = self._generate_payoff(spot_price, [
            (put_buy_strike, put_buy_premium, "PE", "BUY", total_qty),
            (put_sell_strike, put_sell_premium, "PE", "SELL", total_qty),
            (call_sell_strike, call_sell_premium, "CE", "SELL", total_qty),
            (call_buy_strike, call_buy_premium, "CE", "BUY", total_qty),
        ])
        
        return StrategyResult(
            name=f"Iron Condor {symbol} {put_sell_strike}/{call_sell_strike}",
            type=StrategyType.IRON_CONDOR,
            legs=[
                StrategyLeg("BUY", "PE", put_buy_strike, put_buy_premium, lots, expiry),
                StrategyLeg("SELL", "PE", put_sell_strike, put_sell_premium, lots, expiry),
                StrategyLeg("SELL", "CE", call_sell_strike, call_sell_premium, lots, expiry),
                StrategyLeg("BUY", "CE", call_buy_strike, call_buy_premium, lots, expiry),
            ],
            net_premium=net_premium,
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven_points=[lower_breakeven, upper_breakeven],
            risk_reward_ratio=max_profit / max_loss if max_loss > 0 else 0,
            probability_of_profit=max(0, pop),
            margin_required=margin,
            lot_size=lot_size,
            total_lots=lots,
            greeks=greeks,
            payoff_at_expiry=payoff,
            recommendation=self._get_recommendation("IRON_CONDOR", spot_price, (put_sell_strike + call_sell_strike) / 2, iv),
        )
    
    def _calculate_position_greeks(
        self,
        spot: float,
        strike: float,
        days_to_expiry: int,
        iv: float,
        option_type: OptionType,
        quantity: int,
        is_long: bool,
    ) -> Dict[str, float]:
        """Calculate position Greeks for a leg."""
        time_to_expiry = days_to_expiry / 365.0
        
        greeks = self.greeks_calc.calculate_greeks(
            spot=spot,
            strike=strike,
            time_to_expiry=time_to_expiry,
            volatility=iv,
            option_type=option_type,
        )
        
        multiplier = quantity if is_long else -quantity
        
        return {
            "delta": greeks.delta * multiplier,
            "gamma": greeks.gamma * multiplier,
            "theta": greeks.theta * multiplier,
            "vega": greeks.vega * multiplier,
        }
    
    def _combine_greeks(self, greeks_list: List[Dict]) -> Dict[str, float]:
        """Combine Greeks from multiple legs."""
        combined = {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}
        for g in greeks_list:
            combined["delta"] += g.get("delta", 0)
            combined["gamma"] += g.get("gamma", 0)
            combined["theta"] += g.get("theta", 0)
            combined["vega"] += g.get("vega", 0)
        return combined
    
    def _calculate_pop(
        self,
        spot: float,
        target: float,
        iv: float,
        days: int,
        direction: str = "up",
    ) -> float:
        """
        Calculate probability of profit using Black-Scholes.
        
        Probability that spot reaches target by expiry.
        """
        if days <= 0:
            if direction == "up":
                return 1.0 if spot >= target else 0.0
            else:
                return 1.0 if spot <= target else 0.0
        
        time_to_expiry = days / 365.0
        
        # Using d2 from Black-Scholes for probability
        r = 0.065  # Risk-free rate
        d2 = (math.log(spot / target) + (r - 0.5 * iv ** 2) * time_to_expiry) / (iv * math.sqrt(time_to_expiry))
        
        if direction == "up":
            return norm.cdf(d2)
        else:
            return norm.cdf(-d2)
    
    def _generate_payoff(
        self,
        spot: float,
        legs: List[Tuple],  # [(strike, premium, type, action, qty), ...]
    ) -> Dict[float, float]:
        """Generate payoff at various price points."""
        payoff = {}
        
        # Generate price points around spot
        price_range = spot * 0.15  # 15% range
        for price in range(int(spot - price_range), int(spot + price_range), int(spot * 0.01)):
            total_pnl = 0
            
            for strike, premium, opt_type, action, qty in legs:
                if opt_type == "CE":
                    intrinsic = max(0, price - strike)
                else:
                    intrinsic = max(0, strike - price)
                
                if action == "BUY":
                    pnl = (intrinsic - premium) * qty
                else:
                    pnl = (premium - intrinsic) * qty
                
                total_pnl += pnl
            
            payoff[float(price)] = round(total_pnl, 2)
        
        return payoff
    
    def _estimate_naked_margin(self, symbol: str, spot: float, quantity: int) -> float:
        """Estimate margin for naked option positions."""
        multiplier = self.MARGIN_MULTIPLIERS.get(symbol.upper(), 0.12)
        return spot * quantity * multiplier
    
    def _get_recommendation(self, strategy: str, spot: float, strike: float, iv: float) -> str:
        """Generate strategy-specific recommendation."""
        recommendations = {
            "LONG_CALL": f"Bullish strategy. Best when expecting move above {strike + spot * 0.02:.0f}",
            "LONG_PUT": f"Bearish strategy. Best when expecting move below {strike - spot * 0.02:.0f}",
            "BULL_CALL_SPREAD": "Moderately bullish. Limited risk & reward. Good for range-bound upside.",
            "BEAR_PUT_SPREAD": "Moderately bearish. Limited risk & reward. Good for range-bound downside.",
            "LONG_STRADDLE": f"Volatility play. Need big move (>{spot * 0.03:.0f} pts) to profit.",
            "SHORT_STRADDLE": "⚠️ HIGH RISK. Unlimited loss. Only for experienced traders.",
            "IRON_CONDOR": f"Neutral strategy. Profit if spot stays in range. IV: {iv*100:.1f}%",
        }
        return recommendations.get(strategy, "Review risk before trading")


# Singleton
_strategy_engine = None

def get_strategy_engine() -> OptionsStrategyEngine:
    """Get or create strategy engine singleton."""
    global _strategy_engine
    if _strategy_engine is None:
        _strategy_engine = OptionsStrategyEngine()
    return _strategy_engine
