"""
CALIBRATOR - Learn from Trade Outcomes

Based on the Medium article's approach:
- Track performance per underlying
- Adjust thresholds based on results
- All adjustments are bounded and logged

Key insight: "All adjustments are bounded, logged, and reversible.
No process hard-codes new parameters autonomously."
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.decision_log import get_decision_log, DecisionOutcome


@dataclass
class SymbolCalibration:
    """Calibration settings for a specific symbol."""
    symbol: str
    
    # Thresholds (can be adjusted)
    min_confidence: float = 0.55
    min_signal_strength: float = 0.55
    max_iv_for_buying: float = 30.0
    
    # Risk parameters
    default_stop_loss_pct: float = 40.0
    default_target_pct: float = 50.0
    
    # Performance metrics
    total_trades: int = 0
    winning_trades: int = 0
    total_pnl: float = 0
    
    # Calibration history
    last_calibrated: Optional[datetime] = None
    calibration_count: int = 0


class Calibrator:
    """
    Calibrates trading thresholds based on outcomes.
    
    Runs daily after settlement to:
    1. Analyze recent trade performance
    2. Adjust thresholds for underperforming symbols
    3. Relax thresholds for outperforming symbols
    4. Log all changes for transparency
    """
    
    # Calibration bounds (NEVER exceed these)
    MIN_CONFIDENCE_FLOOR = 0.45
    MIN_CONFIDENCE_CEILING = 0.75
    MIN_SIGNAL_FLOOR = 0.45
    MIN_SIGNAL_CEILING = 0.75
    MAX_IV_FLOOR = 20.0
    MAX_IV_CEILING = 40.0
    
    # Adjustment step size
    STEP_SIZE = 0.02  # 2% per adjustment
    
    # Minimum trades for calibration
    MIN_TRADES_FOR_CALIBRATION = 5
    
    # Performance thresholds
    WIN_RATE_LOWER = 0.40  # Below this = tighten thresholds
    WIN_RATE_UPPER = 0.60  # Above this = can relax thresholds
    
    def __init__(self, config_path: str = None):
        self.decision_log = get_decision_log()
        self.config_path = config_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data_cache",
            "calibration.json"
        )
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        
        self.calibrations: Dict[str, SymbolCalibration] = {}
        self._load_calibrations()
    
    def _load_calibrations(self):
        """Load existing calibrations from disk."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)
                
                for symbol, cal_data in data.items():
                    self.calibrations[symbol] = SymbolCalibration(
                        symbol=symbol,
                        min_confidence=cal_data.get("min_confidence", 0.55),
                        min_signal_strength=cal_data.get("min_signal_strength", 0.55),
                        max_iv_for_buying=cal_data.get("max_iv_for_buying", 30.0),
                        default_stop_loss_pct=cal_data.get("default_stop_loss_pct", 40.0),
                        default_target_pct=cal_data.get("default_target_pct", 50.0),
                        total_trades=cal_data.get("total_trades", 0),
                        winning_trades=cal_data.get("winning_trades", 0),
                        total_pnl=cal_data.get("total_pnl", 0),
                        calibration_count=cal_data.get("calibration_count", 0),
                    )
                    
            except Exception as e:
                print(f"Warning: Could not load calibrations: {e}")
    
    def _save_calibrations(self):
        """Save calibrations to disk."""
        data = {}
        for symbol, cal in self.calibrations.items():
            data[symbol] = {
                "min_confidence": cal.min_confidence,
                "min_signal_strength": cal.min_signal_strength,
                "max_iv_for_buying": cal.max_iv_for_buying,
                "default_stop_loss_pct": cal.default_stop_loss_pct,
                "default_target_pct": cal.default_target_pct,
                "total_trades": cal.total_trades,
                "winning_trades": cal.winning_trades,
                "total_pnl": cal.total_pnl,
                "calibration_count": cal.calibration_count,
                "last_calibrated": cal.last_calibrated.isoformat() if cal.last_calibrated else None,
            }
        
        with open(self.config_path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def get_calibration(self, symbol: str) -> SymbolCalibration:
        """Get calibration for a symbol (or default)."""
        symbol = symbol.upper()
        
        if symbol not in self.calibrations:
            self.calibrations[symbol] = SymbolCalibration(symbol=symbol)
        
        return self.calibrations[symbol]
    
    def run_daily_calibration(self, days: int = 14) -> Dict[str, Any]:
        """
        Run daily calibration based on recent performance.
        
        Args:
            days: Look back period for performance analysis
        
        Returns:
            Summary of calibration changes
        """
        print(f"\n{'='*60}")
        print("DAILY CALIBRATION")
        print(f"{'='*60}")
        
        summary = {
            "timestamp": datetime.now().isoformat(),
            "lookback_days": days,
            "symbols_analyzed": [],
            "adjustments": [],
        }
        
        # Get performance stats by symbol
        stats = self.decision_log.get_performance_stats(days=days)
        
        if stats.get("total_trades", 0) < self.MIN_TRADES_FOR_CALIBRATION:
            print(f"Not enough trades ({stats.get('total_trades', 0)}) for calibration")
            summary["status"] = "SKIPPED_INSUFFICIENT_DATA"
            return summary
        
        # Get trades grouped by symbol
        decisions = self.decision_log.get_recent_decisions(limit=200)
        cutoff = datetime.now() - timedelta(days=days)
        recent = [d for d in decisions if d.timestamp > cutoff and d.outcome != DecisionOutcome.PENDING]
        
        # Group by symbol
        by_symbol: Dict[str, List] = {}
        for d in recent:
            if d.symbol not in by_symbol:
                by_symbol[d.symbol] = []
            by_symbol[d.symbol].append(d)
        
        # Calibrate each symbol
        for symbol, trades in by_symbol.items():
            if len(trades) < self.MIN_TRADES_FOR_CALIBRATION:
                continue
            
            summary["symbols_analyzed"].append(symbol)
            
            # Calculate symbol performance
            wins = len([t for t in trades if t.pnl > 0])
            total_pnl = sum(t.pnl for t in trades)
            win_rate = wins / len(trades)
            
            print(f"\n{symbol}: {len(trades)} trades, {win_rate:.0%} win rate, Rs {total_pnl:+,.0f}")
            
            # Get current calibration
            cal = self.get_calibration(symbol)
            old_conf = cal.min_confidence
            old_signal = cal.min_signal_strength
            old_iv = cal.max_iv_for_buying
            
            # Update metrics
            cal.total_trades = len(trades)
            cal.winning_trades = wins
            cal.total_pnl = total_pnl
            
            # Adjust based on performance
            if win_rate < self.WIN_RATE_LOWER or total_pnl <= 0:
                # Underperforming: TIGHTEN thresholds (be more selective)
                cal.min_confidence = min(cal.min_confidence + self.STEP_SIZE, self.MIN_CONFIDENCE_CEILING)
                cal.min_signal_strength = min(cal.min_signal_strength + self.STEP_SIZE, self.MIN_SIGNAL_CEILING)
                cal.max_iv_for_buying = max(cal.max_iv_for_buying - 2, self.MAX_IV_FLOOR)
                
                action = "TIGHTENED"
                
            elif win_rate > self.WIN_RATE_UPPER and total_pnl > 0:
                # Outperforming: Can RELAX thresholds (take more trades)
                cal.min_confidence = max(cal.min_confidence - self.STEP_SIZE, self.MIN_CONFIDENCE_FLOOR)
                cal.min_signal_strength = max(cal.min_signal_strength - self.STEP_SIZE, self.MIN_SIGNAL_FLOOR)
                cal.max_iv_for_buying = min(cal.max_iv_for_buying + 2, self.MAX_IV_CEILING)
                
                action = "RELAXED"
            else:
                action = "UNCHANGED"
            
            cal.last_calibrated = datetime.now()
            cal.calibration_count += 1
            
            # Log changes
            if action != "UNCHANGED":
                adjustment = {
                    "symbol": symbol,
                    "action": action,
                    "reason": f"Win rate: {win_rate:.0%}, P&L: Rs {total_pnl:+,.0f}",
                    "changes": {
                        "min_confidence": f"{old_conf:.2f} → {cal.min_confidence:.2f}",
                        "min_signal": f"{old_signal:.2f} → {cal.min_signal_strength:.2f}",
                        "max_iv": f"{old_iv:.1f} → {cal.max_iv_for_buying:.1f}",
                    }
                }
                summary["adjustments"].append(adjustment)
                
                print(f"   {action}:")
                print(f"     min_confidence: {old_conf:.2f} → {cal.min_confidence:.2f}")
                print(f"     min_signal: {old_signal:.2f} → {cal.min_signal_strength:.2f}")
                print(f"     max_iv: {old_iv:.1f} → {cal.max_iv_for_buying:.1f}")
            else:
                print(f"   {action} (performance within acceptable range)")
        
        # Save calibrations
        self._save_calibrations()
        
        summary["status"] = "COMPLETED"
        print(f"\n{'='*60}")
        
        return summary
    
    def get_trading_parameters(self, symbol: str) -> Dict[str, Any]:
        """
        Get calibrated trading parameters for a symbol.
        
        Use this when generating signals to apply calibrated thresholds.
        """
        cal = self.get_calibration(symbol)
        
        return {
            "min_confidence": cal.min_confidence,
            "min_signal_strength": cal.min_signal_strength,
            "max_iv_for_buying": cal.max_iv_for_buying,
            "default_stop_loss_pct": cal.default_stop_loss_pct,
            "default_target_pct": cal.default_target_pct,
            "total_trades_in_period": cal.total_trades,
            "win_rate_in_period": cal.winning_trades / cal.total_trades if cal.total_trades > 0 else 0,
            "total_pnl_in_period": cal.total_pnl,
        }
    
    def record_trade_outcome(
        self,
        symbol: str,
        pnl: float,
        is_win: bool,
    ):
        """
        Record a trade outcome for real-time tracking.
        
        Note: This updates in-memory state. Full calibration runs daily.
        """
        cal = self.get_calibration(symbol)
        cal.total_trades += 1
        cal.total_pnl += pnl
        if is_win:
            cal.winning_trades += 1
    
    def get_status(self) -> Dict[str, Any]:
        """Get current calibration status for all symbols."""
        status = {}
        
        for symbol, cal in self.calibrations.items():
            win_rate = cal.winning_trades / cal.total_trades if cal.total_trades > 0 else 0
            
            status[symbol] = {
                "min_confidence": cal.min_confidence,
                "min_signal_strength": cal.min_signal_strength,
                "max_iv": cal.max_iv_for_buying,
                "trades": cal.total_trades,
                "win_rate": f"{win_rate:.0%}",
                "pnl": f"Rs {cal.total_pnl:+,.0f}",
                "calibrations": cal.calibration_count,
            }
        
        return status


# Singleton
_calibrator = None

def get_calibrator() -> Calibrator:
    """Get or create calibrator singleton."""
    global _calibrator
    if _calibrator is None:
        _calibrator = Calibrator()
    return _calibrator


# Test
if __name__ == "__main__":
    calibrator = get_calibrator()
    
    # Show current status
    print("Current calibration status:")
    status = calibrator.get_status()
    for symbol, data in status.items():
        print(f"\n{symbol}:")
        for key, value in data.items():
            print(f"  {key}: {value}")
    
    # Run calibration
    print("\nRunning calibration...")
    result = calibrator.run_daily_calibration()
