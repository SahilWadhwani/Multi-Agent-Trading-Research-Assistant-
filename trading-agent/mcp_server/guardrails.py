"""
HARDCODED TRADING GUARDRAILS
These rules CANNOT be overridden by the AI agent.
They exist to protect capital and enforce risk management.
"""

import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass
from enum import Enum
import pytz

# Add parent path for database imports  
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class GuardrailViolation(Exception):
    """Raised when a trade violates guardrails."""
    pass


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class TradeValidationResult:
    """Result of trade validation."""
    is_valid: bool
    risk_level: RiskLevel
    message: str
    details: Dict[str, Any]


class TradingGuardrails:
    """
    IMMUTABLE risk management rules.
    The AI cannot modify or bypass these checks.
    """
    
    # ============== HARDCODED LIMITS ==============
    # These values are intentionally NOT configurable via .env
    # to prevent the AI from manipulating them
    
    MAX_POSITION_PERCENT = 20.0  # Max 20% of available margin per trade (equity)
    MAX_DAILY_TRADES = 50  # Maximum trades per day
    MAX_DAILY_LOSS_PERCENT = 8.0  # Stop trading if down 8% for the day (increased breathing space)
    MIN_TRADE_VALUE = 100  # Minimum trade value in INR
    MAX_TRADE_VALUE = 10000  # Maximum single trade value in INR (equity)
    
    # ============== BLOCKED ACTIONS ==============
    BLOCKED_ACTIONS = [
        "add_funds",
        "withdraw_funds",
        "bank_transfer",
        "modify_guardrails",
    ]
    
    def __init__(self, available_margin: float = 0, daily_trades: int = 0, daily_pnl: float = 0):
        self.available_margin = available_margin
        self.daily_trades = daily_trades
        self.daily_pnl = daily_pnl
        self.daily_pnl_start = 0  # Portfolio value at day start
    
    def update_context(
        self,
        available_margin: Optional[float] = None,
        daily_trades: Optional[int] = None,
        daily_pnl: Optional[float] = None,
    ):
        """Update the context for validation."""
        if available_margin is not None:
            self.available_margin = available_margin
        if daily_trades is not None:
            self.daily_trades = daily_trades
        if daily_pnl is not None:
            self.daily_pnl = daily_pnl
    
    def validate_trade(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        product_type: str = "INTRADAY",
    ) -> TradeValidationResult:
        """
        Validate a trade against all guardrails.
        Returns validation result with details.
        
        CRITICAL: This function must be called before ANY trade execution.
        """
        trade_value = quantity * price
        violations = []
        risk_level = RiskLevel.LOW
        
        # === CHECK 1: Position Size Limit (20% rule) ===
        if self.available_margin > 0:
            position_percent = (trade_value / self.available_margin) * 100
            if position_percent > self.MAX_POSITION_PERCENT:
                violations.append(
                    f"REJECTED: Trade value (₹{trade_value:,.2f}) exceeds {self.MAX_POSITION_PERCENT}% "
                    f"of available margin (₹{self.available_margin:,.2f}). "
                    f"Max allowed: ₹{self.available_margin * self.MAX_POSITION_PERCENT / 100:,.2f}"
                )
                risk_level = RiskLevel.CRITICAL
        
        # === CHECK 2: Daily Trade Limit ===
        if self.daily_trades >= self.MAX_DAILY_TRADES:
            violations.append(
                f"REJECTED: Daily trade limit reached ({self.MAX_DAILY_TRADES} trades). "
                "Trading paused until next session."
            )
            risk_level = RiskLevel.CRITICAL
        
        # === CHECK 3: Daily Loss Limit ===
        if self.daily_pnl_start > 0:
            loss_percent = (self.daily_pnl / self.daily_pnl_start) * 100
            if loss_percent <= -self.MAX_DAILY_LOSS_PERCENT:
                violations.append(
                    f"REJECTED: Daily loss limit reached ({loss_percent:.2f}%). "
                    "Trading paused to protect capital."
                )
                risk_level = RiskLevel.CRITICAL
        
        # === CHECK 4: Trade Value Bounds ===
        if trade_value < self.MIN_TRADE_VALUE:
            violations.append(
                f"REJECTED: Trade value (₹{trade_value:,.2f}) below minimum (₹{self.MIN_TRADE_VALUE:,.2f})."
            )
        
        if trade_value > self.MAX_TRADE_VALUE:
            violations.append(
                f"REJECTED: Trade value (₹{trade_value:,.2f}) exceeds maximum (₹{self.MAX_TRADE_VALUE:,.2f})."
            )
            risk_level = RiskLevel.CRITICAL
        
        # === CHECK 5: Basic Validation ===
        if quantity <= 0:
            violations.append("REJECTED: Quantity must be positive.")
        
        if price <= 0:
            violations.append("REJECTED: Price must be positive.")
        
        if side.upper() not in ["BUY", "SELL"]:
            violations.append(f"REJECTED: Invalid side '{side}'. Must be BUY or SELL.")
        
        # === Determine Risk Level ===
        if not violations:
            if self.available_margin > 0:
                position_percent = (trade_value / self.available_margin) * 100
                if position_percent > 15:
                    risk_level = RiskLevel.MEDIUM
                elif position_percent > 10:
                    risk_level = RiskLevel.LOW
        
        # === Build Result ===
        is_valid = len(violations) == 0
        
        return TradeValidationResult(
            is_valid=is_valid,
            risk_level=risk_level,
            message="\n".join(violations) if violations else "Trade validated successfully",
            details={
                "trade_value": trade_value,
                "available_margin": self.available_margin,
                "position_percent": (trade_value / self.available_margin * 100) if self.available_margin > 0 else 0,
                "daily_trades": self.daily_trades,
                "violations": violations,
            }
        )
    
    def is_blocked_action(self, action: str) -> bool:
        """Check if an action is blocked (fund management, etc.)."""
        return action.lower() in self.BLOCKED_ACTIONS
    
    @staticmethod
    def is_market_hours() -> Tuple[bool, str]:
        """
        Check if current time is within NSE/BSE market hours.
        Market hours: 9:15 AM to 3:30 PM IST, Monday to Friday.
        
        Returns:
            Tuple of (is_open, message)
        """
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        
        # Check if weekend
        if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False, f"Market closed: Weekend ({now.strftime('%A')})"
        
        # Market timing
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0, tzinfo=ist)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0, tzinfo=ist)
        
        if now < market_open:
            time_to_open = market_open - now
            return False, f"Market opens in {time_to_open.seconds // 3600}h {(time_to_open.seconds % 3600) // 60}m"
        
        if now > market_close:
            return False, "Market closed for today (after 3:30 PM IST)"
        
        return True, f"Market is OPEN (closes at 3:30 PM IST)"
    
    @staticmethod  
    def is_pre_market_hours() -> bool:
        """Check if in pre-market session (9:00 AM - 9:15 AM IST)."""
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        pre_market_start = now.replace(hour=9, minute=0, second=0, microsecond=0, tzinfo=ist)
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0, tzinfo=ist)
        return pre_market_start <= now < market_open
    
    def get_max_trade_value(self) -> float:
        """Get the maximum allowed trade value based on current margin."""
        margin_limit = self.available_margin * (self.MAX_POSITION_PERCENT / 100)
        return min(margin_limit, self.MAX_TRADE_VALUE)
    
    def get_status_report(self) -> Dict[str, Any]:
        """Get current guardrail status."""
        is_open, market_status = self.is_market_hours()
        return {
            "market_status": market_status,
            "market_is_open": is_open,
            "available_margin": self.available_margin,
            "max_trade_value": self.get_max_trade_value(),
            "daily_trades_used": self.daily_trades,
            "daily_trades_remaining": max(0, self.MAX_DAILY_TRADES - self.daily_trades),
            "daily_pnl": self.daily_pnl,
            "limits": {
                "max_position_percent": self.MAX_POSITION_PERCENT,
                "max_daily_trades": self.MAX_DAILY_TRADES,
                "max_daily_loss_percent": self.MAX_DAILY_LOSS_PERCENT,
                "min_trade_value": self.MIN_TRADE_VALUE,
                "max_trade_value": self.MAX_TRADE_VALUE,
            }
        }


