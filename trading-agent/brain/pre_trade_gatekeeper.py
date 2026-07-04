"""
Pre-Trade Gatekeeper: Institutional-grade validation gateway.

Combines all 8 fixes into a single deterministic validator that prevents
directional options trading mistakes through market regime, theta-aware SL,
and calibrated win probability checks.

Based on Gemini's architectural review - fixes 3 critical logical bugs
in the initial fix implementations.
"""

import math
import logging
from typing import Dict, Optional, Tuple, Any
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class PreTradeGatekeeper:
    """
    Single validation point for all pre-trade checks.
    
    Architectural improvements over scattered checks:
    - No logic duplication
    - Deterministic state management
    - Clear pass/fail contracts
    - Calibration metrics for continuous improvement
    """
    
    def __init__(self, normal_lot_size: int = 50):
        self.normal_lot_size = normal_lot_size
        self._skip_count = 0
        self._execute_count = 0
        self._calibration_history = []  # Track win_rate vs actual outcomes
    
    def validate_execution(
        self,
        signal: Dict[str, Any],
        market_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Runs complete pipeline validation.
        
        Args:
            signal: {
                "direction": "bearish|bullish",
                "llm_confidence": 0.85,
                "strike": 23400,
                "option_type": "PE|CE",
                "entry_premium": 212.4,
            }
            market_data: {
                "regime": "STRONG_TREND|MEAN_REVERT|CHOPPY|BREAKOUT",
                "spot_price": 23400,
                "support": 23100,
                "resistance": 23700,
                "hours_to_expiry": 4.5,
                "iv_regime": "LOW|NORMAL|ELEVATED|HIGH",
            }
        
        Returns: {
            "status": "EXECUTE" | "SKIP",
            "reason": "...",
            "size": int,  # if EXECUTE
            "stop_loss_price": float,  # if EXECUTE
            "stop_loss_pct": float,  # if EXECUTE
            "win_probability": float,  # if EXECUTE (0-1)
            "calibrated_reason": str,  # if EXECUTE
        }
        """
        result = {"status": "SKIP", "reason": "Unknown"}
        
        # ─── GATE 1: Market Regime Check ───
        regime = market_data.get("regime", "unknown").lower()
        if (
            "mean_revert" in regime
            or "choppy" in regime
            or "range_bound" in regime
            or "low_vol_grind" in regime
            or "expiry_day" in regime
        ):
            result["reason"] = f"High-risk regime: {regime} - directional bets unreliable"
            self._skip_count += 1
            return result
        
        # ─── GATE 2: Support/Resistance Boundary Guard ───
        spot = market_data.get("spot_price", 0)
        support = market_data.get("support", spot * 0.99)
        resistance = market_data.get("resistance", spot * 1.01)
        
        distance_to_support = ((spot - support) / support) * 100 if support > 0 else 999
        distance_to_resistance = ((resistance - spot) / spot) * 100 if spot > 0 else 999
        
        signal_dir = signal.get("direction", "").lower()
        
        # Bearish bets (PE) need distance from support
        if "pe" in signal_dir.lower() or "bearish" in signal_dir:
            if distance_to_support < 1.0:
                result["reason"] = (
                    f"Bearish bet blocked: spot within {distance_to_support:.2f}% of support "
                    f"({support:.0f}) - high reversal risk"
                )
                self._skip_count += 1
                return result
        
        # Bullish bets (CE) need distance from resistance
        if "ce" in signal_dir.lower() or "bullish" in signal_dir:
            if distance_to_resistance < 1.0:
                result["reason"] = (
                    f"Bullish bet blocked: spot within {distance_to_resistance:.2f}% of resistance "
                    f"({resistance:.0f}) - high reversal risk"
                )
                self._skip_count += 1
                return result
        
        # ─── GATE 3: Non-Linear Theta-Aware Calibration ───
        hours_to_expiry = market_data.get("hours_to_expiry", 24)
        llm_confidence = self._normalize_confidence(signal.get("llm_confidence", 0.5))
        
        calibrated_win_rate = self._calibrate_probability(
            llm_confidence, regime, hours_to_expiry
        )
        
        if calibrated_win_rate < 0.55:  # Hard floor for edge
            result["reason"] = (
                f"Calibrated win probability too low: {calibrated_win_rate:.2%} "
                    f"(LLM: {llm_confidence:.0%}, regime: {regime}, TTX: {hours_to_expiry:.1f}h)"
            )
            self._skip_count += 1
            return result
        
        # ─── GATE 4: Position Sizing (Theta-aware) ───
        requested_lots = max(1, int(signal.get("lots") or 1))
        position_size = self._calculate_size(hours_to_expiry, requested_lots=requested_lots)
        
        if position_size == 0:
            result["reason"] = (
                f"Position size zeroed: terminal theta decay window "
                f"(hours to expiry: {hours_to_expiry:.1f}h)"
            )
            self._skip_count += 1
            return result
        
        # ─── GATE 5: Non-Linear Smart SL Calculation ───
        entry_premium = signal.get("entry_premium", 100)
        sl_price, sl_pct = self._calculate_nonlinear_sl(entry_premium, hours_to_expiry)
        
        # ─── ALL GATES PASSED ───
        self._execute_count += 1
        result = {
            "status": "EXECUTE",
            "size": position_size,
            "stop_loss_price": sl_price,
            "stop_loss_pct": sl_pct,
            "win_probability": calibrated_win_rate,
            "calibrated_reason": (
                f"Signal approved: {calibrated_win_rate:.1%} win rate "
                f"({llm_confidence:.0%} LLM × {regime} regime × {hours_to_expiry:.1f}h theta factor)"
            ),
        }
        
        # Log for calibration
        self._calibration_history.append({
            "timestamp": datetime.now(IST),
            "signal_dir": signal_dir,
            "calibrated_win_rate": calibrated_win_rate,
            "actual_outcome": None,  # Will be filled later after trade closes
            "hours_to_expiry": hours_to_expiry,
        })
        
        return result
    
    def _normalize_confidence(self, raw_confidence: float) -> float:
        """
        Normalize confidence to 0..1.

        Older LLM prompts return 0..100, while LeanFOBrain signal_strength is
        already 0..1. Treating both correctly is critical; dividing a 0.85
        signal by 100 turns a strong signal into 0.85%.
        """
        try:
            conf = float(raw_confidence)
        except (TypeError, ValueError):
            return 0.5
        if conf > 1.0:
            conf = conf / 100.0
        return min(max(conf, 0.0), 1.0)

    def _calibrate_probability(
        self,
        llm_confidence: float,
        regime: str,
        hours_to_expiry: float,
    ) -> float:
        """
        FIX for Gemini's bug #1: Non-linear calibration with proper bounding.
        
        Converts LLM analysis confidence → actual win probability
        accounting for:
        1. Market regime factor
        2. Exponential time decay penalty near expiry (non-linear theta)
        """
        # Start with normalized LLM/signal confidence (0..1).
        win_rate = self._normalize_confidence(llm_confidence)
        
        # Regime-based adjustment
        regime_factors = {
            "strong_trend": 1.1,
            "breakout": 1.15,
            "mean_revert": 0.4,  # LLM predictions LOSE in reversals
            "choppy": 0.5,       # LLM unreliable in chop
            "trending_bullish": 1.08,
            "trending_bearish": 1.08,
            "high_vol_breakout": 1.05,
            "range_bound": 0.45,
            "low_vol_grind": 0.50,
            "expiry_day": 0.25,
        }
        regime_factor = regime_factors.get(regime.lower(), 0.6)
        win_rate *= regime_factor
        
        # Non-linear time decay penalty (FIX for bug #2)
        # Terminal window (last 24 hours): aggressive penalty via sqrt scaling
        if hours_to_expiry < 24:
            # Sqrt decay: 24h → 1.0x, 12h → 0.71x, 4h → 0.41x, 1h → 0.2x
            time_decay_factor = math.sqrt(hours_to_expiry / 24.0)
            win_rate *= time_decay_factor
        
        # CRITICAL FIX: Bound result to [0.0, 1.0]
        # This prevents win_rate from exceeding 1.0 due to regime factors
        return min(max(win_rate, 0.0), 1.0)
    
    def _calculate_size(self, hours_to_expiry: float, requested_lots: int = 1) -> int:
        """
        Position sizing in lots based on theta decay proximity.
        
        Reduces size as expiry approaches to limit theta bleed.
        """
        requested_lots = max(1, int(requested_lots or 1))
        if hours_to_expiry < 4:
            # Terminal window: zero out (theta > directional move)
            return 0
        if hours_to_expiry < 8:
            # Intermediate window: half size
            return max(1, requested_lots // 2)
        # Normal / next-day expiry: do not increase the upstream risk-sized lots.
        return requested_lots
    
    def _calculate_nonlinear_sl(
        self,
        entry_premium: float,
        hours_to_expiry: float,
    ) -> Tuple[float, float]:
        """
        FIX for Gemini's bug #2: Non-linear theta-aware SL.
        
        Instead of linear scaling (hours / 24), use:
        - sqrt(hours_to_expiry / 24) to model accelerating theta decay
        - time_factor multiplier that widens SL as expiry approaches
        
        Near expiry: theta dominates, so SL must be wider
        Far from expiry: directional move can be small, SL tight
        """
        # Non-linear time factor (closer to 1.0 = closer to expiry)
        # Uses sqrt to model theta acceleration: last hour is more brutal than first hour
        time_factor = 1.0 / max(math.sqrt(hours_to_expiry / 24.0), 0.1)
        
        # Base directional buffer (5% of premium)
        directional_buffer = entry_premium * 0.05
        
        # Final SL delta combines directional move + theta acceleration
        sl_delta = directional_buffer * time_factor
        
        # Hard floor: never let SL go below 50% of entry (max 50% loss)
        sl_price = max(entry_premium - sl_delta, entry_premium * 0.5)
        
        # Calculate SL as percentage
        sl_pct = ((entry_premium - sl_price) / entry_premium) * 100
        
        return round(sl_price, 2), round(sl_pct, 2)
    
    def get_calibration_metrics(self) -> Dict[str, Any]:
        """
        Return metrics for calibration validation.
        
        Gemini's verification blueprint:
        - Skip Rate: Should be 20-50% (not >85%)
        - Win Rate vs Calibrated: Should match within ±5%
        """
        total_decisions = self._skip_count + self._execute_count
        skip_rate = (self._skip_count / total_decisions * 100) if total_decisions > 0 else 0
        
        # Calculate accuracy: compare calibrated_win_rate to actual outcomes
        completed_trades = [t for t in self._calibration_history if t["actual_outcome"] is not None]
        
        calibration_accuracy = None
        if completed_trades:
            actual_wins = sum(1 for t in completed_trades if t["actual_outcome"])
            actual_win_rate = actual_wins / len(completed_trades)
            
            avg_calibrated = sum(t["calibrated_win_rate"] for t in completed_trades) / len(completed_trades)
            
            calibration_accuracy = {
                "actual_win_rate": actual_win_rate,
                "avg_calibrated_win_rate": avg_calibrated,
                "error_pct": abs(actual_win_rate - avg_calibrated) * 100,
                "is_well_calibrated": abs(actual_win_rate - avg_calibrated) <= 0.05,  # ±5% threshold
                "trades_analyzed": len(completed_trades),
            }
        
        return {
            "total_decisions": total_decisions,
            "skipped": self._skip_count,
            "executed": self._execute_count,
            "skip_rate_pct": skip_rate,
            "skip_rate_healthy": 20 <= skip_rate <= 85,  # Target range
            "calibration_accuracy": calibration_accuracy,
        }
    
    def record_trade_outcome(self, decision_id: str, won: bool) -> None:
        """
        Called after a trade closes to record actual outcome for calibration.
        
        Allows continuous improvement of win_rate calibration.
        """
        if self._calibration_history:
            # Find the most recent entry matching this trade
            for entry in reversed(self._calibration_history):
                if entry["actual_outcome"] is None:
                    entry["actual_outcome"] = won
                    logger.info(
                        f"Trade calibration recorded: {decision_id} → {won} | "
                        f"Calibrated: {entry['calibrated_win_rate']:.1%}"
                    )
                    break


# Singleton instance
_gatekeeper: Optional[PreTradeGatekeeper] = None


def get_pre_trade_gatekeeper() -> PreTradeGatekeeper:
    """Get or create global gatekeeper instance."""
    global _gatekeeper
    if _gatekeeper is None:
        _gatekeeper = PreTradeGatekeeper(normal_lot_size=50)
    return _gatekeeper


def validate_signal_before_execution(
    signal: Dict[str, Any],
    market_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Convenience function: validate a signal using global gatekeeper.
    
    Example:
    ```python
    result = validate_signal_before_execution(
        signal={
            "direction": "bearish",
            "llm_confidence": 0.85,
            "entry_premium": 212.4,
        },
        market_data={
            "regime": "STRONG_TREND",
            "spot_price": 23400,
            "support": 23100,
            "hours_to_expiry": 4.5,
        }
    )
    
    if result["status"] == "EXECUTE":
        # Place order
        position_size = result["size"]
        sl_price = result["stop_loss_price"]
    else:
        # Skip trade
        reason = result["reason"]
    ```
    """
    gatekeeper = get_pre_trade_gatekeeper()
    return gatekeeper.validate_execution(signal, market_data)
