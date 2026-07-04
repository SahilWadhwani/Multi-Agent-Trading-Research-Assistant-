"""
Risk Debate Team - Three Risk Perspectives.

Three risk analysts with different risk appetites debate:
1. Aggressive - Maximize returns, accept higher risk
2. Conservative - Preserve capital, minimize losses
3. Neutral - Balanced approach

Final decision synthesizes all three perspectives.
Inspired by TradingAgents' risk management debate mechanism.
"""

import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from llm.client import get_llm_client, LLMClient


class RiskProfile(Enum):
    AGGRESSIVE = "aggressive"
    CONSERVATIVE = "conservative"
    NEUTRAL = "neutral"


@dataclass
class RiskRecommendation:
    """A risk analyst's recommendation."""
    profile: RiskProfile
    position_size: int  # In lots
    stop_loss_pct: float
    target_pct: float
    reasoning: str
    confidence: float
    risk_score: float  # 1-10 scale
    max_loss_amount: float


@dataclass
class RiskConsensus:
    """Consensus from the risk debate."""
    recommended_lots: int
    recommended_stop_loss: float
    recommended_target: float
    risk_score: float
    proceed: bool  # Should we take this trade?
    reasoning: str
    
    # Individual views
    aggressive_view: RiskRecommendation
    conservative_view: RiskRecommendation
    neutral_view: RiskRecommendation


class AggressiveRiskAnalyst:
    """
    Aggressive risk analyst - seeks maximum returns.
    
    Philosophy:
    - Fortune favors the bold
    - Take larger positions on high-confidence trades
    - Wider stops to avoid premature exits
    - Higher targets for bigger gains
    """
    
    def __init__(self, llm: LLMClient = None):
        self.llm = llm or get_llm_client()
        self.profile = RiskProfile.AGGRESSIVE
    
    def analyze(
        self,
        symbol: str,
        premium: float,
        lot_size: int,
        available_capital: float,
        signal_confidence: float,
        market_data: Dict[str, Any],
    ) -> RiskRecommendation:
        """Generate aggressive risk recommendation."""
        # Calculate max lots we can buy
        max_lots = int(available_capital * 0.7 / (premium * lot_size))  # Use up to 70%
        
        prompt = f"""You are an AGGRESSIVE risk analyst. Your philosophy: "Fortune favors the bold."

TRADE SETUP:
- Symbol: {symbol}
- Premium: Rs {premium}
- Lot Size: {lot_size}
- Signal Confidence: {signal_confidence:.0%}
- Available Capital: Rs {available_capital:,.0f}
- Max Possible Lots: {max_lots}

MARKET DATA:
- IV: {market_data.get('iv', 'N/A')}%
- Trend: {market_data.get('trend', 'N/A')}
- PCR: {market_data.get('pcr', 'N/A')}

As an aggressive trader:
1. How many lots should we trade? (maximize opportunity)
2. What stop loss % to use? (wider stops OK for good setups)
3. What target %? (aim high)
4. Risk score (1-10, where 10 = highest risk)

Format:
LOTS: [number]
STOP_LOSS: [percentage, e.g., 50]
TARGET: [percentage, e.g., 80]
RISK_SCORE: [1-10]
REASONING: [2 sentences max]"""

        result = self.llm.chat(prompt=prompt, task_type="analysis")
        response = result.get("response", "")
        
        return self._parse_recommendation(
            response, premium, lot_size, available_capital, max_lots
        )
    
    def _parse_recommendation(
        self, response: str, premium: float, lot_size: int,
        capital: float, max_lots: int
    ) -> RiskRecommendation:
        """Parse LLM response into recommendation."""
        lots = min(max_lots, 2)  # Default aggressive: 2 lots or max
        stop_loss = 50  # Default: 50% SL (wider)
        target = 80  # Default: 80% target (higher)
        risk_score = 7
        reasoning = "Aggressive approach for high conviction trade"
        
        lines = response.split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("LOTS:"):
                try:
                    lots = int(line.replace("LOTS:", "").strip())
                    lots = min(max(lots, 1), max_lots)
                except:
                    pass
            elif line.startswith("STOP_LOSS:"):
                try:
                    stop_loss = float(line.replace("STOP_LOSS:", "").replace("%", "").strip())
                except:
                    pass
            elif line.startswith("TARGET:"):
                try:
                    target = float(line.replace("TARGET:", "").replace("%", "").strip())
                except:
                    pass
            elif line.startswith("RISK_SCORE:"):
                try:
                    risk_score = int(line.replace("RISK_SCORE:", "").strip())
                except:
                    pass
            elif line.startswith("REASONING:"):
                reasoning = line.replace("REASONING:", "").strip()
        
        max_loss = lots * lot_size * premium * (stop_loss / 100)
        
        return RiskRecommendation(
            profile=self.profile,
            position_size=lots,
            stop_loss_pct=stop_loss,
            target_pct=target,
            reasoning=reasoning,
            confidence=0.7,
            risk_score=risk_score,
            max_loss_amount=max_loss,
        )