class OptionsGuardrails:
    """
    GUARDRAILS FOR OPTIONS BUYING.
    
    Optimized for Rs 15-20k capital buying index options.
    More flexible than equity but with mandatory stop-loss.
    """
    
    # ============== CAPITAL LIMITS ==============
    MAX_POSITION_PERCENT = 70.0    # Can use 70% of capital per trade
    MAX_TRADE_VALUE = 15000        # Max Rs 15,000 per trade
    MAX_DAILY_LOSS = 5000          # Stop if down Rs 5,000 (hard limit, scaled for 20k capital)
    MAX_DAILY_LOSS_PERCENT = 25.0  # Or 25% of starting capital
    MAX_DAILY_TRADES = 10          # Max 10 option trades per day
    
    # ============== OPTIONS-SPECIFIC ==============
    MAX_PREMIUM_PER_LOT = 300      # Don't buy options > Rs 300 premium
    MIN_PREMIUM_PER_LOT = 15       # Too cheap = too risky (avoid <15)
    MIN_DAYS_TO_EXPIRY = 1         # Don't buy on expiry day unless intraday
    MAX_DAYS_TO_EXPIRY = 30        # Don't buy too far dated (theta decay)
    
    # Allowed instruments
    ALLOWED_INSTRUMENTS = ["NIFTY", "BANKNIFTY", "FINNIFTY"]
    
    # Lot sizes for reference
    LOT_SIZES = {
        "NIFTY": 65,
        "BANKNIFTY": 30,
        "FINNIFTY": 40,
    }
    
    # ============== MANDATORY RISK MANAGEMENT ==============
    # REALISTIC TARGETS for intraday options buying
    MANDATORY_STOP_LOSS = True     # MUST set SL on every trade
    DEFAULT_STOP_LOSS_PCT = 25     # Default: exit if option loses 25%
    MAX_STOP_LOSS_PCT = 35         # Cannot set SL wider than 35%
    DEFAULT_TARGET_PCT = 30        # Default: book profit at 30% gain
    TRAIL_AFTER_PCT = 15           # Start trailing after 15% profit
    MIN_RISK_REWARD = 1.0          # Minimum 1:1 risk-reward
    
    # ============== TIME RULES ==============
    # INTRADAY ONLY for options buying (overnight = theta decay + gap risk)
    INTRADAY_ONLY = True           # MUST exit same day
    INTRADAY_SQUARE_OFF = "15:10"  # Auto exit 20 mins before close
    NO_TRADE_FIRST_MIN = 15        # Avoid first 15 minutes volatility
    NO_TRADE_LAST_MIN = 20         # Avoid last 20 minutes (exit time)
    EXPIRY_DAY_SQUARE_OFF = "14:30"  # Earlier exit on expiry day
    NO_TRADE_BEFORE_EXPIRY_HOURS = 2  # Don't enter 2 hours before expiry
    
    # ============== BLOCKED ACTIONS ==============
    BLOCKED_ACTIONS = [
        "sell_options",            # No naked option selling (unlimited risk)
        "write_options",
        "add_funds",
        "withdraw_funds",
        "bank_transfer",
    ]
    
    @classmethod
    def validate_option_trade(
        cls,
        symbol: str,
        strike: float,
        option_type: str,
        premium: float,
        lots: int,
        available_capital: float,
        daily_loss_so_far: float = 0,
        daily_trades_so_far: int = 0,
        days_to_expiry: int = 7,
        stop_loss_pct: float = None,
    ) -> Dict[str, Any]:
        """
        Validate an options trade against guardrails.
        
        Returns:
            {
                "valid": bool,
                "violations": List[str],
                "warnings": List[str],
                "adjusted_lots": int,  # Suggested lots if original too high
            }
        """
        violations = []
        warnings = []
        adjusted_lots = lots
        
        lot_size = cls.LOT_SIZES.get(symbol.upper(), 50)
        trade_value = premium * lot_size * lots
        
        # Check 1: Instrument allowed
        if symbol.upper() not in cls.ALLOWED_INSTRUMENTS:
            violations.append(f"Symbol {symbol} not in allowed list: {cls.ALLOWED_INSTRUMENTS}")
        
        # Check 2: Premium range
        if premium > cls.MAX_PREMIUM_PER_LOT:
            violations.append(f"Premium Rs {premium} exceeds max Rs {cls.MAX_PREMIUM_PER_LOT}")
        if premium < cls.MIN_PREMIUM_PER_LOT:
            warnings.append(f"Premium Rs {premium} is very low - high risk of total loss")
        
        # Check 3: Trade value vs capital
        if trade_value > available_capital * (cls.MAX_POSITION_PERCENT / 100):
            max_value = available_capital * (cls.MAX_POSITION_PERCENT / 100)
            max_lots = int(max_value / (premium * lot_size))
            if max_lots >= 1:
                adjusted_lots = max_lots
                warnings.append(f"Reducing lots from {lots} to {adjusted_lots} (position size limit)")
            else:
                violations.append(f"Insufficient capital for even 1 lot")
        
        if trade_value > cls.MAX_TRADE_VALUE:
            violations.append(f"Trade value Rs {trade_value} exceeds max Rs {cls.MAX_TRADE_VALUE}")
        
        # Check 4: Daily loss limit
        if daily_loss_so_far >= cls.MAX_DAILY_LOSS:
            violations.append(f"Daily loss limit reached (Rs {daily_loss_so_far} >= Rs {cls.MAX_DAILY_LOSS})")
        
        # Check 5: Daily trade limit
        if daily_trades_so_far >= cls.MAX_DAILY_TRADES:
            violations.append(f"Daily trade limit reached ({daily_trades_so_far} >= {cls.MAX_DAILY_TRADES})")
        
        # Check 6: Days to expiry
        if days_to_expiry < cls.MIN_DAYS_TO_EXPIRY:
            warnings.append(f"Expiry day trading - high theta decay risk")
        if days_to_expiry > cls.MAX_DAYS_TO_EXPIRY:
            warnings.append(f"Far dated option - consider closer expiry")
        
        # Check 7: Stop loss requirement
        if cls.MANDATORY_STOP_LOSS:
            if stop_loss_pct is None:
                warnings.append(f"No stop loss set - using default {cls.DEFAULT_STOP_LOSS_PCT}%")
            elif stop_loss_pct > cls.MAX_STOP_LOSS_PCT:
                violations.append(f"Stop loss {stop_loss_pct}% too wide (max {cls.MAX_STOP_LOSS_PCT}%)")
        
        return {
            "valid": len(violations) == 0,
            "violations": violations,
            "warnings": warnings,
            "adjusted_lots": adjusted_lots,
            "trade_value": premium * lot_size * adjusted_lots,
        }
    
    @classmethod
    def is_trading_time_allowed(cls) -> Tuple[bool, str]:
        """Check if current time is good for trading."""
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        
        # Check market hours
        if now.weekday() >= 5:
            return False, "Market closed (weekend)"
        
        market_open = now.replace(hour=9, minute=15, second=0)
        market_close = now.replace(hour=15, minute=30, second=0)
        
        if now < market_open:
            return False, "Market not yet open"
        if now > market_close:
            return False, "Market closed"
        
        # Check first N minutes
        first_safe_time = market_open + timedelta(minutes=cls.NO_TRADE_FIRST_MIN)
        if now < first_safe_time:
            return False, f"Avoiding first {cls.NO_TRADE_FIRST_MIN} minutes"
        
        # Check last N minutes
        last_safe_time = market_close - timedelta(minutes=cls.NO_TRADE_LAST_MIN)
        if now > last_safe_time:
            return False, f"Avoiding last {cls.NO_TRADE_LAST_MIN} minutes"
        
        return True, "Trading time OK"
    
    @classmethod
    def get_status(cls, available_capital: float, daily_loss: float = 0, daily_trades: int = 0) -> Dict:
        """Get current guardrail status."""
        is_allowed, time_msg = cls.is_trading_time_allowed()
        
        return {
            "trading_allowed": is_allowed,
            "time_status": time_msg,
            "available_capital": available_capital,
            "max_trade_value": min(cls.MAX_TRADE_VALUE, available_capital * cls.MAX_POSITION_PERCENT / 100),
            "daily_loss_used": daily_loss,
            "daily_loss_remaining": max(0, cls.MAX_DAILY_LOSS - daily_loss),
            "daily_trades_used": daily_trades,
            "daily_trades_remaining": max(0, cls.MAX_DAILY_TRADES - daily_trades),
            "limits": {
                "max_position_pct": cls.MAX_POSITION_PERCENT,
                "max_daily_loss": cls.MAX_DAILY_LOSS,
                "max_daily_trades": cls.MAX_DAILY_TRADES,
                "max_premium": cls.MAX_PREMIUM_PER_LOT,
                "mandatory_stop_loss": cls.MANDATORY_STOP_LOSS,
                "default_stop_loss_pct": cls.DEFAULT_STOP_LOSS_PCT,
            }
        }
    
    # Blocked actions - AI can NEVER do these
    BLOCKED_ACTIONS = [
        "add_funds",
        "withdraw_funds", 
        "bank_transfer",
        "modify_bank_account",
        "change_credentials",
    ]
    
