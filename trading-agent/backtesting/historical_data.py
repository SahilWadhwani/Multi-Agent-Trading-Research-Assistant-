"""
Historical Data Manager for Backtesting.

Fetches and caches historical market data:
- Spot prices (OHLCV)
- Simulated option prices using Black-Scholes
- India VIX data for volatility estimation
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.upstox_client import get_upstox_client
from data_feeds.options_greeks import get_greeks_calculator, OptionType


@dataclass
class HistoricalCandle:
    """Single OHLCV candle."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class SimulatedOptionPrice:
    """Simulated option price for backtesting."""
    strike: float
    option_type: str  # CE or PE
    premium: float
    delta: float
    iv: float
    intrinsic: float
    time_value: float


class HistoricalDataManager:
    """
    Manages historical data for backtesting.
    
    Features:
    - Fetches spot price history from Upstox
    - Simulates option prices using Black-Scholes
    - Caches data locally for fast replay
    - Supports Nifty, BankNifty, FinNifty
    """
    
    # Index mapping
    INDEX_KEYS = {
        "NIFTY": "NSE_INDEX|Nifty 50",
        "BANKNIFTY": "NSE_INDEX|Nifty Bank",
        "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    }
    
    # Lot sizes
    LOT_SIZES = {
        "NIFTY": 50,
        "BANKNIFTY": 15,
        "FINNIFTY": 40,
    }
    
    # Strike intervals
    STRIKE_INTERVALS = {
        "NIFTY": 50,
        "BANKNIFTY": 100,
        "FINNIFTY": 50,
    }
    
    # Historical average IV by index
    AVERAGE_IV = {
        "NIFTY": 0.13,      # 13% typical IV
        "BANKNIFTY": 0.18,  # 18% typical IV (more volatile)
        "FINNIFTY": 0.15,   # 15% typical IV
    }
    
    def __init__(self, cache_dir: str = None):
        self.client = get_upstox_client()
        self.greeks_calc = get_greeks_calculator()
        self.cache_dir = cache_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data_cache"
        )
        os.makedirs(self.cache_dir, exist_ok=True)
        self._init_cache_db()
    
    def _init_cache_db(self):
        """Initialize SQLite cache database."""
        db_path = os.path.join(self.cache_dir, "historical_cache.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS spot_candles (
                symbol TEXT,
                timestamp TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                PRIMARY KEY (symbol, timestamp)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vix_data (
                timestamp TEXT PRIMARY KEY,
                vix_value REAL
            )
        """)
        
        conn.commit()
        conn.close()
    
    def _get_cache_db(self) -> sqlite3.Connection:
        """Get cache database connection."""
        db_path = os.path.join(self.cache_dir, "historical_cache.db")
        return sqlite3.connect(db_path)
    
    def fetch_spot_history(
        self,
        symbol: str,
        days: int = 60,
        interval: str = "day",
    ) -> List[HistoricalCandle]:
        """
        Fetch historical spot prices.
        
        Args:
            symbol: NIFTY, BANKNIFTY, FINNIFTY
            days: Number of days of history
            interval: "day", "1minute", "30minute"
        
        Returns:
            List of historical candles
        """
        symbol_upper = symbol.upper()
        instrument_key = self.INDEX_KEYS.get(symbol_upper)
        
        if not instrument_key:
            raise ValueError(f"Unknown symbol: {symbol}")
        
        # Check cache first
        cached = self._get_cached_candles(symbol_upper, days)
        if cached:
            return cached
        
        # Fetch from Upstox
        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        try:
            result = self.client.get_historical_candles(
                instrument_key=instrument_key,
                interval=interval,
                from_date=from_date,
                to_date=to_date,
            )
            
            if result.get("status") != "success":
                print(f"Warning: Could not fetch history for {symbol}")
                return self._generate_synthetic_data(symbol_upper, days)
            
            candles = []
            data = result.get("data", {}).get("candles", [])
            
            for candle in data:
                # Upstox format: [timestamp, open, high, low, close, volume, oi]
                if len(candle) >= 6:
                    candles.append(HistoricalCandle(
                        timestamp=datetime.fromisoformat(candle[0].replace("Z", "+00:00")),
                        open=candle[1],
                        high=candle[2],
                        low=candle[3],
                        close=candle[4],
                        volume=candle[5] or 0,
                    ))
            
            # Cache the data
            self._cache_candles(symbol_upper, candles)
            
            return sorted(candles, key=lambda x: x.timestamp)
            
        except Exception as e:
            print(f"Error fetching history: {e}")
            return self._generate_synthetic_data(symbol_upper, days)
    
    def _get_cached_candles(self, symbol: str, min_days: int) -> Optional[List[HistoricalCandle]]:
        """Get candles from cache if sufficient data exists."""
        conn = self._get_cache_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT timestamp, open, high, low, close, volume
            FROM spot_candles
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (symbol, min_days))
        
        rows = cursor.fetchall()
        conn.close()
        
        if len(rows) < min_days * 0.8:  # Need at least 80% of requested data
            return None
        
        candles = []
        for row in rows:
            candles.append(HistoricalCandle(
                timestamp=datetime.fromisoformat(row[0]),
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
            ))
        
        return sorted(candles, key=lambda x: x.timestamp)
    
    def _cache_candles(self, symbol: str, candles: List[HistoricalCandle]):
        """Cache candles to database."""
        conn = self._get_cache_db()
        cursor = conn.cursor()
        
        for candle in candles:
            cursor.execute("""
                INSERT OR REPLACE INTO spot_candles
                (symbol, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                candle.timestamp.isoformat(),
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            ))
        
        conn.commit()
        conn.close()
    
    def _generate_synthetic_data(self, symbol: str, days: int) -> List[HistoricalCandle]:
        """Generate synthetic historical data when real data unavailable."""
        print(f"Generating synthetic data for {symbol} ({days} days)")
        
        # Base prices
        base_prices = {
            "NIFTY": 24000,
            "BANKNIFTY": 51000,
            "FINNIFTY": 22000,
        }
        
        base_price = base_prices.get(symbol, 24000)
        candles = []
        
        import random
        random.seed(42)  # Reproducible for testing
        
        current_price = base_price
        
        for i in range(days):
            date = datetime.now() - timedelta(days=days - i)
            
            # Random daily movement (-2% to +2%)
            daily_return = random.gauss(0.0002, 0.012)  # Slight upward bias
            
            open_price = current_price
            close_price = current_price * (1 + daily_return)
            high_price = max(open_price, close_price) * (1 + abs(random.gauss(0, 0.005)))
            low_price = min(open_price, close_price) * (1 - abs(random.gauss(0, 0.005)))
            
            candles.append(HistoricalCandle(
                timestamp=date.replace(hour=15, minute=30),
                open=round(open_price, 2),
                high=round(high_price, 2),
                low=round(low_price, 2),
                close=round(close_price, 2),
                volume=random.randint(100000, 500000),
            ))
            
            current_price = close_price
        
        return candles
    
    def simulate_option_chain(
        self,
        symbol: str,
        spot_price: float,
        days_to_expiry: int,
        strikes_around_atm: int = 10,
        iv_override: float = None,
    ) -> Dict[str, List[SimulatedOptionPrice]]:
        """
        Simulate option chain prices using Black-Scholes.
        
        Args:
            symbol: NIFTY, BANKNIFTY, FINNIFTY
            spot_price: Current spot price
            days_to_expiry: Days until expiry
            strikes_around_atm: Number of strikes each side of ATM
            iv_override: Override default IV
        
        Returns:
            {"calls": [...], "puts": [...]}
        """
        symbol_upper = symbol.upper()
        strike_interval = self.STRIKE_INTERVALS.get(symbol_upper, 50)
        iv = iv_override or self.AVERAGE_IV.get(symbol_upper, 0.15)
        
        # Find ATM strike
        atm_strike = round(spot_price / strike_interval) * strike_interval
        
        time_to_expiry = days_to_expiry / 365.0
        
        calls = []
        puts = []
        
        for i in range(-strikes_around_atm, strikes_around_atm + 1):
            strike = atm_strike + (i * strike_interval)
            
            # Calculate call price
            call_greeks = self.greeks_calc.calculate_greeks(
                spot=spot_price,
                strike=strike,
                time_to_expiry=time_to_expiry,
                volatility=iv,
                option_type=OptionType.CALL,
            )
            
            calls.append(SimulatedOptionPrice(
                strike=strike,
                option_type="CE",
                premium=round(call_greeks.theoretical_price or 0, 2),
                delta=call_greeks.delta,
                iv=iv,
                intrinsic=call_greeks.intrinsic_value,
                time_value=call_greeks.time_value,
            ))
            
            # Calculate put price
            put_greeks = self.greeks_calc.calculate_greeks(
                spot=spot_price,
                strike=strike,
                time_to_expiry=time_to_expiry,
                volatility=iv,
                option_type=OptionType.PUT,
            )
            
            puts.append(SimulatedOptionPrice(
                strike=strike,
                option_type="PE",
                premium=round(put_greeks.theoretical_price or 0, 2),
                delta=put_greeks.delta,
                iv=iv,
                intrinsic=put_greeks.intrinsic_value,
                time_value=put_greeks.time_value,
            ))
        
        return {"calls": calls, "puts": puts}
    
    def simulate_option_price_at_expiry(
        self,
        spot_at_expiry: float,
        strike: float,
        option_type: str,
    ) -> float:
        """
        Calculate option price at expiry (intrinsic value only).
        
        Args:
            spot_at_expiry: Spot price at expiry
            strike: Option strike
            option_type: "CE" or "PE"
        
        Returns:
            Option value at expiry
        """
        if option_type.upper() == "CE":
            return max(0, spot_at_expiry - strike)
        else:
            return max(0, strike - spot_at_expiry)
    
    def get_trading_days(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> List[datetime]:
        """Get list of trading days (excluding weekends)."""
        days = []
        current = start_date
        
        while current <= end_date:
            # Skip weekends
            if current.weekday() < 5:
                days.append(current)
            current += timedelta(days=1)
        
        return days
    
    def estimate_iv_from_move(
        self,
        daily_returns: List[float],
    ) -> float:
        """
        Estimate implied volatility from historical daily returns.
        
        Args:
            daily_returns: List of daily returns (e.g., [0.01, -0.02, ...])
        
        Returns:
            Annualized volatility estimate
        """
        if len(daily_returns) < 5:
            return 0.15  # Default
        
        # Calculate standard deviation of returns
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
        daily_vol = math.sqrt(variance)
        
        # Annualize (252 trading days)
        annual_vol = daily_vol * math.sqrt(252)
        
        return min(max(annual_vol, 0.08), 0.50)  # Clamp between 8% and 50%


# Singleton
_data_manager = None

def get_historical_data_manager() -> HistoricalDataManager:
    """Get or create historical data manager singleton."""
    global _data_manager
    if _data_manager is None:
        _data_manager = HistoricalDataManager()
    return _data_manager