class ConservativeRiskAnalyst:
    """
    Conservative risk analyst - capital preservation first.
    
    Philosophy:
    - First rule: Don't lose money
    - Smaller positions even on good trades
    - Tight stops to limit losses
    - Take profits early
    """
    
    def __init__(self, llm: LLMClient = None):
        self.llm = llm or get_llm_client()
        self.profile = RiskProfile.CONSERVATIVE
    
    def analyze(
        self,
        symbol: str,
        premium: float,
        lot_size: int,
        available_capital: float,
        signal_confidence: float,
        market_data: Dict[str, Any],
    ) -> RiskRecommendation:
        """Generate conservative risk recommendation."""
        # Max lots with conservative approach: 30% of capital
        max_lots = int(available_capital * 0.3 / (premium * lot_size))
        max_lots = max(max_lots, 1)  # At least 1 lot
        
        prompt = f"""You are a CONSERVATIVE risk analyst. Your philosophy: "Capital preservation is paramount."

TRADE SETUP:
- Symbol: {symbol}
- Premium: Rs {premium}
- Lot Size: {lot_size}
- Signal Confidence: {signal_confidence:.0%}
- Available Capital: Rs {available_capital:,.0f}
- Max Conservative Lots: {max_lots}

MARKET DATA:
- IV: {market_data.get('iv', 'N/A')}%
- Trend: {market_data.get('trend', 'N/A')}
- PCR: {market_data.get('pcr', 'N/A')}

As a conservative trader:
1. How many lots should we trade? (protect capital)
2. What stop loss % to use? (tight stops preferred)
3. What target %? (take profits early)
4. Risk score (1-10, where 10 = highest risk)
5. Should we even take this trade? Or is it too risky?

Format:
LOTS: [number]
STOP_LOSS: [percentage, e.g., 30]
TARGET: [percentage, e.g., 40]
RISK_SCORE: [1-10]
REASONING: [2 sentences max]"""

        result = self.llm.chat(prompt=prompt, task_type="analysis")
        response = result.get("response", "")
        
        return self._parse_recommendation(
            response, premium, lot_size, available_capital, max_lots
        )
    
    def _parse_recommendation(
        self, response: str, premium: float, lot_size: int,
        capital: float, max_lots: int
    ) -> RiskRecommendation:
        """Parse LLM response into recommendation."""
        lots = 1  # Default conservative: 1 lot
        stop_loss = 30  # Default: 30% SL (tight)
        target = 40  # Default: 40% target (early exit)
        risk_score = 4
        reasoning = "Conservative approach to protect capital"
        
        lines = response.split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("LOTS:"):
                try:
                    lots = int(line.replace("LOTS:", "").strip())
                    lots = min(max(lots, 1), max_lots)
                except:
                    pass
            elif line.startswith("STOP_LOSS:"):
                try:
                    stop_loss = float(line.replace("STOP_LOSS:", "").replace("%", "").strip())
                except:
                    pass
            elif line.startswith("TARGET:"):
                try:
                    target = float(line.replace("TARGET:", "").replace("%", "").strip())
                except:
                    pass
            elif line.startswith("RISK_SCORE:"):
                try:
                    risk_score = int(line.replace("RISK_SCORE:", "").strip())
                except:
                    pass
            elif line.startswith("REASONING:"):
                reasoning = line.replace("REASONING:", "").strip()
        
        max_loss = lots * lot_size * premium * (stop_loss / 100)
        
        return RiskRecommendation(
            profile=self.profile,
            position_size=lots,
            stop_loss_pct=stop_loss,
            target_pct=target,
            reasoning=reasoning,
            confidence=0.6,
            risk_score=risk_score,
            max_loss_amount=max_loss,
        )


