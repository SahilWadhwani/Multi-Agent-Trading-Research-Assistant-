"""
Market Data Feed for Indian Markets via Upstox API.
Provides OHLCV data and real-time quotes.
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# Add parent path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.upstox_client import get_upstox_client


class MarketDataFeed:
    """
    Fetches market data from Upstox API.
    Provides historical OHLCV data and real-time quotes.
    """
    
    def __init__(self):
        self.client = None
    
    def _get_client(self):
        """Lazy load Upstox client."""
        if self.client is None:
            self.client = get_upstox_client()
        return self.client
    
    def get_quote(self, symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """
        Get real-time quote for a symbol.
        
        Args:
            symbol: Stock symbol (e.g., "RELIANCE", "TCS")
            exchange: Exchange (NSE or BSE)
        
        Returns:
            Quote data including LTP, OHLC, volume
        """
        client = self._get_client()
        
        if not client.ensure_authenticated():
            return {"error": "Not authenticated"}
        
        try:
            result = client.get_market_quote(symbol, exchange)
            
            if result.get("status") == "success" and result.get("data"):
                quote_data = list(result["data"].values())[0]
                return {
                    "symbol": symbol,
                    "exchange": exchange,
                    "ltp": quote_data.get("last_price"),
                    "open": quote_data.get("ohlc", {}).get("open"),
                    "high": quote_data.get("ohlc", {}).get("high"),
                    "low": quote_data.get("ohlc", {}).get("low"),
                    "close": quote_data.get("ohlc", {}).get("close"),
                    "volume": quote_data.get("volume"),
                    "change": quote_data.get("net_change"),
                    "change_percent": quote_data.get("percentage_change"),
                    "timestamp": datetime.utcnow().isoformat(),
                }
            else:
                return {"error": result.get("message", "Failed to get quote")}
                
        except Exception as e:
            return {"error": str(e)}
    
    def get_historical_data(
        self,
        symbol: str,
        interval: str = "day",
        days: int = 100,
        exchange: str = "NSE"
    ) -> Dict[str, Any]:
        """
        Get historical OHLCV data.
        
        Args:
            symbol: Stock symbol
            interval: "1minute", "30minute", "day", "week", "month"
            days: Number of days of data
            exchange: Exchange
        
        Returns:
            Historical candle data
        """
        client = self._get_client()
        
        if not client.ensure_authenticated():
            return {"error": "Not authenticated"}
        
        try:
            instrument_key = f"{exchange}_EQ|{symbol}"
            result = client.get_historical_candles(
                instrument_key=instrument_key,
                interval=interval,
            )
            
            if result.get("status") == "success" and result.get("data"):
                candles = result["data"].get("candles", [])
                
                # Parse candles into structured format
                # Upstox format: [timestamp, open, high, low, close, volume, oi]
                parsed = {
                    "symbol": symbol,
                    "interval": interval,
                    "candles": [],
                    "opens": [],
                    "highs": [],
                    "lows": [],
                    "closes": [],
                    "volumes": [],
                }
                
                for candle in candles:
                    if len(candle) >= 6:
                        parsed["candles"].append({
                            "timestamp": candle[0],
                            "open": candle[1],
                            "high": candle[2],
                            "low": candle[3],
                            "close": candle[4],
                            "volume": candle[5],
                        })
                        parsed["opens"].append(candle[1])
                        parsed["highs"].append(candle[2])
                        parsed["lows"].append(candle[3])
                        parsed["closes"].append(candle[4])
                        parsed["volumes"].append(candle[5])
                
                # Reverse to chronological order (oldest first)
                for key in ["candles", "opens", "highs", "lows", "closes", "volumes"]:
                    parsed[key] = list(reversed(parsed[key]))
                
                return parsed
            else:
                return {"error": result.get("message", "Failed to get historical data")}
                
        except Exception as e:
            return {"error": str(e)}
    
    def get_multiple_quotes(self, symbols: List[str], exchange: str = "NSE") -> Dict[str, Any]:
        """Get quotes for multiple symbols."""
        results = {}
        for symbol in symbols:
            results[symbol] = self.get_quote(symbol, exchange)
        return results
    
    def get_index_data(self, index: str = "NIFTY 50") -> Dict[str, Any]:
        """
        Get index data (Nifty 50, Bank Nifty, etc.).
        Note: Index data requires different instrument keys.
        """
        # Index instrument keys are different
        index_map = {
            "NIFTY 50": "NSE_INDEX|Nifty 50",
            "NIFTY BANK": "NSE_INDEX|Nifty Bank",
            "NIFTY IT": "NSE_INDEX|Nifty IT",
        }
        
        client = self._get_client()
        
        if not client.ensure_authenticated():
            return {"error": "Not authenticated"}
        
        try:
            # For indices, we might need to use a different endpoint
            # This is a placeholder - actual implementation depends on Upstox API
            return {"index": index, "note": "Index data endpoint to be implemented"}
        except Exception as e:
            return {"error": str(e)}


# Singleton instance
_feed_instance = None

def get_market_feed() -> MarketDataFeed:
    """Get the market data feed singleton."""
    global _feed_instance
    if _feed_instance is None:
        _feed_instance = MarketDataFeed()
    return _feed_instance