# Singleton instance
_guardrails_instance = None

def get_guardrails() -> TradingGuardrails:
    """Get or create the guardrails singleton."""
    global _guardrails_instance
    if _guardrails_instance is None:
        _guardrails_instance = TradingGuardrails()
    return _guardrails_instance


# ============== CONVENIENCE EXPORTS ==============
# These provide easy access to guardrail values and functions

GUARDRAILS = {
    "max_position_percent": TradingGuardrails.MAX_POSITION_PERCENT,
    "max_daily_trades": TradingGuardrails.MAX_DAILY_TRADES,
    "max_daily_loss_percent": TradingGuardrails.MAX_DAILY_LOSS_PERCENT,
    "min_trade_value": TradingGuardrails.MIN_TRADE_VALUE,
    "max_trade_value": TradingGuardrails.MAX_TRADE_VALUE,
    "blocked_actions": TradingGuardrails.BLOCKED_ACTIONS,
}

# Options-specific guardrails
OPTIONS_GUARDRAILS = {
    "max_position_percent": OptionsGuardrails.MAX_POSITION_PERCENT,
    "max_trade_value": OptionsGuardrails.MAX_TRADE_VALUE,
    "max_daily_loss": OptionsGuardrails.MAX_DAILY_LOSS,
    "max_daily_trades": OptionsGuardrails.MAX_DAILY_TRADES,
    "max_premium": OptionsGuardrails.MAX_PREMIUM_PER_LOT,
    "min_premium": OptionsGuardrails.MIN_PREMIUM_PER_LOT,
    "mandatory_stop_loss": OptionsGuardrails.MANDATORY_STOP_LOSS,
    "default_stop_loss_pct": OptionsGuardrails.DEFAULT_STOP_LOSS_PCT,
    "allowed_instruments": OptionsGuardrails.ALLOWED_INSTRUMENTS,
    "lot_sizes": OptionsGuardrails.LOT_SIZES,
}


def validate_options_trade(
    symbol: str,
    strike: float,
    option_type: str,
    premium: float,
    lots: int,
    available_capital: float,
    **kwargs,
) -> Dict[str, Any]:
    """Convenience function to validate an options trade."""
    return OptionsGuardrails.validate_option_trade(
        symbol=symbol,
        strike=strike,
        option_type=option_type,
        premium=premium,
        lots=lots,
        available_capital=available_capital,
        **kwargs,
    )


def validate_trade_risk(
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    available_margin: float = 0,
    daily_trades: int = 0,
    daily_pnl: float = 0,
) -> TradeValidationResult:
    """Convenience function to validate a trade."""
    guardrails = get_guardrails()
    guardrails.update_context(available_margin, daily_trades, daily_pnl)
    return guardrails.validate_trade(symbol, side, quantity, price)


def is_market_hours() -> bool:
    """Simple check if market is open."""
    is_open, _ = TradingGuardrails.is_market_hours()
    return is_open


def get_market_status() -> Tuple[bool, str]:
    """Get market status with message."""
    return TradingGuardrails.is_market_hours()