class NeutralRiskAnalyst:
    """
    Neutral risk analyst - balanced approach.
    
    Philosophy:
    - Balance risk and reward
    - Position size based on conviction
    - Reasonable stops and targets
    - Let winners run, cut losers
    """
    
    def __init__(self, llm: LLMClient = None):
        self.llm = llm or get_llm_client()
        self.profile = RiskProfile.NEUTRAL
    
    def analyze(
        self,
        symbol: str,
        premium: float,
        lot_size: int,
        available_capital: float,
        signal_confidence: float,
        market_data: Dict[str, Any],
    ) -> RiskRecommendation:
        """Generate balanced risk recommendation."""
        # Balanced: 50% of capital max
        max_lots = int(available_capital * 0.5 / (premium * lot_size))
        max_lots = max(max_lots, 1)
        
        prompt = f"""You are a NEUTRAL risk analyst. Your philosophy: "Balance risk and reward."

TRADE SETUP:
- Symbol: {symbol}
- Premium: Rs {premium}
- Lot Size: {lot_size}
- Signal Confidence: {signal_confidence:.0%}
- Available Capital: Rs {available_capital:,.0f}
- Max Balanced Lots: {max_lots}

MARKET DATA:
- IV: {market_data.get('iv', 'N/A')}%
- Trend: {market_data.get('trend', 'N/A')}
- PCR: {market_data.get('pcr', 'N/A')}

As a balanced trader:
1. How many lots should we trade? (based on confidence)
2. What stop loss % to use? (reasonable protection)
3. What target %? (minimum 1:1 risk-reward)
4. Risk score (1-10, where 10 = highest risk)

Format:
LOTS: [number]
STOP_LOSS: [percentage, e.g., 40]
TARGET: [percentage, e.g., 50]
RISK_SCORE: [1-10]
REASONING: [2 sentences max]"""

        result = self.llm.chat(prompt=prompt, task_type="analysis")
        response = result.get("response", "")
        
        return self._parse_recommendation(
            response, premium, lot_size, available_capital, max_lots
        )
    
    def _parse_recommendation(
        self, response: str, premium: float, lot_size: int,
        capital: float, max_lots: int
    ) -> RiskRecommendation:
        """Parse LLM response into recommendation."""
        lots = min(max_lots, 1)  # Default neutral: 1-2 lots
        stop_loss = 40  # Default: 40% SL (balanced)
        target = 50  # Default: 50% target (1:1.25 RR)
        risk_score = 5
        reasoning = "Balanced approach with reasonable risk-reward"
        
        lines = response.split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("LOTS:"):
                try:
                    lots = int(line.replace("LOTS:", "").strip())
                    lots = min(max(lots, 1), max_lots)
                except:
                    pass
            elif line.startswith("STOP_LOSS:"):
                try:
                    stop_loss = float(line.replace("STOP_LOSS:", "").replace("%", "").strip())
                except:
                    pass
            elif line.startswith("TARGET:"):
                try:
                    target = float(line.replace("TARGET:", "").replace("%", "").strip())
                except:
                    pass
            elif line.startswith("RISK_SCORE:"):
                try:
                    risk_score = int(line.replace("RISK_SCORE:", "").strip())
                except:
                    pass
            elif line.startswith("REASONING:"):
                reasoning = line.replace("REASONING:", "").strip()
        
        max_loss = lots * lot_size * premium * (stop_loss / 100)
        
        return RiskRecommendation(
            profile=self.profile,
            position_size=lots,
            stop_loss_pct=stop_loss,
            target_pct=target,
            reasoning=reasoning,
            confidence=0.65,
            risk_score=risk_score,
            max_loss_amount=max_loss,
        )


