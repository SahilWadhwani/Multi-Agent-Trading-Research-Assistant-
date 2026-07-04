"""
F&O Trader Agent (LLM-Powered)

Handles:
- F&O position sizing based on capital and risk
- Strategy selection based on F&O analyst report
- Margin calculation for derivatives
- Order generation for multi-leg strategies
- Risk management for F&O positions

Uses LLM for intelligent strategy selection and position sizing.
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from data_feeds.fo_data_feed import get_fo_data_feed
from strategies.options_strategies import get_strategy_engine, StrategyResult
from llm.client import get_llm_client


@dataclass
class FOTradeProposal:
    """Trade proposal for F&O position."""
    symbol: str
    strategy_name: str
    strategy_type: str
    legs: List[Dict]
    total_lots: int
    lot_size: int
    net_premium: float  # Positive = credit, Negative = debit
    max_profit: float
    max_loss: float
    margin_required: float
    breakeven_points: List[float]
    risk_reward_ratio: float
    probability_of_profit: Optional[float]
    action: str  # EXECUTE, HOLD, AVOID
    confidence: float
    reasoning: str
    greeks: Dict[str, float]
    expiry: str


class FOTrader:
    """
    F&O Trader Agent - Generates derivative trade proposals.
    
    Responsibilities:
    - Convert F&O analysis into actionable trades
    - Size positions based on capital and risk tolerance
    - Select optimal strategies based on market view
    - Calculate margin requirements
    - Generate multi-leg orders
    """
    
    # Risk parameters
    MAX_POSITION_PERCENT = 0.20  # Max 20% of capital per position
    MAX_LOSS_PER_TRADE_PERCENT = 0.02  # Max 2% loss per trade
    MIN_RISK_REWARD = 1.5  # Minimum risk-reward ratio
    
    def __init__(self):
        self.fo_feed = get_fo_data_feed()
        self.strategy_engine = get_strategy_engine()
        self.llm = get_llm_client()
        self._llm_available = self.llm.is_available()
        
        if self._llm_available:
            print(f"   📈 F&O Trader: LLM-powered ({self.llm.model})")
        else:
            print("   📈 F&O Trader: Rule-based (install Proxima/Ollama for AI trading)")
    
    def generate_proposal(
        self,
        fo_analysis: Dict[str, Any],
        available_capital: float,
        existing_positions: List[Dict] = None,
        risk_appetite: str = "MODERATE",  # CONSERVATIVE, MODERATE, AGGRESSIVE
    ) -> FOTradeProposal:
        """
        Generate F&O trade proposal based on analysis.
        
        Args:
            fo_analysis: Report from F&O Analyst
            available_capital: Available trading capital
            existing_positions: Current F&O positions
            risk_appetite: Risk tolerance level
        
        Returns:
            FOTradeProposal with complete trade details
        """
        existing_positions = existing_positions or []
        
        symbol = fo_analysis.get("symbol", "UNKNOWN")
        spot_price = fo_analysis.get("spot_price", 0)
        expiry = fo_analysis.get("expiry", "")
        days_to_expiry = fo_analysis.get("days_to_expiry", 7)
        atm_strike = fo_analysis.get("atm_strike", spot_price)
        lot_size = fo_analysis.get("lot_size", 50)
        bias = fo_analysis.get("bias", "NEUTRAL")
        confidence = fo_analysis.get("confidence", 0.5)
        
        iv_analysis = fo_analysis.get("iv_analysis", {})
        iv_level = iv_analysis.get("iv_level", "MODERATE")
        atm_iv = iv_analysis.get("atm_iv", 15) / 100  # Convert to decimal
        
        strategies = fo_analysis.get("strategy_suggestions", [])
        
        # Determine max risk and lots
        max_risk_amount = available_capital * self.MAX_LOSS_PER_TRADE_PERCENT
        max_position_value = available_capital * self.MAX_POSITION_PERCENT
        
        # Adjust for risk appetite
        risk_multiplier = {"CONSERVATIVE": 0.5, "MODERATE": 1.0, "AGGRESSIVE": 1.5}
        max_lots = self._calculate_max_lots(
            max_risk_amount * risk_multiplier.get(risk_appetite, 1.0),
            spot_price,
            lot_size,
        )
        
        # Select strategy based on bias and IV
        selected_strategy = self._select_strategy(
            bias=bias,
            iv_level=iv_level,
            days_to_expiry=days_to_expiry,
            confidence=confidence,
            suggested_strategies=strategies,
        )
        
        # Fetch option chain for pricing
        chain = self.fo_feed.get_option_chain(symbol, expiry)
        if "error" in chain:
            return self._create_hold_proposal(symbol, expiry, f"Could not fetch chain: {chain['error']}")
        
        # Build the selected strategy
        strategy_result = self._build_strategy(
            symbol=symbol,
            strategy=selected_strategy,
            chain=chain,
            spot_price=spot_price,
            atm_strike=atm_strike,
            atm_iv=atm_iv,
            days_to_expiry=days_to_expiry,
            max_lots=max_lots,
            expiry=expiry,
        )
        
        if not strategy_result:
            return self._create_hold_proposal(symbol, expiry, "Could not build strategy")
        
        # Validate against guardrails
        action, reasoning = self._validate_proposal(
            strategy_result=strategy_result,
            available_capital=available_capital,
            existing_positions=existing_positions,
            confidence=confidence,
        )
        
        # Use LLM for final decision if available
        if self._llm_available and action == "EXECUTE":
            llm_decision = self._get_llm_decision(fo_analysis, strategy_result, reasoning)
            if llm_decision:
                if "AVOID" in llm_decision.upper() or "DON'T" in llm_decision.upper():
                    action = "AVOID"
                reasoning = f"{reasoning}\n\nLLM Analysis: {llm_decision}"
        
        return FOTradeProposal(
            symbol=symbol,
            strategy_name=strategy_result.name,
            strategy_type=strategy_result.type.value,
            legs=[{
                "action": leg.action,
                "option_type": leg.option_type,
                "strike": leg.strike,
                "premium": leg.premium,
                "quantity_lots": leg.quantity,
            } for leg in strategy_result.legs],
            total_lots=strategy_result.total_lots,
            lot_size=strategy_result.lot_size,
            net_premium=strategy_result.net_premium,
            max_profit=strategy_result.max_profit,
            max_loss=strategy_result.max_loss,
            margin_required=strategy_result.margin_required,
            breakeven_points=strategy_result.breakeven_points,
            risk_reward_ratio=strategy_result.risk_reward_ratio,
            probability_of_profit=strategy_result.probability_of_profit,
            action=action,
            confidence=confidence,
            reasoning=reasoning,
            greeks=strategy_result.greeks,
            expiry=expiry,
        )
    
    def _calculate_max_lots(self, max_risk: float, spot: float, lot_size: int) -> int:
        """Calculate maximum lots based on risk."""
        # Assume max loss per lot is ~3% of spot value (conservative)
        estimated_loss_per_lot = spot * lot_size * 0.03
        max_lots = int(max_risk / estimated_loss_per_lot)
        return max(1, min(max_lots, 10))  # Between 1 and 10 lots
    
    def _select_strategy(
        self,
        bias: str,
        iv_level: str,
        days_to_expiry: int,
        confidence: float,
        suggested_strategies: List[Dict],
    ) -> str:
        """Select optimal strategy based on conditions."""
        # High confidence + clear bias = directional strategies
        if confidence >= 0.7:
            if bias == "BULLISH":
                if iv_level == "HIGH":
                    return "BULL_PUT_SPREAD"  # Sell premium
                else:
                    return "LONG_CALL" if days_to_expiry > 5 else "BULL_CALL_SPREAD"
            elif bias == "BEARISH":
                if iv_level == "HIGH":
                    return "BEAR_CALL_SPREAD"  # Sell premium
                else:
                    return "LONG_PUT" if days_to_expiry > 5 else "BEAR_PUT_SPREAD"
        
        # Moderate confidence = spreads
        if confidence >= 0.55:
            if bias == "BULLISH":
                return "BULL_CALL_SPREAD"
            elif bias == "BEARISH":
                return "BEAR_PUT_SPREAD"
        
        # Low confidence / Neutral = volatility strategies
        if iv_level == "HIGH":
            return "IRON_CONDOR"  # Sell volatility
        else:
            if days_to_expiry > 7:
                return "LONG_STRADDLE"  # Buy volatility
            else:
                return "IRON_CONDOR"  # Theta decay helps
        
        return "HOLD"
    
    def _build_strategy(
        self,
        symbol: str,
        strategy: str,
        chain: Dict,
        spot_price: float,
        atm_strike: float,
        atm_iv: float,
        days_to_expiry: int,
        max_lots: int,
        expiry: str,
    ) -> Optional[StrategyResult]:
        """Build the selected strategy with real prices."""
        calls = {c["strike"]: c for c in chain.get("calls", [])}
        puts = {p["strike"]: p for p in chain.get("puts", [])}
        
        strike_interval = chain.get("strike_interval", 50)
        
        try:
            if strategy == "LONG_CALL":
                atm_call = calls.get(atm_strike)
                if not atm_call:
                    return None
                return self.strategy_engine.build_long_call(
                    symbol, atm_strike, atm_call["ltp"], spot_price, expiry,
                    max_lots, atm_iv, days_to_expiry
                )
            
            elif strategy == "LONG_PUT":
                atm_put = puts.get(atm_strike)
                if not atm_put:
                    return None
                return self.strategy_engine.build_long_put(
                    symbol, atm_strike, atm_put["ltp"], spot_price, expiry,
                    max_lots, atm_iv, days_to_expiry
                )
            
            elif strategy == "BULL_CALL_SPREAD":
                buy_strike = atm_strike
                sell_strike = atm_strike + strike_interval
                buy_call = calls.get(buy_strike)
                sell_call = calls.get(sell_strike)
                if not buy_call or not sell_call:
                    return None
                return self.strategy_engine.build_bull_call_spread(
                    symbol, buy_strike, sell_strike,
                    buy_call["ltp"], sell_call["ltp"],
                    spot_price, expiry, max_lots, atm_iv, days_to_expiry
                )
            
            elif strategy == "BEAR_PUT_SPREAD":
                buy_strike = atm_strike
                sell_strike = atm_strike - strike_interval
                buy_put = puts.get(buy_strike)
                sell_put = puts.get(sell_strike)
                if not buy_put or not sell_put:
                    return None
                return self.strategy_engine.build_bear_put_spread(
                    symbol, buy_strike, sell_strike,
                    buy_put["ltp"], sell_put["ltp"],
                    spot_price, expiry, max_lots, atm_iv, days_to_expiry
                )
            
            elif strategy == "BULL_PUT_SPREAD":
                sell_strike = atm_strike
                buy_strike = atm_strike - strike_interval
                sell_put = puts.get(sell_strike)
                buy_put = puts.get(buy_strike)
                if not sell_put or not buy_put:
                    return None
                # Bull put spread is opposite of bear put spread
                return self.strategy_engine.build_bear_put_spread(
                    symbol, sell_strike, buy_strike,
                    sell_put["ltp"], buy_put["ltp"],
                    spot_price, expiry, max_lots, atm_iv, days_to_expiry
                )
            
            elif strategy == "BEAR_CALL_SPREAD":
                sell_strike = atm_strike
                buy_strike = atm_strike + strike_interval
                sell_call = calls.get(sell_strike)
                buy_call = calls.get(buy_strike)
                if not sell_call or not buy_call:
                    return None
                return self.strategy_engine.build_bull_call_spread(
                    symbol, sell_strike, buy_strike,
                    sell_call["ltp"], buy_call["ltp"],
                    spot_price, expiry, max_lots, atm_iv, days_to_expiry
                )
            
            elif strategy == "LONG_STRADDLE":
                atm_call = calls.get(atm_strike)
                atm_put = puts.get(atm_strike)
                if not atm_call or not atm_put:
                    return None
                return self.strategy_engine.build_long_straddle(
                    symbol, atm_strike, atm_call["ltp"], atm_put["ltp"],
                    spot_price, expiry, max_lots, atm_iv, days_to_expiry
                )
            
            elif strategy == "SHORT_STRADDLE":
                atm_call = calls.get(atm_strike)
                atm_put = puts.get(atm_strike)
                if not atm_call or not atm_put:
                    return None
                return self.strategy_engine.build_short_straddle(
                    symbol, atm_strike, atm_call["ltp"], atm_put["ltp"],
                    spot_price, expiry, max_lots, atm_iv, days_to_expiry
                )
            
            elif strategy == "IRON_CONDOR":
                # Build iron condor with 1 strike interval wings
                put_sell = atm_strike - strike_interval
                put_buy = atm_strike - 2 * strike_interval
                call_sell = atm_strike + strike_interval
                call_buy = atm_strike + 2 * strike_interval
                
                put_sell_opt = puts.get(put_sell)
                put_buy_opt = puts.get(put_buy)
                call_sell_opt = calls.get(call_sell)
                call_buy_opt = calls.get(call_buy)
                
                if not all([put_sell_opt, put_buy_opt, call_sell_opt, call_buy_opt]):
                    return None
                
                return self.strategy_engine.build_iron_condor(
                    symbol,
                    put_buy, put_sell, call_sell, call_buy,
                    put_buy_opt["ltp"], put_sell_opt["ltp"],
                    call_sell_opt["ltp"], call_buy_opt["ltp"],
                    spot_price, expiry, max_lots, atm_iv, days_to_expiry
                )
            
        except Exception as e:
            print(f"   ⚠️ Strategy build error: {e}")
            return None
        
        return None
    
    def _validate_proposal(
        self,
        strategy_result: StrategyResult,
        available_capital: float,
        existing_positions: List[Dict],
        confidence: float,
    ) -> tuple:
        """Validate proposal against guardrails."""
        reasons = []
        
        # Check margin requirement
        if strategy_result.margin_required > available_capital * self.MAX_POSITION_PERCENT:
            return "AVOID", f"Margin ({strategy_result.margin_required:.0f}) exceeds limit ({available_capital * self.MAX_POSITION_PERCENT:.0f})"
        
        # Check max loss
        if strategy_result.max_loss != float('inf'):
            if strategy_result.max_loss > available_capital * self.MAX_LOSS_PER_TRADE_PERCENT * 2:
                return "AVOID", f"Max loss ({strategy_result.max_loss:.0f}) too high"
        else:
            # Unlimited loss strategies need extra caution
            if confidence < 0.75:
                return "AVOID", "Unlimited loss strategy requires high confidence (>75%)"
        
        # Check risk-reward
        if strategy_result.risk_reward_ratio > 0 and strategy_result.risk_reward_ratio < 1.0:
            reasons.append(f"Low R:R ({strategy_result.risk_reward_ratio:.2f})")
        
        # Check probability of profit
        if strategy_result.probability_of_profit and strategy_result.probability_of_profit < 0.35:
            reasons.append(f"Low POP ({strategy_result.probability_of_profit*100:.0f}%)")
        
        # Check confidence
        if confidence < 0.5:
            reasons.append(f"Low confidence ({confidence*100:.0f}%)")
        
        if reasons:
            return "EXECUTE", f"Proceed with caution: {', '.join(reasons)}"
        
        return "EXECUTE", f"Strategy validated. Margin: ₹{strategy_result.margin_required:.0f}, Max Loss: ₹{strategy_result.max_loss:.0f}"
    
    def _create_hold_proposal(self, symbol: str, expiry: str, reason: str) -> FOTradeProposal:
        """Create a HOLD proposal."""
        return FOTradeProposal(
            symbol=symbol,
            strategy_name="HOLD",
            strategy_type="hold",
            legs=[],
            total_lots=0,
            lot_size=50,
            net_premium=0,
            max_profit=0,
            max_loss=0,
            margin_required=0,
            breakeven_points=[],
            risk_reward_ratio=0,
            probability_of_profit=None,
            action="HOLD",
            confidence=0,
            reasoning=reason,
            greeks={},
            expiry=expiry,
        )
    
    def _get_llm_decision(
        self,
        fo_analysis: Dict,
        strategy_result: StrategyResult,
        initial_reasoning: str,
    ) -> Optional[str]:
        """Get LLM opinion on the trade."""
        try:
            prompt = f"""Review this F&O trade proposal:

