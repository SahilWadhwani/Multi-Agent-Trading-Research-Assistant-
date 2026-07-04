"""
SMART EXIT LOGIC

Instead of fixed targets, the LLM decides when to exit based on:
1. Current P&L
2. Market conditions changing
3. Time decay
4. News/sentiment shifts
5. Technical levels

Only HARD guardrail: Maximum loss limit (25%) - this is non-negotiable for capital protection.
Everything else is dynamic.
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.client import get_llm_client


class ExitReason(Enum):
    HOLD = "hold"                    # Keep position
    TAKE_PROFIT = "take_profit"      # Good profit, exit
    CUT_LOSS = "cut_loss"           # Conditions changed, exit with loss
    TRAIL_STOP = "trail_stop"       # Trailing stop triggered
    TIME_DECAY = "time_decay"       # Too close to expiry
    REVERSAL = "reversal"           # Market reversing
    NEWS_CHANGE = "news_change"     # Sentiment shifted
    MAX_LOSS = "max_loss"           # Hard stop (25% loss)
    EOD_EXIT = "eod_exit"           # End of day
    TARGET_ZONE = "target_zone"     # In profit target zone


@dataclass
class ExitDecision:
    """Smart exit decision."""
    should_exit: bool
    reason: ExitReason
    confidence: float
    explanation: str
    suggested_action: str  # "HOLD", "EXIT_NOW", "TRAIL_STOP", "PARTIAL_EXIT"


class SmartExitManager:
    """
    LLM-powered exit decisions for INTRADAY options.
    
    Key Learnings Applied:
    - INTRADAY ONLY: Exit same day (no overnight theta decay)
    - Fixed SL: 30% max loss (protects capital)
    - Realistic targets: 30-50% profit (achievable intraday)
    - Partial exits: Book 50% at first target
    """
    
    # HARD GUARDRAILS - Cannot be overridden
    MAX_LOSS_PCT = 30.0           # Exit if down 30% - no exceptions
    MUST_EXIT_BY = "15:10"        # Intraday square off time (IST)
    
    # Profit targets (realistic for intraday)
    FIRST_TARGET_PCT = 25.0       # Book partial at 25%
    GOOD_PROFIT_PCT = 35.0        # Good profit - consider full exit
    EXCELLENT_PROFIT_PCT = 50.0   # Excellent - definitely book
    
    # Trailing logic
    MIN_PROFIT_TO_TRAIL = 15.0    # Start trailing after 15% profit
    TRAIL_DISTANCE_PCT = 10.0     # Trail by 10% from peak
    
    def __init__(self):
        self.llm = get_llm_client()
    
    def evaluate_exit(
        self,
        symbol: str,
        option_type: str,  # CE or PE
        entry_price: float,
        current_price: float,
        entry_time: datetime,
        current_spot: float,
        entry_spot: float,
        iv_at_entry: float,
        current_iv: float,
        pcr_at_entry: float,
        current_pcr: float,
        news_sentiment: str,  # BULLISH, BEARISH, NEUTRAL
        time_to_expiry_hours: float,
        highest_pnl_pct: float = 0,  # Peak P&L reached
    ) -> ExitDecision:
        """
        Make a smart exit decision using LLM.
        """
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        
        # Calculate current P&L
        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        spot_move_pct = ((current_spot - entry_spot) / entry_spot) * 100
        hold_duration_mins = (now - entry_time).total_seconds() / 60
        
        # HARD STOP - Non-negotiable
        if pnl_pct <= -self.MAX_LOSS_PCT:
            return ExitDecision(
                should_exit=True,
                reason=ExitReason.MAX_LOSS,
                confidence=1.0,
                explanation=f"HARD STOP: Position down {pnl_pct:.1f}% (max loss limit {self.MAX_LOSS_PCT}%)",
                suggested_action="EXIT_NOW"
            )
        
        # EOD Exit - 15 mins before close
        market_close = now.replace(hour=15, minute=15)
        if now >= market_close:
            return ExitDecision(
                should_exit=True,
                reason=ExitReason.EOD_EXIT,
                confidence=1.0,
                explanation="End of day - closing all intraday positions",
                suggested_action="EXIT_NOW"
            )
        
        # Build context for LLM
        position_context = f"""
