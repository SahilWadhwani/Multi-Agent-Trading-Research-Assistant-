"""
Market Hours Checker for NSE/BSE.
Controls when the agent should be active.
"""

import time
from datetime import datetime, timedelta
from typing import Tuple, Optional
import pytz


class MarketHoursChecker:
    """
    Checks and manages market hours for Indian markets (NSE/BSE).
    
    Market Timings (IST):
    - Pre-open: 9:00 AM - 9:15 AM
    - Normal Trading: 9:15 AM - 3:30 PM
    - Closing Session: 3:30 PM - 4:00 PM (limited)
    - Market Closed: After 4:00 PM and before 9:00 AM
    
    Holidays are not automatically tracked - add them manually.
    """
    
    IST = pytz.timezone('Asia/Kolkata')
    
    # NSE/BSE 2024-2025 Market Holidays (update as needed)
    MARKET_HOLIDAYS = [
        "2024-01-26",  # Republic Day
        "2024-03-08",  # Maha Shivaratri
        "2024-03-25",  # Holi
        "2024-03-29",  # Good Friday
        "2024-04-11",  # Id-Ul-Fitr
        "2024-04-14",  # Dr. Ambedkar Jayanti
        "2024-04-17",  # Ram Navami
        "2024-04-21",  # Mahavir Jayanti
        "2024-05-01",  # Maharashtra Day
        "2024-05-23",  # Buddha Purnima
        "2024-06-17",  # Bakri Id
        "2024-07-17",  # Muharram
        "2024-08-15",  # Independence Day
        "2024-10-02",  # Mahatma Gandhi Jayanti
        "2024-10-12",  # Dussehra
        "2024-10-31",  # Diwali-Laxmi Pujan
        "2024-11-01",  # Diwali-Balipratipada
        "2024-11-15",  # Guru Nanak Jayanti
        "2024-12-25",  # Christmas
        # 2025 holidays
        "2025-01-26",  # Republic Day
        "2025-02-26",  # Maha Shivaratri
        "2025-03-14",  # Holi
        "2025-04-14",  # Dr. Ambedkar Jayanti
        "2025-04-18",  # Good Friday
        "2025-05-01",  # Maharashtra Day
        "2025-08-15",  # Independence Day
        "2025-10-02",  # Gandhi Jayanti
        "2025-12-25",  # Christmas
        # 2026 holidays
        "2026-01-26",  # Republic Day
        "2026-03-10",  # Holi
        "2026-04-03",  # Good Friday
        "2026-04-14",  # Dr. Ambedkar Jayanti
        "2026-05-01",  # Maharashtra Day
        "2026-08-15",  # Independence Day
        "2026-10-02",  # Gandhi Jayanti
        "2026-10-20",  # Dussehra
        "2026-11-07",  # Diwali
        "2026-12-25",  # Christmas
    ]
    
    def __init__(self):
        self.holidays = set(self.MARKET_HOLIDAYS)
    
    def now_ist(self) -> datetime:
        """Get current time in IST."""
        return datetime.now(self.IST)
    
    def is_holiday(self, date: Optional[datetime] = None) -> bool:
        """Check if given date is a market holiday."""
        if date is None:
            date = self.now_ist()
        return date.strftime("%Y-%m-%d") in self.holidays
    
    def is_weekend(self, date: Optional[datetime] = None) -> bool:
        """Check if given date is a weekend."""
        if date is None:
            date = self.now_ist()
        return date.weekday() >= 5
    
    def is_trading_day(self, date: Optional[datetime] = None) -> bool:
        """Check if given date is a valid trading day."""
        if date is None:
            date = self.now_ist()
        return not self.is_weekend(date) and not self.is_holiday(date)
    
    def get_market_status(self) -> Tuple[str, str, bool]:
        """
        Get comprehensive market status.
        
        Returns:
            Tuple of (status, message, should_trade)
            - status: PRE_MARKET, OPEN, CLOSING, CLOSED, WEEKEND, HOLIDAY
            - message: Human readable message
            - should_trade: Whether agent should actively trade
        """
        now = self.now_ist()
        
        # Check weekend
        if self.is_weekend(now):
            day_name = now.strftime("%A")
            return "WEEKEND", f"Market closed: {day_name}", False
        
        # Check holiday
        if self.is_holiday(now):
            return "HOLIDAY", "Market closed: Trading holiday", False
        
        # Define time boundaries
        pre_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        closing_session_end = now.replace(hour=16, minute=0, second=0, microsecond=0)
        
        # Before pre-open
        if now < pre_open:
            time_to_open = pre_open - now
            hours, remainder = divmod(time_to_open.seconds, 3600)
            minutes = remainder // 60
            return "CLOSED", f"Market opens in {hours}h {minutes}m", False
        
        # Pre-open session
        if pre_open <= now < market_open:
            time_to_trading = market_open - now
            minutes = time_to_trading.seconds // 60
            return "PRE_MARKET", f"Pre-open session. Trading starts in {minutes}m", False
        
        # Normal trading hours
        if market_open <= now < market_close:
            time_to_close = market_close - now
            hours, remainder = divmod(time_to_close.seconds, 3600)
            minutes = remainder // 60
            return "OPEN", f"Market OPEN. Closes in {hours}h {minutes}m", True
        
        # Closing session
        if market_close <= now < closing_session_end:
            return "CLOSING", "Closing session - limited trading", False
        
        # After hours
        return "CLOSED", "Market closed for today", False
    
    def time_until_market_open(self) -> timedelta:
        """Calculate time until next market opening."""
        now = self.now_ist()
        
        # Find next trading day
        check_date = now
        while True:
            market_open = check_date.replace(hour=9, minute=15, second=0, microsecond=0)
            
            if check_date.date() == now.date() and now >= market_open:
                # Already past opening today, check tomorrow
                check_date += timedelta(days=1)
                continue
            
            if self.is_trading_day(check_date):
                return market_open - now
            
            check_date += timedelta(days=1)
            
            # Safety check - don't loop forever
            if (check_date - now).days > 10:
                return timedelta(days=1)
    
    def should_agent_run(self) -> Tuple[bool, str]:
        """
        Determine if the agent should be actively running.
        
        Returns:
            Tuple of (should_run, reason)
        """
        status, message, should_trade = self.get_market_status()
        
        if should_trade:
            return True, f"Market is {status}: {message}"
        
        # Even when market is closed, agent might want to analyze
        if status == "PRE_MARKET":
            return True, f"Pre-market analysis mode: {message}"
        
        return False, f"Agent paused: {message}"
    
    def wait_for_market_open(self, check_interval: int = 60):
        """
        Block until market opens.
        
        Args:
            check_interval: Seconds between checks
        """
        while True:
            status, message, should_trade = self.get_market_status()
            
            if should_trade:
                print(f"\n✓ {message}")
                return
            
            time_to_open = self.time_until_market_open()
            
            if time_to_open.total_seconds() < check_interval:
                # Almost time, check more frequently
                print(f"⏳ Market opening soon... ({message})")
                time.sleep(min(time_to_open.total_seconds(), 10))
            else:
                print(f"💤 {message}. Sleeping for {check_interval}s...")
                time.sleep(check_interval)
    
    def get_status_display(self) -> dict:
        """Get status information for dashboard display."""
        now = self.now_ist()
        status, message, should_trade = self.get_market_status()
        
        return {
            "current_time_ist": now.strftime("%Y-%m-%d %H:%M:%S IST"),
            "status": status,
            "message": message,
            "is_trading_hours": should_trade,
            "is_trading_day": self.is_trading_day(now),
            "is_holiday": self.is_holiday(now),
            "is_weekend": self.is_weekend(now),
        }


# Singleton instance
_checker = None

def get_market_checker() -> MarketHoursChecker:
    """Get or create the market hours checker singleton."""
    global _checker
    if _checker is None:
        _checker = MarketHoursChecker()
    return _checker


if __name__ == "__main__":
    checker = MarketHoursChecker()
    status = checker.get_status_display()
    print("\n=== Market Status ===")
    for key, value in status.items():
        print(f"  {key}: {value}")
