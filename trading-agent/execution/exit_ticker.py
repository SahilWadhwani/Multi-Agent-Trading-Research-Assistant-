"""
Real-time SL/Target Exit Ticker.

Hooks into WebSocket price updates to check SL/target on EVERY tick.
Replaces 15-second exit_manager cycle for instant execution.

Flow:
1. WebSocket price update arrives
2. exit_ticker.on_price_update() called immediately
3. Check all open positions' SL/target
4. If hit: Execute exit order instantly (no 15-sec delay)
5. Scheduler's 15-sec cycle acts as backup safety check
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, Tuple

import pytz

from brain.position_tracker import get_position_tracker
from execution.price_hub import get_price_hub
from execution.runtime_safety import TradingMode, load_trading_mode
from data_feeds.fo_data_feed import get_fo_data_feed

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


# ════════════════════════════════════════════════════════════════════════════════
# SMART EXIT FRAMEWORK - Rules-based, adaptively optimized
# ════════════════════════════════════════════════════════════════════════════════


def get_target_for_regime(regime: str) -> float:
    """
    LAYER 1: Regime-aware base target
    Different regimes have different optimal profit targets
    """
    if regime is None:
        return 25.0
    
    regime_lower = str(regime).lower()
    
    if "strong_trend" in regime_lower or "trend" in regime_lower:
        return 30.0  # Trends run, let them go
    elif "breakout" in regime_lower:
        return 28.0  # Fast moves, capture before pullback
    elif "choppy" in regime_lower or "range" in regime_lower:
        return 15.0  # Consolidation, quick reversals
    elif "mean_revert" in regime_lower:
        return 10.0  # Reversals are immediate, get out quick
    else:
        return 25.0  # Default for unknown regime


def get_target_based_on_hold_time(base_target: float, hold_minutes: float) -> float:
    """
    LAYER 2: Time-based exit escalation
    As position ages, tighten the target (don't hold forever).
    Escalates downward: 100% -> 90% -> 70% -> 50% -> 0%
    """
    if hold_minutes < 60:
        return base_target  # First hour: Full target
    elif hold_minutes < 120:
        return base_target * 0.90  # Second hour: 90% of target
    elif hold_minutes < 180:
        return base_target * 0.70  # Third hour: 70% of target
    elif hold_minutes < 240:
        return base_target * 0.50  # Fourth hour: 50% of target
    else:
        return 0.0  # >4 hours: Must exit immediately


def get_stoploss_for_iv(iv_level: Optional[float]) -> float:
    """
    LAYER 3: Volatility-adaptive stoploss
    Adjust SL based on market volatility (IV).
    Higher IV = wider SL (allow whipsaw), Lower IV = tighter SL
    """
    if iv_level is None:
        return 20.0  # Default
    
    if iv_level < 15:
        return 15.0  # Low volatility: tight SL
    elif iv_level < 20:
        return 20.0  # Normal volatility: standard
    elif iv_level < 25:
        return 25.0  # Elevated volatility: wider
    else:
        return 30.0  # High volatility: very wide


def should_exit_on_smart_pullback(
    peak_profit_pct: float,
    current_profit_pct: float,
    hold_minutes: float,
    min_hold_for_pullback: float = 30,
) -> Tuple[bool, str]:
    """
    LAYER 4: Smart trailing stop with guardrails
    Exit if pullback meets certain conditions (not just random pullback).
    
    Returns: (should_exit: bool, reason: str)
    """
    
    # Rule 1: Only consider if in profit
    if current_profit_pct < 5:
        return False, ""  # Too small profit, don't exit
    
    # Rule 2: Calculate pullback magnitude
    if peak_profit_pct <= 0:
        return False, ""  # No peak yet
    
    pullback_pct = (peak_profit_pct - current_profit_pct) / peak_profit_pct
    
    # Rule 3: Ignore small pullbacks (noise)
    if pullback_pct < 0.03:  # < 3%
        return False, ""  # Just noise, ignore
    
    # Rule 4: Only consider if held long enough
    if hold_minutes < min_hold_for_pullback:
        return False, ""  # Too early, let trade breathe
    
    # Rule 5: Exit on deep pullback (always)
    if pullback_pct > 0.10:  # > 10%
        return True, f"deep_pullback_{pullback_pct*100:.1f}pct"
    
    # Rule 6: Exit on moderate pullback + aged position
    if pullback_pct > 0.05 and hold_minutes > 90:  # > 5% pullback + 90min held
        return True, f"aged_pullback_{pullback_pct*100:.1f}pct"
    
    return False, ""


def calculate_smart_target(
    entry_price: float,
    regime: Optional[str],
    hold_minutes: float,
    iv_level: Optional[float],
) -> float:
    """
    Combined smart target calculation integrating all layers.
    Returns: Target price (not percentage)
    """
    base_target_pct = get_target_for_regime(regime)
    time_adjusted_target_pct = get_target_based_on_hold_time(base_target_pct, hold_minutes)
    
    # Calculate target price
    target_price = entry_price * (1 + time_adjusted_target_pct / 100)
    return target_price


def calculate_smart_stoploss(
    entry_price: float,
    iv_level: Optional[float],
) -> float:
    """
    Calculate adaptive SL price based on IV.
    Returns: SL price (not percentage)
    """
    sl_pct = get_stoploss_for_iv(iv_level)
    sl_price = entry_price * (1 - sl_pct / 100)
    return sl_price


class ExitTicker:
    """
    Real-time exit checker triggered on every WebSocket price update.
    
    - Maintains a lock-safe map of position SL/target thresholds
    - On price update: Check all positions instantly
    - If SL/target hit: Execute exit immediately (async)
    - Prevents duplicate exits via position state tracking
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._position_thresholds: Dict[str, Dict] = {}  # decision_id -> {sl, target, qty, ...}
        self._exiting: set = set()  # decision_ids currently being exited
        self._exit_callback: Optional[Callable] = None
        self._enabled = False
        
    def enable(self) -> None:
        """Activate ticker and register with price hub."""
        if self._enabled:
            return
        
        try:
            hub = get_price_hub()
            hub.add_callback(self._on_price_update)
            self._enabled = True
            logger.info("ExitTicker enabled - real-time SL/target monitoring active")
        except Exception as e:
            logger.error("Failed to enable ExitTicker: %s", e)
    
    def disable(self) -> None:
        """Deactivate ticker."""
        self._enabled = False
        with self._lock:
            self._position_thresholds.clear()
            self._exiting.clear()
        logger.info("ExitTicker disabled")
    
    def register_position(
        self,
        decision_id: str,
        symbol: str,
        instrument_key: str,
        entry_price: float,
        sl_pct: float,
        target_pct: float,
        qty: int,
        lot_size: int,
    ) -> None:
        """
        Register an open position for SL/target monitoring.
        Called when a new trade is executed.
        
        Now tracks: entry_time, regime, peak_profit for smart exits
        """
        sl_price = entry_price * (1 - sl_pct / 100)
        target_price = entry_price * (1 + target_pct / 100)
        
        # Get current regime and IV for smart calculations
        try:
            feed = get_fo_data_feed()
            regime = feed.get_market_regime()
            iv_level = feed.get_iv_level(symbol)
        except Exception as e:
            logger.warning("Could not get market context for smart exits: %s", e)
            regime = None
            iv_level = None
        
        now = datetime.now(IST)
        
        with self._lock:
            self._position_thresholds[decision_id] = {
                "symbol": symbol,
                "instrument_key": instrument_key,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "target_price": target_price,
                "qty": qty,
                "lot_size": lot_size,
                "sl_pct": sl_pct,
                "target_pct": target_pct,
                # Smart exit tracking
                "entry_time": now,
                "peak_price": entry_price,
                "peak_profit_pct": 0.0,
                "regime": regime,
                "iv_level": iv_level,
            }
        
        logger.info(
            "ExitTicker registered: %s @ Rs %.2f | Regime: %s | IV: %.1f | "
            "Initial target: +%.1f%% (Rs %.2f) | SL: -%.1f%% (Rs %.2f)",
            symbol,
            entry_price,
            regime or "unknown",
            iv_level or 0,
            target_pct,
            target_price,
            sl_pct,
            sl_price,
        )
    
    def unregister_position(self, decision_id: str) -> None:
        """Remove position from monitoring (when closed)."""
        with self._lock:
            self._position_thresholds.pop(decision_id, None)
            self._exiting.discard(decision_id)
    
    def _on_price_update(self, instrument_key: str, ltp: float) -> None:
        """
        Called on EVERY WebSocket price tick.
        Checks if any SL/target thresholds are hit (with smart exit logic).
        
        MUST be fast - runs in WebSocket thread!
        Recalculates targets dynamically based on hold time + regime + IV
        """
        if not self._enabled:
            return
        
        with self._lock:
            # Get copy of current positions (to avoid holding lock during exit)
            positions_to_check = []
            for dec_id, info in list(self._position_thresholds.items()):
                if info["instrument_key"] == instrument_key:
                    if dec_id not in self._exiting:  # Skip if already exiting
                        positions_to_check.append((dec_id, info))
        
        # Check each position (outside lock)
        now = datetime.now(IST)
        
        for decision_id, info in positions_to_check:
            # Update peak price tracking
            if ltp > info["peak_price"]:
                info["peak_price"] = ltp
                info["peak_profit_pct"] = (ltp - info["entry_price"]) / info["entry_price"] * 100
            
            # Calculate current profit
            current_profit_pct = (ltp - info["entry_price"]) / info["entry_price"] * 100
            
            # Calculate hold time in minutes
            hold_minutes = (now - info["entry_time"]).total_seconds() / 60.0
            
            # ═══ SMART EXIT LOGIC ═══
            
            # EXIT 1: SL_HIT (fixed, never changes)
            if ltp <= info["sl_price"]:
                self._trigger_exit(decision_id, info, "SL_HIT", ltp)
                continue
            
            # EXIT 2: ADAPTIVE TARGET (smart, recalculated each tick)
            smart_target = calculate_smart_target(
                entry_price=info["entry_price"],
                regime=info["regime"],
                hold_minutes=hold_minutes,
                iv_level=info["iv_level"],
            )
            
            if ltp >= smart_target:
                self._trigger_exit(decision_id, info, "SMART_TARGET_HIT", ltp)
                continue
            
            # EXIT 3: SMART PULLBACK (intelligent trailing)
            should_pullback_exit, pullback_reason = should_exit_on_smart_pullback(
                peak_profit_pct=info["peak_profit_pct"],
                current_profit_pct=current_profit_pct,
                hold_minutes=hold_minutes,
            )
            
            if should_pullback_exit:
                self._trigger_exit(decision_id, info, f"SMART_PULLBACK_{pullback_reason}", ltp)
                continue
            
            # EXIT 4: HOLD TIME LIMIT (safety net, don't hold >4h)
            if hold_minutes > 240:
                self._trigger_exit(decision_id, info, "HOLD_TIME_LIMIT_4H", ltp)
                continue
            
            # EXIT 5: EOD FORCED (after market hours, 15:30 IST buffer = 15:25)
            current_time = now.time()
            if current_time >= IST.localize(datetime.now().replace(hour=15, minute=25)).time():
                if current_time <= IST.localize(datetime.now().replace(hour=15, minute=31)).time():
                    # We're in EOD window, force close
                    self._trigger_exit(decision_id, info, "EOD_FORCED_CLOSE", ltp)
                    continue
            
            # LOG: Position status update (only on regime/target changes)
            if hold_minutes % 30 < 1 or hold_minutes < 1:  # Every 30 min approx
                logger.debug(
                    "ExitTicker monitoring: %s @ Rs %.2f | "
                    "Profit: %.2f%% | Peak: %.2f%% | "
                    "SmartTarget: +%.1f%% (Rs %.2f) | Hold: %.0f min",
                    info["symbol"],
                    ltp,
                    current_profit_pct,
                    info["peak_profit_pct"],
                    (smart_target - info["entry_price"]) / info["entry_price"] * 100,
                    smart_target,
                    hold_minutes,
                )
    
    def _trigger_exit(
        self,
        decision_id: str,
        info: Dict,
        exit_reason: str,
        current_price: float,
    ) -> None:
        """
        Execute exit for a position that hit SL/target.
        Runs async to avoid blocking WebSocket thread.
        """
        # Calculate exit profit for logging
        profit_pct = (current_price - info["entry_price"]) / info["entry_price"] * 100
        hold_time = (datetime.now(IST) - info["entry_time"]).total_seconds() / 60.0
        
        # Mark as exiting to prevent duplicate exits
        with self._lock:
            if decision_id in self._exiting:
                return
            self._exiting.add(decision_id)
        
        # Log smart exit decision
        logger.info(
            "🎯 SmartExit: %s | Symbol: %s | Entry: Rs %.2f → Exit: Rs %.2f | "
            "Profit: %.2f%% | Peak: %.2f%% | Hold: %.0f min | Regime: %s | IV: %.1f",
            exit_reason,
            info["symbol"],
            info["entry_price"],
            current_price,
            profit_pct,
            info["peak_profit_pct"],
            hold_time,
            info.get("regime", "unknown"),
            info.get("iv_level", 0),
        )
        
        # Queue for async execution
        try:
            threading.Thread(
                target=self._execute_exit_async,
                args=(decision_id, info, exit_reason, current_price),
                daemon=True,
            ).start()
        except Exception as e:
            logger.error("Failed to queue exit for %s: %s", decision_id, e)
            with self._lock:
                self._exiting.discard(decision_id)
    
    def _execute_exit_async(
        self,
        decision_id: str,
        info: Dict,
        exit_reason: str,
        current_price: float,
    ) -> None:
        """
        Async exit execution (runs in background thread).
        Places actual SELL order via broker.
        """
        try:
            mode = load_trading_mode()
            use_broker_exits = mode in (
                TradingMode.MICRO_LIVE,
                TradingMode.LIVE,
            )
            
            if not use_broker_exits:
                # Paper/shadow: close local state immediately at the tick price.
                logger.info(
                    "ExitTicker (paper): %s exiting %s @ %s (%s)",
                    exit_reason,
                    info["symbol"],
                    current_price,
                    decision_id[:8],
                )
                tracker = get_position_tracker()
                pos = self._find_open_position(tracker, decision_id)
                if pos:
                    tracker.close_position_record(
                        pos,
                        exit_price=current_price,
                        exit_reason=f"{exit_reason.lower()}_exit_ticker_paper",
                    )
                self.unregister_position(decision_id)
                return
            
            # Live trading: place actual SELL order
            logger.warning(
                "⚡ ExitTicker LIVE EXIT: %s | %s @ Rs %.2f | SL=%.2f | Target=%.2f | %s",
                exit_reason,
                info["symbol"],
                info["entry_price"],
                info["sl_price"],
                info["target_price"],
                decision_id[:8],
            )
            
            try:
                from database.operations import is_token_valid
                from mcp_server.upstox_client import get_upstox_client
                
                if is_token_valid():
                    client = get_upstox_client()
                    if client.is_authenticated():
                        tracker = get_position_tracker()
                        pos = self._find_open_position(tracker, decision_id)
                        if not pos:
                            logger.error("ExitTicker could not find local position %s", decision_id)
                            return

                        from execution.exit_manager import exit_position_via_broker_safely

                        summary = exit_position_via_broker_safely(
                            tracker=tracker,
                            pos=pos,
                            exit_reason=f"{exit_reason.lower()}_exit_ticker",
                            client=client,
                            mode=mode.value,
                        )
                        if summary:
                            logger.info(
                                "ExitTicker exit confirmed: %s | Broker Order: %s",
                                decision_id[:8],
                                summary.get("broker_order_id"),
                            )
                            self.unregister_position(decision_id)
                            return
                        logger.error("ExitTicker safe exit did not confirm fill: %s", decision_id[:8])
            except Exception as e:
                logger.error("ExitTicker broker execution error: %s", e)
        
        except Exception as e:
            logger.error("ExitTicker exit execution failed: %s", e)
        finally:
            # Clean up exiting flag
            with self._lock:
                self._exiting.discard(decision_id)

    def _find_open_position(self, tracker, decision_id: str):
        """Lookup a local open position by decision id."""
        for pos in tracker.get_open_positions():
            if pos.decision_id == decision_id:
                return pos
        return None


# Global singleton
_ticker: Optional[ExitTicker] = None
_ticker_lock = threading.Lock()


def get_exit_ticker() -> ExitTicker:
    """Get or create the global ExitTicker singleton."""
    global _ticker
    if _ticker is None:
        with _ticker_lock:
            if _ticker is None:
                _ticker = ExitTicker()
    return _ticker


def enable_exit_ticker() -> None:
    """Activate real-time exit monitoring."""
    ticker = get_exit_ticker()
    ticker.enable()


def disable_exit_ticker() -> None:
    """Deactivate real-time exit monitoring."""
    ticker = get_exit_ticker()
    ticker.disable()
