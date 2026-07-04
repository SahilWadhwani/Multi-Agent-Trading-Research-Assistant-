"""
Trader Agent
Converts analyst reports into concrete trade proposals.
Decides what to trade, how much, and why.
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database.operations import get_current_portfolio_value, get_today_pnl


@dataclass
class TradeProposal:
    """Structured trade proposal."""
    symbol: str
    exchange: str
    action: str  # BUY, SELL, HOLD
    quantity: int
    price_estimate: float
    product_type: str  # INTRADAY, DELIVERY
    order_type: str  # MARKET, LIMIT
    limit_price: Optional[float]
    stop_loss: Optional[float]
    target: Optional[float]
    reasoning: str
    confidence: float
    risk_reward_ratio: Optional[float]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "action": self.action,
            "quantity": self.quantity,
            "price_estimate": self.price_estimate,
            "product_type": self.product_type,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "stop_loss": self.stop_loss,
            "target": self.target,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "risk_reward_ratio": self.risk_reward_ratio,
        }


class TraderAgent:
    """
    Trader Agent - The Decision Maker.
    
    Takes analyst reports and converts them into actionable trade proposals.
    
    Responsibilities:
    - Interpret analyst signals and reports
    - Determine trade direction (BUY/SELL/HOLD)
    - Calculate position size based on risk parameters
    - Set stop-loss and target prices
    - Generate reasoning for each trade decision
    """
    
    def __init__(self, default_risk_per_trade: float = 0.02):
        """
        Args:
            default_risk_per_trade: Default risk per trade as fraction of capital (2%)
        """
        self.default_risk_per_trade = default_risk_per_trade
    
    def _is_good_trading_time(self) -> tuple:
        """
        Check if current time is good for new trades.
        
        PROFIT RULES:
        - Avoid first 15 min (9:15-9:30) - high volatility
        - Avoid last 30 min (3:00-3:30) - closing volatility
        - Best time: 9:45 AM - 2:30 PM IST
        
        Returns:
            (is_good_time, reason)
        """
        import pytz
        from datetime import datetime
        
        try:
            ist = pytz.timezone('Asia/Kolkata')
            now = datetime.now(ist)
            current_time = now.time()
            
            market_open = datetime.strptime("09:15", "%H:%M").time()
            avoid_open = datetime.strptime("09:30", "%H:%M").time()
            avoid_close = datetime.strptime("15:00", "%H:%M").time()
            market_close = datetime.strptime("15:30", "%H:%M").time()
            
            # Before market
            if current_time < market_open:
                return False, "Market not open yet"
            
            # First 15 minutes - avoid
            if market_open <= current_time < avoid_open:
                return False, "Avoiding first 15 min volatility"
            
            # Last 30 minutes - avoid for new INTRADAY positions
            if avoid_close <= current_time <= market_close:
                return False, "Avoiding last 30 min - closing volatility"
            
            # After market
            if current_time > market_close:
                return False, "Market closed"
            
            # Good trading window
            return True, "Good trading window"
            
        except Exception:
            # If timezone check fails, allow trading (paper mode safety)
            return True, "Time check skipped"
    
    def generate_proposal(
        self,
        technical_report: Dict[str, Any],
        available_capital: float,
        existing_positions: List[Dict] = None,
        product_type: str = "INTRADAY"
    ) -> TradeProposal:
        """
        Generate a trade proposal based on analyst reports.
        
        Args:
            technical_report: Report from TechnicalAnalyst
            available_capital: Available trading capital
            existing_positions: Current holdings
            product_type: INTRADAY or DELIVERY
        
        Returns:
            TradeProposal with all trade details
        """
        existing_positions = existing_positions or []
        
        symbol = technical_report.get("symbol", "UNKNOWN")
        exchange = technical_report.get("exchange", "NSE")
        quote = technical_report.get("quote", {})
        current_price = quote.get("ltp") or technical_report.get("indicators", {}).get("current_price", 0)
        
        bias = technical_report.get("bias", "NEUTRAL")
        confidence = technical_report.get("confidence", 0.0)
        signals = technical_report.get("signals", [])
        sr = technical_report.get("support_resistance", {})
        
        # PROFIT RULE: Check if it's a good time to trade (for INTRADAY)
        is_good_time, time_reason = self._is_good_trading_time()
        
        # Check existing position
        has_position = any(p.get("symbol") == symbol for p in existing_positions)
        position_qty = sum(p.get("quantity", 0) for p in existing_positions if p.get("symbol") == symbol)
        
        # Determine action
        action = self._determine_action(bias, confidence, has_position, position_qty)
        
        # PROFIT RULE: For INTRADAY, avoid new entries during volatile times
        if product_type == "INTRADAY" and action in ["BUY", "SELL"] and not has_position:
            if not is_good_time:
                action = "HOLD"
                # We'll include the reason in reasoning_parts later
        
        # Calculate quantity based on risk management
        quantity, reasoning_parts = self._calculate_quantity(
            action=action,
            current_price=current_price,
            available_capital=available_capital,
            confidence=confidence,
            support_resistance=sr,
            existing_qty=position_qty,
        )
        
        # Determine stop-loss and target
        stop_loss, target, rr_ratio = self._calculate_levels(
            action=action,
            current_price=current_price,
            support_resistance=sr,
            product_type=product_type,
        )
        
        # Build reasoning
        reasoning = self._build_reasoning(
            symbol=symbol,
            action=action,
            technical_report=technical_report,
            reasoning_parts=reasoning_parts,
        )
        
        return TradeProposal(
            symbol=symbol,
            exchange=exchange,
            action=action,
            quantity=quantity,
            price_estimate=current_price,
            product_type=product_type,
            order_type="MARKET" if product_type == "INTRADAY" else "LIMIT",
            limit_price=current_price if product_type == "DELIVERY" else None,
            stop_loss=stop_loss,
            target=target,
            reasoning=reasoning,
            confidence=confidence,
            risk_reward_ratio=rr_ratio,
        )
    
    def _determine_action(
        self,
        bias: str,
        confidence: float,
        has_position: bool,
        position_qty: int
    ) -> str:
        """
        Determine trade action based on bias and position.
        
        PROFITABILITY RULES:
        1. Minimum 60% confidence for new positions
        2. Minimum 75% confidence to add to positions
        3. Always close losing positions on reversal signals
        """
        
        # PROFIT RULE: Higher confidence threshold for new entries
        min_confidence_new = 0.60  # 60% for new positions
        min_confidence_add = 0.75  # 75% to add to winners
        
        if confidence < min_confidence_new:
            return "HOLD"
        
        if bias == "BULLISH":
            if has_position and position_qty > 0:
                # Already long - only add on very high confidence
                if confidence >= min_confidence_add:
                    return "BUY"  # Add to winning position
                return "HOLD"  # Don't add, let it ride
            elif has_position and position_qty < 0:
                # Short position - close it (cut losses)
                return "BUY"  # Cover short immediately
            else:
                return "BUY"  # New long position
        
        elif bias == "BEARISH":
            if has_position and position_qty > 0:
                return "SELL"  # Close long (cut losses on reversal)
            elif has_position and position_qty < 0:
                if confidence >= min_confidence_add:
                    return "SELL"  # Add to short
                return "HOLD"
            else:
                return "SELL"  # Open short (if allowed)
        
        return "HOLD"
    
    def _calculate_quantity(
        self,
        action: str,
        current_price: float,
        available_capital: float,
        confidence: float,
        support_resistance: Dict,
        existing_qty: int,
    ) -> tuple:
        """
        Calculate position size using risk-based sizing.
        Returns (quantity, reasoning_parts).
        """
        reasoning_parts = []
        
        if action == "HOLD" or current_price <= 0:
            return 0, ["No trade - HOLD signal"]
        
        # Risk-adjusted position sizing
        # Higher confidence = larger position (up to limits)
        risk_multiplier = 0.5 + (confidence * 0.5)  # 0.5 to 1.0 based on confidence
        
        # Calculate max position value (5% of capital per trade max)
        max_position_percent = 0.05 * risk_multiplier
        max_position_value = available_capital * max_position_percent
        
        # Calculate quantity
        quantity = int(max_position_value / current_price)
        
        # Ensure minimum viable quantity
        quantity = max(1, quantity)
        
        # Position value
        position_value = quantity * current_price
        
        reasoning_parts.append(f"Available capital: ₹{available_capital:,.2f}")
        reasoning_parts.append(f"Risk multiplier (confidence-based): {risk_multiplier:.2f}")
        reasoning_parts.append(f"Max position: {max_position_percent:.1%} of capital = ₹{max_position_value:,.2f}")
        reasoning_parts.append(f"Calculated quantity: {quantity} @ ₹{current_price:.2f} = ₹{position_value:,.2f}")
        
        return quantity, reasoning_parts
    
    def _calculate_levels(
        self,
        action: str,
        current_price: float,
        support_resistance: Dict,
        product_type: str,
    ) -> tuple:
        """Calculate stop-loss and target levels."""
        
        if action == "HOLD" or current_price <= 0:
            return None, None, None
        
        # Get support/resistance levels
        nearest_support = support_resistance.get("nearest_support", {})
        nearest_resistance = support_resistance.get("nearest_resistance", {})
        
        support_level = nearest_support.get("level") if nearest_support else None
        resistance_level = nearest_resistance.get("level") if nearest_resistance else None
        
        if action == "BUY":
            # Stop loss below support or 2% below entry
            if support_level and support_level < current_price:
                stop_loss = support_level * 0.99  # Slightly below support
            else:
                stop_loss = current_price * 0.98  # 2% stop loss
            
            # Target at resistance or 4% above entry
            if resistance_level and resistance_level > current_price:
                target = resistance_level * 0.99  # Slightly below resistance
            else:
                target = current_price * 1.04  # 4% target
            
            # Intraday: tighter levels
            if product_type == "INTRADAY":
                stop_loss = max(stop_loss, current_price * 0.99)  # Max 1% SL
                target = min(target, current_price * 1.02)  # Max 2% target
        
        else:  # SELL
            # Stop loss above resistance or 2% above entry
            if resistance_level and resistance_level > current_price:
                stop_loss = resistance_level * 1.01
            else:
                stop_loss = current_price * 1.02
            
            # Target at support or 4% below entry
            if support_level and support_level < current_price:
                target = support_level * 1.01
            else:
                target = current_price * 0.96
            
            if product_type == "INTRADAY":
                stop_loss = min(stop_loss, current_price * 1.01)
                target = max(target, current_price * 0.98)
        
        # Calculate risk-reward ratio
        risk = abs(current_price - stop_loss)
        reward = abs(target - current_price)
        rr_ratio = reward / risk if risk > 0 else 0
        
        # PROFIT RULE: Ensure minimum 1.5 R:R ratio
        min_rr_ratio = 1.5
        if rr_ratio < min_rr_ratio and reward > 0:
            # Adjust target to achieve minimum R:R
            required_reward = risk * min_rr_ratio
            if action == "BUY":
                target = current_price + required_reward
            else:
                target = current_price - required_reward
            rr_ratio = min_rr_ratio
        
        return round(stop_loss, 2), round(target, 2), round(rr_ratio, 2)
    
    def _build_reasoning(
        self,
        symbol: str,
        action: str,
        technical_report: Dict,
        reasoning_parts: List[str],
    ) -> str:
        """Build comprehensive reasoning string."""
        
        parts = [
            f"=== Trade Proposal: {action} {symbol} ===",
            "",
            "TECHNICAL ANALYSIS:",
            technical_report.get("summary", "No summary available"),
            "",
            "SIGNAL BREAKDOWN:",
        ]
        
        for signal in technical_report.get("signals", []):
            parts.append(f"  - {signal.get('indicator')}: {signal.get('action')} ({signal.get('strength')})")
        
        parts.append("")
        parts.append("POSITION SIZING:")
        parts.extend([f"  - {r}" for r in reasoning_parts])
        
        parts.append("")
        parts.append(f"CONFIDENCE: {technical_report.get('confidence', 0):.0%}")
        parts.append(f"BIAS: {technical_report.get('bias', 'NEUTRAL')}")
        
        return "\n".join(parts)
    
    def evaluate_existing_position(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        quantity: int,
        technical_report: Dict,
    ) -> Dict[str, Any]:
        """
        Evaluate whether to hold, add, or exit an existing position.
        """
        pnl_percent = ((current_price - entry_price) / entry_price) * 100
        bias = technical_report.get("bias", "NEUTRAL")
        confidence = technical_report.get("confidence", 0)
        
        recommendation = {
            "symbol": symbol,
            "current_pnl_percent": round(pnl_percent, 2),
            "position_value": round(quantity * current_price, 2),
            "action": "HOLD",
            "reasoning": "",
        }
        
        # Exit rules
        if pnl_percent < -3:  # 3% loss
            recommendation["action"] = "EXIT"
            recommendation["reasoning"] = f"Stop loss triggered: {pnl_percent:.1f}% loss"
        elif pnl_percent > 5:  # 5% profit
            if bias == "BEARISH" or confidence < 0.5:
                recommendation["action"] = "EXIT"
                recommendation["reasoning"] = f"Target reached: {pnl_percent:.1f}% profit, signals turning negative"
            else:
                recommendation["action"] = "TRAIL_STOP"
                recommendation["reasoning"] = f"Trailing stop: {pnl_percent:.1f}% profit, maintain with trailing SL"
        elif bias == "BEARISH" and confidence > 0.6:
            recommendation["action"] = "EXIT"
            recommendation["reasoning"] = f"Technical reversal: Strong bearish signal with {confidence:.0%} confidence"
        else:
            recommendation["action"] = "HOLD"
            recommendation["reasoning"] = f"Continue holding: P&L {pnl_percent:.1f}%, bias {bias}"
        
        return recommendation


# Convenience function
def create_trade_proposal(
    technical_report: Dict,
    available_capital: float,
    product_type: str = "INTRADAY"
) -> Dict[str, Any]:
    """Quick trade proposal generation."""
    trader = TraderAgent()
    proposal = trader.generate_proposal(
        technical_report=technical_report,
        available_capital=available_capital,
        product_type=product_type,
    )
    return proposal.to_dict()
