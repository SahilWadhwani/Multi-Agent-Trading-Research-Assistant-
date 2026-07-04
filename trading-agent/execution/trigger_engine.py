"""
Trigger Engine for Fast Options Execution.

Pre-computed triggers execute instantly without waiting for LLM analysis.
LLM sets up triggers before/during market, execution is sub-second.
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
import threading
import queue
import time
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TriggerType(Enum):
    PRICE_ABOVE = "price_above"      # Trigger when price > threshold
    PRICE_BELOW = "price_below"      # Trigger when price < threshold
    PRICE_CROSS_UP = "price_cross_up"    # Trigger when price crosses above
    PRICE_CROSS_DOWN = "price_cross_down"  # Trigger when price crosses below
    PERCENT_MOVE_UP = "pct_move_up"    # Trigger on X% up move
    PERCENT_MOVE_DOWN = "pct_move_down"  # Trigger on X% down move
    TIME_BASED = "time_based"        # Trigger at specific time


class TriggerStatus(Enum):
    PENDING = "pending"
    TRIGGERED = "triggered"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class TradeTrigger:
    """A trading trigger that executes when conditions are met."""
    trigger_id: str
    symbol: str
    trigger_type: TriggerType
    
    # Trigger conditions
    threshold: float  # Price level or percentage
    reference_price: Optional[float] = None  # For % moves
    trigger_time: Optional[datetime] = None  # For time-based
    
    # Trade details (execute when triggered)
    strike: float = 0
    option_type: str = "CE"  # CE or PE
    lots: int = 1
    stop_loss_pct: float = 40
    target_pct: float = 50
    
    # State
    status: TriggerStatus = TriggerStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    triggered_at: Optional[datetime] = None
    expiry: Optional[datetime] = None  # Auto-cancel after this time
    
    # Reasoning (from LLM)
    reasoning: str = ""
    confidence: float = 0.5


@dataclass
class ExecutedOrder:
    """Record of an executed order from trigger."""
    order_id: str
    trigger_id: str
    symbol: str
    strike: float
    option_type: str
    lots: int
    entry_price: float
    stop_loss: float
    target: float
    executed_at: datetime
    status: str = "OPEN"


class TriggerEngine:
    """
    Manages trading triggers for fast execution.
    
    Workflow:
    1. LLM analyzes and sets triggers pre-market or during
    2. Real-time feed updates prices
    3. Engine checks triggers instantly (no LLM call)
    4. When triggered, executes immediately
    
    This provides sub-second execution while still using
    LLM intelligence for strategy decisions.
    """
    
    def __init__(self):
        self.triggers: Dict[str, TradeTrigger] = {}
        self.executed_orders: List[ExecutedOrder] = []
        self.price_cache: Dict[str, float] = {}  # symbol -> last price
        self.trigger_counter = 0
        
        # Callbacks
        self._on_trigger_fired: Optional[Callable] = None
        self._on_order_executed: Optional[Callable] = None
        
        # Threading
        self._lock = threading.Lock()
        self._event_queue = queue.Queue()
    
    def create_trigger(
        self,
        symbol: str,
        trigger_type: TriggerType,
        threshold: float,
        strike: float,
        option_type: str,
        lots: int = 1,
        stop_loss_pct: float = 40,
        target_pct: float = 50,
        reference_price: float = None,
        expiry_hours: float = 8,  # Default: expire end of day
        reasoning: str = "",
        confidence: float = 0.5,
    ) -> TradeTrigger:
        """
        Create a new trading trigger.
        
        Args:
            symbol: NIFTY, BANKNIFTY, etc.
            trigger_type: When to trigger (price above, below, etc.)
            threshold: Price level or % move
            strike: Option strike to trade
            option_type: CE or PE
            lots: Number of lots
            stop_loss_pct: Stop loss percentage
            target_pct: Target percentage
            reference_price: Reference for % calculations
            expiry_hours: Trigger expires after this many hours
            reasoning: LLM's reasoning for this trigger
            confidence: LLM's confidence (0-1)
        
        Returns:
            Created TradeTrigger object
        """
        with self._lock:
            self.trigger_counter += 1
            trigger_id = f"TRG_{datetime.now().strftime('%Y%m%d_%H%M')}_{self.trigger_counter}"
            
            expiry = datetime.now() + timedelta(hours=expiry_hours) if expiry_hours else None
            
            trigger = TradeTrigger(
                trigger_id=trigger_id,
                symbol=symbol.upper(),
                trigger_type=trigger_type,
                threshold=threshold,
                reference_price=reference_price or self.price_cache.get(symbol.upper(), 0),
                strike=strike,
                option_type=option_type.upper(),
                lots=lots,
                stop_loss_pct=stop_loss_pct,
                target_pct=target_pct,
                expiry=expiry,
                reasoning=reasoning,
                confidence=confidence,
            )
            
            self.triggers[trigger_id] = trigger
            
            print(f"   Trigger created: {trigger_id}")
            print(f"   {trigger_type.value}: {symbol} @ {threshold}")
            print(f"   Trade: {strike} {option_type} x{lots} lots")
            
            return trigger
    
    def update_price(self, symbol: str, price: float):
        """
        Update price and check triggers.
        
        This is called by the WebSocket feed on every price update.
        Must be fast - no LLM calls here!
        """
        symbol_upper = symbol.upper()
        old_price = self.price_cache.get(symbol_upper)
        self.price_cache[symbol_upper] = price
        
        # Check all pending triggers for this symbol
        with self._lock:
            for trigger_id, trigger in list(self.triggers.items()):
                if trigger.symbol != symbol_upper:
                    continue
                if trigger.status != TriggerStatus.PENDING:
                    continue
                
                # Check expiry
                if trigger.expiry and datetime.now() > trigger.expiry:
                    trigger.status = TriggerStatus.EXPIRED
                    continue
                
                # Check trigger condition
                if self._check_trigger(trigger, price, old_price):
                    trigger.status = TriggerStatus.TRIGGERED
                    trigger.triggered_at = datetime.now()
                    
                    # Queue for execution
                    self._event_queue.put(trigger)
                    
                    print(f"   TRIGGERED: {trigger_id} @ {price}")
    
    def _check_trigger(self, trigger: TradeTrigger, price: float, old_price: Optional[float]) -> bool:
        """Check if trigger condition is met."""
        if trigger.trigger_type == TriggerType.PRICE_ABOVE:
            return price > trigger.threshold
        
        elif trigger.trigger_type == TriggerType.PRICE_BELOW:
            return price < trigger.threshold
        
        elif trigger.trigger_type == TriggerType.PRICE_CROSS_UP:
            if old_price is None:
                return False
            return old_price <= trigger.threshold < price
        
        elif trigger.trigger_type == TriggerType.PRICE_CROSS_DOWN:
            if old_price is None:
                return False
            return old_price >= trigger.threshold > price
        
        elif trigger.trigger_type == TriggerType.PERCENT_MOVE_UP:
            if not trigger.reference_price:
                return False
            pct_move = (price - trigger.reference_price) / trigger.reference_price * 100
            return pct_move >= trigger.threshold
        
        elif trigger.trigger_type == TriggerType.PERCENT_MOVE_DOWN:
            if not trigger.reference_price:
                return False
            pct_move = (trigger.reference_price - price) / trigger.reference_price * 100
            return pct_move >= trigger.threshold
        
        elif trigger.trigger_type == TriggerType.TIME_BASED:
            if not trigger.trigger_time:
                return False
            return datetime.now() >= trigger.trigger_time
        
        return False
    
    def process_triggered(self, execute_callback: Callable[[TradeTrigger], Optional[str]]) -> List[ExecutedOrder]:
        """
        Process triggered events and execute trades.
        
        Args:
            execute_callback: Function that actually places the order
                             Returns order_id on success, None on failure
        
        Returns:
            List of executed orders
        """
        executed = []
        
        while not self._event_queue.empty():
            try:
                trigger = self._event_queue.get_nowait()
                
                if trigger.status != TriggerStatus.TRIGGERED:
                    continue
                
                # Execute trade
                print(f"   Executing: {trigger.trigger_id}")
                order_id = execute_callback(trigger)
                
                if order_id:
                    trigger.status = TriggerStatus.EXECUTED
                    
                    # Calculate actual levels
                    entry_price = self._estimate_option_price(trigger)
                    stop_loss = entry_price * (1 - trigger.stop_loss_pct / 100)
                    target = entry_price * (1 + trigger.target_pct / 100)
                    
                    order = ExecutedOrder(
                        order_id=order_id,
                        trigger_id=trigger.trigger_id,
                        symbol=trigger.symbol,
                        strike=trigger.strike,
                        option_type=trigger.option_type,
                        lots=trigger.lots,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        target=target,
                        executed_at=datetime.now(),
                    )
                    
                    self.executed_orders.append(order)
                    executed.append(order)
                    
                    print(f"   Order placed: {order_id}")
                else:
                    print(f"   Order FAILED for trigger {trigger.trigger_id}")
                    trigger.status = TriggerStatus.PENDING  # Retry next tick
                    
            except queue.Empty:
                break
        
        return executed
    
    def _estimate_option_price(self, trigger: TradeTrigger) -> float:
        """Estimate option price for record keeping."""
        # This is just for logging - actual price comes from order execution
        spot = self.price_cache.get(trigger.symbol, trigger.strike)
        
        if trigger.option_type == "CE":
            # ATM call roughly 1% of spot
            base = spot * 0.01
        else:
            base = spot * 0.01
        
        # Adjust for moneyness
        diff = spot - trigger.strike
        if trigger.option_type == "CE":
            intrinsic = max(0, diff)
        else:
            intrinsic = max(0, -diff)
        
        return max(base, intrinsic + spot * 0.005)
    
    def cancel_trigger(self, trigger_id: str) -> bool:
        """Cancel a pending trigger."""
        with self._lock:
            if trigger_id in self.triggers:
                trigger = self.triggers[trigger_id]
                if trigger.status == TriggerStatus.PENDING:
                    trigger.status = TriggerStatus.CANCELLED
                    return True
        return False
    
    def cancel_all_triggers(self, symbol: str = None):
        """Cancel all pending triggers, optionally for a specific symbol."""
        with self._lock:
            for trigger in self.triggers.values():
                if trigger.status == TriggerStatus.PENDING:
                    if symbol is None or trigger.symbol == symbol.upper():
                        trigger.status = TriggerStatus.CANCELLED
    
    def get_pending_triggers(self, symbol: str = None) -> List[TradeTrigger]:
        """Get all pending triggers."""
        with self._lock:
            pending = [t for t in self.triggers.values() if t.status == TriggerStatus.PENDING]
            if symbol:
                pending = [t for t in pending if t.symbol == symbol.upper()]
            return pending
    
    def get_executed_orders(self, symbol: str = None) -> List[ExecutedOrder]:
        """Get all executed orders."""
        orders = self.executed_orders
        if symbol:
            orders = [o for o in orders if o.symbol == symbol.upper()]
        return orders
    
    def get_status(self) -> Dict[str, Any]:
        """Get engine status."""
        with self._lock:
            pending = len([t for t in self.triggers.values() if t.status == TriggerStatus.PENDING])
            triggered = len([t for t in self.triggers.values() if t.status == TriggerStatus.TRIGGERED])
            executed = len([t for t in self.triggers.values() if t.status == TriggerStatus.EXECUTED])
            
            return {
                "total_triggers": len(self.triggers),
                "pending": pending,
                "triggered": triggered,
                "executed": executed,
                "open_orders": len([o for o in self.executed_orders if o.status == "OPEN"]),
                "price_cache": dict(self.price_cache),
            }


# ============== LLM TRIGGER HELPERS ==============

def create_momentum_triggers(
    engine: TriggerEngine,
    symbol: str,
    current_price: float,
    atm_strike: float,
    threshold_pct: float = 0.3,
) -> List[TradeTrigger]:
    """
    Create momentum triggers for a symbol.
    
    Sets up triggers for:
    - Buy CE if price breaks above threshold
    - Buy PE if price breaks below threshold
    """
    triggers = []
    
    # Upside trigger
    up_threshold = current_price * (1 + threshold_pct / 100)
    triggers.append(engine.create_trigger(
        symbol=symbol,
        trigger_type=TriggerType.PRICE_CROSS_UP,
        threshold=up_threshold,
        strike=atm_strike + 50,  # Slightly OTM
        option_type="CE",
        lots=1,
        reasoning=f"Momentum: Buy CE if {symbol} crosses above {up_threshold:.0f}",
    ))
    
    # Downside trigger
    down_threshold = current_price * (1 - threshold_pct / 100)
    triggers.append(engine.create_trigger(
        symbol=symbol,
        trigger_type=TriggerType.PRICE_CROSS_DOWN,
        threshold=down_threshold,
        strike=atm_strike - 50,  # Slightly OTM
        option_type="PE",
        lots=1,
        reasoning=f"Momentum: Buy PE if {symbol} crosses below {down_threshold:.0f}",
    ))
    
    return triggers


def create_breakout_triggers(
    engine: TriggerEngine,
    symbol: str,
    resistance: float,
    support: float,
    atm_strike: float,
) -> List[TradeTrigger]:
    """
    Create breakout triggers based on support/resistance.
    """
    triggers = []
    
    # Resistance breakout
    triggers.append(engine.create_trigger(
        symbol=symbol,
        trigger_type=TriggerType.PRICE_ABOVE,
        threshold=resistance,
        strike=atm_strike,
        option_type="CE",
        lots=1,
        reasoning=f"Breakout: Buy CE on resistance break above {resistance:.0f}",
    ))
    
    # Support breakdown
    triggers.append(engine.create_trigger(
        symbol=symbol,
        trigger_type=TriggerType.PRICE_BELOW,
        threshold=support,
        strike=atm_strike,
        option_type="PE",
        lots=1,
        reasoning=f"Breakdown: Buy PE on support break below {support:.0f}",
    ))
    
    return triggers


# Singleton
_engine = None

def get_trigger_engine() -> TriggerEngine:
    """Get or create trigger engine singleton."""
    global _engine
    if _engine is None:
        _engine = TriggerEngine()
    return _engine