POSITION STATUS:
- Symbol: {symbol} {option_type}
- Entry: Rs {entry_price:.1f} | Current: Rs {current_price:.1f}
- P&L: {pnl_pct:+.1f}%
- Peak P&L reached: {highest_pnl_pct:+.1f}%
- Hold duration: {hold_duration_mins:.0f} minutes

MARKET CONTEXT:
- Spot moved: {spot_move_pct:+.2f}% since entry
- IV: {iv_at_entry:.1f}% → {current_iv:.1f}% ({"rising" if current_iv > iv_at_entry else "falling"})
- PCR: {pcr_at_entry:.2f} → {current_pcr:.2f}
- News sentiment: {news_sentiment}
- Time to expiry: {time_to_expiry_hours:.1f} hours

OPTION TYPE CONTEXT:
- {option_type} option {"gains" if option_type == "PE" else "loses"} when spot falls
- Current spot move is {"favorable" if (option_type == "PE" and spot_move_pct < 0) or (option_type == "CE" and spot_move_pct > 0) else "unfavorable"}
"""

        prompt = f"""You are managing an options position. Decide whether to HOLD or EXIT.

{position_context}

GUIDELINES:
1. Let profits run - don't exit just because you're up 10-15%
2. If up 20%+, consider trailing (don't give back all gains)
3. If market conditions have reversed against the position, cut losses
4. If approaching expiry with time decay eating premium, consider exit
5. News sentiment shift against position = warning sign

RESPOND WITH ONE OF:
- HOLD: Keep position, conditions still favorable
- EXIT_NOW: Close immediately (explain why)
- TRAIL_STOP: Set mental stop at current level minus 10% (we're in good profit)
- PARTIAL_EXIT: Book 50% profit, let rest run

Your response format:
DECISION: [HOLD/EXIT_NOW/TRAIL_STOP/PARTIAL_EXIT]
CONFIDENCE: [0.0-1.0]
REASON: [One line explanation]
"""

        try:
            response = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                task_type="trading",
            )
            
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Parse response
            decision = "HOLD"
            confidence = 0.5
            reason = "LLM analysis"
            
            for line in content.split('\n'):
                line = line.strip().upper()
                if line.startswith("DECISION:"):
                    dec = line.replace("DECISION:", "").strip()
                    if "EXIT" in dec:
                        decision = "EXIT_NOW"
                    elif "TRAIL" in dec:
                        decision = "TRAIL_STOP"
                    elif "PARTIAL" in dec:
                        decision = "PARTIAL_EXIT"
                    else:
                        decision = "HOLD"
                elif line.startswith("CONFIDENCE:"):
                    try:
                        confidence = float(line.replace("CONFIDENCE:", "").strip())
                    except:
                        pass
                elif line.startswith("REASON:"):
                    reason = line.replace("REASON:", "").strip()
            
            # Map to exit reason
            if decision == "EXIT_NOW":
                if pnl_pct > 0:
                    exit_reason = ExitReason.TAKE_PROFIT
                else:
                    exit_reason = ExitReason.CUT_LOSS
            elif decision == "TRAIL_STOP":
                exit_reason = ExitReason.TRAIL_STOP
            else:
                exit_reason = ExitReason.HOLD
            
            return ExitDecision(
                should_exit=(decision in ["EXIT_NOW"]),
                reason=exit_reason,
                confidence=confidence,
                explanation=reason,
                suggested_action=decision
            )
            
        except Exception as e:
            # Fallback to rule-based if LLM fails
            return self._fallback_decision(pnl_pct, highest_pnl_pct, spot_move_pct, option_type)
    
    def _fallback_decision(
        self,
        pnl_pct: float,
        highest_pnl_pct: float,
        spot_move_pct: float,
        option_type: str,
    ) -> ExitDecision:
        """Fallback rules if LLM is unavailable."""
        
        # Trailing stop logic
        if highest_pnl_pct >= 20 and pnl_pct < highest_pnl_pct - 10:
            return ExitDecision(
                should_exit=True,
                reason=ExitReason.TRAIL_STOP,
                confidence=0.8,
                explanation=f"Trailing stop: was up {highest_pnl_pct:.1f}%, now {pnl_pct:.1f}%",
                suggested_action="EXIT_NOW"
            )
        
        # Excellent profit - book it
        if pnl_pct >= self.EXCELLENT_PROFIT:
            return ExitDecision(
                should_exit=True,
                reason=ExitReason.TARGET_ZONE,
                confidence=0.85,
                explanation=f"Excellent profit {pnl_pct:.1f}% - booking gains",
                suggested_action="EXIT_NOW"
            )
        
        # Check if market reversed
        is_favorable = (option_type == "PE" and spot_move_pct < 0) or (option_type == "CE" and spot_move_pct > 0)
        if pnl_pct < -10 and not is_favorable:
            return ExitDecision(
                should_exit=True,
                reason=ExitReason.REVERSAL,
                confidence=0.7,
                explanation=f"Market moving against position, down {pnl_pct:.1f}%",
                suggested_action="EXIT_NOW"
            )
        
        # Default: hold
        return ExitDecision(
            should_exit=False,
            reason=ExitReason.HOLD,
            confidence=0.6,
            explanation="Conditions acceptable, holding position",
            suggested_action="HOLD"
        )
    
    def _compute_dynamic_thresholds(
        self,
        hours_held: float = 0.0,
        current_hour_ist: int = 12,
        atr_pct: float = 0.0,
    ) -> Dict[str, float]:
        """
        Phase G: Compute dynamic exit thresholds based on time and volatility.

        Returns dict with adjusted: max_loss, good_profit, excellent_profit,
        trail_distance, min_profit_to_trail.
        """
        # Total session is ~6 hours (9:15 - 15:15)
        total_session_hours = 6.0

        # Theta decay factor: increases from 0 to 0.4 as time passes
        time_factor = 0.0
        if hours_held > 2.0:
            time_factor = min(0.4, (hours_held - 2.0) / total_session_hours)
        if current_hour_ist >= 14:
            time_factor = max(time_factor, 0.2)
        if current_hour_ist >= 15:
            time_factor = max(time_factor, 0.35)

        # Theta-aware SL tightening: base 30% tightens to ~18% near close
        dynamic_sl = self.MAX_LOSS_PCT * (1.0 - time_factor)
        dynamic_sl = max(dynamic_sl, 15.0)  # Never tighter than 15%

        # Theta profit acceleration: lower profit targets late in the day
        profit_decay = 0.0
        if current_hour_ist >= 14 and hours_held > 1.5:
            profit_decay = min(0.3, (current_hour_ist - 13) * 0.1)
        dynamic_good_profit = self.GOOD_PROFIT_PCT * (1.0 - profit_decay)
        dynamic_excellent_profit = self.EXCELLENT_PROFIT_PCT * (1.0 - profit_decay)

        # ATR-based trail distance: volatile days wider, quiet days tighter
        if atr_pct > 0:
            dynamic_trail = max(8.0, min(15.0, atr_pct * 1.5 * 100))
        else:
            dynamic_trail = self.TRAIL_DISTANCE_PCT

        # ATR also adjusts minimum profit before trailing starts
        dynamic_min_trail = self.MIN_PROFIT_TO_TRAIL
        if atr_pct > 0.005:  # High vol
            dynamic_min_trail = min(20.0, self.MIN_PROFIT_TO_TRAIL + 5.0)

        return {
            "max_loss": dynamic_sl,
            "good_profit": dynamic_good_profit,
            "excellent_profit": dynamic_excellent_profit,
            "trail_distance": dynamic_trail,
            "min_profit_to_trail": dynamic_min_trail,
        }

    def quick_check(
        self,
        pnl_pct: float,
        highest_pnl_pct: float,
        hours_held: float = 0.0,
        current_hour_ist: int = 0,
        atr_pct: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        Quick check without full LLM call.
        Returns (should_exit, reason).

        Phase G: If hours_held/current_hour_ist/atr_pct provided, uses dynamic
        theta-aware and ATR-based thresholds. Otherwise falls back to flat logic.
        """
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)

        # INTRADAY EXIT - Must exit by 3:10 PM
        if now.hour >= 15 and now.minute >= 10:
            return True, f"INTRADAY_EXIT (market closing, P&L: {pnl_pct:+.1f}%)"

        # Use dynamic thresholds if timing context is provided
        if current_hour_ist > 0 or hours_held > 0:
            if current_hour_ist == 0:
                current_hour_ist = now.hour
            t = self._compute_dynamic_thresholds(hours_held, current_hour_ist, atr_pct)
        else:
            t = {
                "max_loss": self.MAX_LOSS_PCT,
                "good_profit": self.GOOD_PROFIT_PCT,
                "excellent_profit": self.EXCELLENT_PROFIT_PCT,
                "trail_distance": self.TRAIL_DISTANCE_PCT,
                "min_profit_to_trail": self.MIN_PROFIT_TO_TRAIL,
            }

        # Hard stop loss (dynamic: tightens with time)
        if pnl_pct <= -t["max_loss"]:
            return True, f"STOP_LOSS ({pnl_pct:.1f}% >= {t['max_loss']:.0f}% dynamic max)"

        # Excellent profit
        if pnl_pct >= t["excellent_profit"]:
            return True, f"EXCELLENT_PROFIT ({pnl_pct:.1f}% >= {t['excellent_profit']:.0f}%)"

        # Good profit
        if pnl_pct >= t["good_profit"]:
            return True, f"GOOD_PROFIT ({pnl_pct:.1f}% >= {t['good_profit']:.0f}%)"

        # Trailing stop (ATR-adjusted distance)
        if highest_pnl_pct >= t["min_profit_to_trail"]:
            trail_level = highest_pnl_pct - t["trail_distance"]
            if pnl_pct < trail_level:
                return True, (
                    f"TRAIL_STOP (peak {highest_pnl_pct:.1f}%, now {pnl_pct:.1f}%, "
                    f"trail {t['trail_distance']:.0f}%)"
                )

        # First target reached (partial exit signal)
        if pnl_pct >= self.FIRST_TARGET_PCT:
            return False, f"FIRST_TARGET ({pnl_pct:.1f}%) - consider partial exit"

        return False, "HOLD"


# Singleton
_exit_manager = None

def get_exit_manager() -> SmartExitManager:
    global _exit_manager
    if _exit_manager is None:
        _exit_manager = SmartExitManager()
    return _exit_manager


def should_exit(
    pnl_pct: float,
    highest_pnl_pct: float = 0,
    use_llm: bool = False,
    hours_held: float = 0.0,
    current_hour_ist: int = 0,
    atr_pct: float = 0.0,
    **kwargs
) -> Tuple[bool, str]:
    """
    Quick function to check if should exit.

    Args:
        pnl_pct: Current P&L percentage
        highest_pnl_pct: Highest P&L reached
        use_llm: Whether to use full LLM analysis
        hours_held: Hours since entry (for theta-aware dynamic SL)
        current_hour_ist: Current hour in IST (for theta acceleration)
        atr_pct: Spot ATR as % of spot (for dynamic trail width)
        **kwargs: Additional context for LLM

    Returns:
        (should_exit, reason)
    """
    manager = get_exit_manager()

    if use_llm and kwargs:
        decision = manager.evaluate_exit(**kwargs)
        return decision.should_exit, decision.explanation
    else:
        return manager.quick_check(
            pnl_pct, highest_pnl_pct,
            hours_held=hours_held,
            current_hour_ist=current_hour_ist,
            atr_pct=atr_pct,
        )


# Test
if __name__ == "__main__":
    print("Smart Exit Manager Test")
    print("="*50)
    
    manager = SmartExitManager()
    
    # Test scenarios
    scenarios = [
        {"pnl": -30, "peak": 5, "desc": "Down 30% (should hit max loss)"},
        {"pnl": 25, "peak": 28, "desc": "Up 25%, was up 28% (trailing)"},
        {"pnl": 40, "peak": 40, "desc": "Up 40% (excellent profit)"},
        {"pnl": 5, "peak": 10, "desc": "Up 5%, was up 10% (small pullback)"},
        {"pnl": -5, "peak": 0, "desc": "Down 5% (acceptable loss)"},
    ]
    
    for s in scenarios:
        should, reason = manager.quick_check(s["pnl"], s["peak"])
        status = "EXIT" if should else "HOLD"
        print(f"\n{s['desc']}")
        print(f"  Decision: {status} - {reason}")