class RiskDebateEngine:
    """
    Orchestrates the risk debate between three analysts.
    
    Synthesizes aggressive, conservative, and neutral views
    into a final risk recommendation.
    """
    
    def __init__(self):
        self.llm = get_llm_client()
        self.aggressive = AggressiveRiskAnalyst(self.llm)
        self.conservative = ConservativeRiskAnalyst(self.llm)
        self.neutral = NeutralRiskAnalyst(self.llm)
    
    def debate_risk(
        self,
        symbol: str,
        premium: float,
        lot_size: int,
        available_capital: float,
        signal_confidence: float,
        max_loss_allowed: float = 4000,  # Rs 4,000 default
        market_data: Dict[str, Any] = None,
    ) -> RiskConsensus:
        """
        Run risk debate and get consensus.
        
        Args:
            symbol: Trading symbol
            premium: Option premium
            lot_size: Lot size for the symbol
            available_capital: Capital available for trading
            signal_confidence: How confident is the trading signal
            max_loss_allowed: Maximum loss we can tolerate
            market_data: Current market conditions
        
        Returns:
            RiskConsensus with final recommendation
        """
        market_data = market_data or {}
        
        print(f"\n{'='*60}")
        print(f"RISK DEBATE: {symbol}")
        print(f"Premium: Rs {premium}, Lot Size: {lot_size}")
        print(f"Capital: Rs {available_capital:,.0f}, Max Loss: Rs {max_loss_allowed:,.0f}")
        print(f"{'='*60}")
        
        # Get all three perspectives
        print("\n--- AGGRESSIVE ANALYST ---")
        aggressive_view = self.aggressive.analyze(
            symbol, premium, lot_size, available_capital,
            signal_confidence, market_data
        )
        print(f"Lots: {aggressive_view.position_size}, SL: {aggressive_view.stop_loss_pct}%, "
              f"Target: {aggressive_view.target_pct}%")
        print(f"Reasoning: {aggressive_view.reasoning}")
        
        print("\n--- CONSERVATIVE ANALYST ---")
        conservative_view = self.conservative.analyze(
            symbol, premium, lot_size, available_capital,
            signal_confidence, market_data
        )
        print(f"Lots: {conservative_view.position_size}, SL: {conservative_view.stop_loss_pct}%, "
              f"Target: {conservative_view.target_pct}%")
        print(f"Reasoning: {conservative_view.reasoning}")
        
        print("\n--- NEUTRAL ANALYST ---")
        neutral_view = self.neutral.analyze(
            symbol, premium, lot_size, available_capital,
            signal_confidence, market_data
        )
        print(f"Lots: {neutral_view.position_size}, SL: {neutral_view.stop_loss_pct}%, "
              f"Target: {neutral_view.target_pct}%")
        print(f"Reasoning: {neutral_view.reasoning}")
        
        # Synthesize consensus
        print("\n--- CONSENSUS ---")
        consensus = self._synthesize_consensus(
            aggressive_view, conservative_view, neutral_view,
            signal_confidence, max_loss_allowed, premium, lot_size
        )
        
        proceed_emoji = "GO" if consensus.proceed else "NO GO"
        print(f"Decision: {proceed_emoji}")
        print(f"Lots: {consensus.recommended_lots}, SL: {consensus.recommended_stop_loss}%, "
              f"Target: {consensus.recommended_target}%")
        print(f"Risk Score: {consensus.risk_score}/10")
        print(f"Reasoning: {consensus.reasoning}")
        
        print(f"{'='*60}\n")
        
        return consensus
    
    def _synthesize_consensus(
        self,
        aggressive: RiskRecommendation,
        conservative: RiskRecommendation,
        neutral: RiskRecommendation,
        signal_confidence: float,
        max_loss_allowed: float,
        premium: float,
        lot_size: int,
    ) -> RiskConsensus:
        """Synthesize three views into consensus."""
        # Weight by signal confidence
        # High confidence -> lean aggressive
        # Low confidence -> lean conservative
        
        if signal_confidence >= 0.75:
            # High confidence: weight aggressive more
            weights = {"aggressive": 0.4, "neutral": 0.35, "conservative": 0.25}
        elif signal_confidence >= 0.55:
            # Medium confidence: balanced
            weights = {"aggressive": 0.25, "neutral": 0.5, "conservative": 0.25}
        else:
            # Low confidence: weight conservative more
            weights = {"aggressive": 0.15, "neutral": 0.35, "conservative": 0.5}
        
        # Weighted average for lots
        raw_lots = (
            aggressive.position_size * weights["aggressive"] +
            neutral.position_size * weights["neutral"] +
            conservative.position_size * weights["conservative"]
        )
        
        # Weighted average for stop loss
        raw_sl = (
            aggressive.stop_loss_pct * weights["aggressive"] +
            neutral.stop_loss_pct * weights["neutral"] +
            conservative.stop_loss_pct * weights["conservative"]
        )
        
        # Weighted average for target
        raw_target = (
            aggressive.target_pct * weights["aggressive"] +
            neutral.target_pct * weights["neutral"] +
            conservative.target_pct * weights["conservative"]
        )
        
        # Round lots
        recommended_lots = max(1, round(raw_lots))
        
        # Check max loss constraint
        potential_loss = recommended_lots * lot_size * premium * (raw_sl / 100)
        
        while potential_loss > max_loss_allowed and recommended_lots > 1:
            recommended_lots -= 1
            potential_loss = recommended_lots * lot_size * premium * (raw_sl / 100)
        
        # Final risk score
        avg_risk = (
            aggressive.risk_score * weights["aggressive"] +
            neutral.risk_score * weights["neutral"] +
            conservative.risk_score * weights["conservative"]
        )
        
        # Determine if we should proceed
        proceed = True
        reasoning_parts = []
        
        if potential_loss > max_loss_allowed:
            proceed = False
            reasoning_parts.append("potential loss exceeds limit")
        
        if avg_risk > 8:
            proceed = False
            reasoning_parts.append("risk too high")
        
        if signal_confidence < 0.4:
            proceed = False
            reasoning_parts.append("signal confidence too low")
        
        if conservative.risk_score >= 8:
            proceed = False
            reasoning_parts.append("even conservative view sees high risk")
        
        if proceed:
            reasoning = f"Consensus reached with {signal_confidence:.0%} signal confidence. "
            if signal_confidence >= 0.7:
                reasoning += "Higher position size justified by strong signal."
            else:
                reasoning += "Moderate position size with balanced risk."
        else:
            reasoning = f"Trade not recommended: {', '.join(reasoning_parts)}."
        
        return RiskConsensus(
            recommended_lots=recommended_lots,
            recommended_stop_loss=round(raw_sl, 1),
            recommended_target=round(raw_target, 1),
            risk_score=round(avg_risk, 1),
            proceed=proceed,
            reasoning=reasoning,
            aggressive_view=aggressive,
            conservative_view=conservative,
            neutral_view=neutral,
        )


# Singleton
_risk_engine = None

def get_risk_debate_engine() -> RiskDebateEngine:
    """Get or create risk debate engine singleton."""
    global _risk_engine
    if _risk_engine is None:
        _risk_engine = RiskDebateEngine()
    return _risk_engine


def get_risk_recommendation(
    symbol: str,
    premium: float,
    lot_size: int,
    available_capital: float,
    signal_confidence: float,
    max_loss: float = 4000,
) -> RiskConsensus:
    """Convenience function to get risk recommendation."""
    engine = get_risk_debate_engine()
    return engine.debate_risk(
        symbol=symbol,
        premium=premium,
        lot_size=lot_size,
        available_capital=available_capital,
        signal_confidence=signal_confidence,
        max_loss_allowed=max_loss,
    )