Symbol: {fo_analysis.get('symbol')}
Spot: {fo_analysis.get('spot_price')}
Strategy: {strategy_result.name}
Bias: {fo_analysis.get('bias')} (Confidence: {fo_analysis.get('confidence', 0)*100:.0f}%)

Position Details:
- Lots: {strategy_result.total_lots}
- Net Premium: ₹{strategy_result.net_premium:,.0f}
- Max Profit: ₹{strategy_result.max_profit:,.0f}
- Max Loss: ₹{strategy_result.max_loss:,.0f}
- R:R Ratio: {strategy_result.risk_reward_ratio:.2f}
- POP: {strategy_result.probability_of_profit*100:.0f}% if strategy_result.probability_of_profit else 'N/A'

Market Context:
- PCR: {fo_analysis.get('oi_analysis', {}).get('pcr_oi', 'N/A')}
- IV Level: {fo_analysis.get('iv_analysis', {}).get('iv_level', 'N/A')}
- Max Pain: {fo_analysis.get('support_resistance', {}).get('max_pain', 'N/A')}

Initial Assessment: {initial_reasoning}

Should we EXECUTE or AVOID this trade? Give a brief 1-2 sentence reasoning."""

            response = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                task_type="trade_decision",
                max_tokens=150,
            )
            
            return response.content.strip()
            
        except Exception as e:
            print(f"   ⚠️ LLM decision failed: {e}")
            return None
    
    def generate_orders(self, proposal: FOTradeProposal) -> List[Dict]:
        """
        Generate Upstox order parameters from proposal.
        
        Returns list of orders to be placed.
        """
        if proposal.action != "EXECUTE" or not proposal.legs:
            return []
        
        orders = []
        lot_size = proposal.lot_size
        
        for leg in proposal.legs:
            order = {
                "symbol": proposal.symbol,
                "expiry": proposal.expiry,
                "strike": leg["strike"],
                "option_type": leg["option_type"],
                "transaction_type": "BUY" if leg["action"] == "BUY" else "SELL",
                "quantity": leg["quantity_lots"] * lot_size,
                "order_type": "MARKET",
                "product": "I",  # Intraday (MIS) - change to "D" for NRML
                "price": 0,
                "tag": f"AI_FO_{proposal.strategy_type.upper()}",
            }
            orders.append(order)
        
        return orders


# Singleton
_fo_trader = None

def get_fo_trader() -> FOTrader:
    """Get or create F&O Trader singleton."""
    global _fo_trader
    if _fo_trader is None:
        _fo_trader = FOTrader()
    return _fo_trader
