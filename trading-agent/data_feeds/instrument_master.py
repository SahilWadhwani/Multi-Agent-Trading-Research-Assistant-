"""
INSTRUMENT MASTER

Fetches and caches Upstox instrument data dynamically.
No more hardcoding ISINs or symbols!

Data source: https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
"""

import os
import json
import gzip
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

# Cache file path
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_cache")
CACHE_FILE = os.path.join(CACHE_DIR, "instrument_master.json")
CACHE_EXPIRY_HOURS = 24  # Refresh daily


@dataclass
class Instrument:
    """Single instrument data."""
    symbol: str           # Trading symbol (e.g., "RELIANCE")
    name: str             # Full name
    isin: str             # ISIN code
    instrument_key: str   # Upstox API key (e.g., "NSE_EQ|INE002A01018")
    segment: str          # NSE_EQ, NSE_FO, NSE_INDEX
    instrument_type: str  # EQ, INDEX, FUT, CE, PE
    lot_size: int
    exchange: str         # NSE, BSE
    tick_size: float


class InstrumentMaster:
    """
    Dynamic instrument master - fetches from Upstox.
    
    Use this instead of hardcoded symbol mappings!
    """
    
    UPSTOX_NSE_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    UPSTOX_BSE_URL = "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz"
    
    def __init__(self, auto_load: bool = True):
        self._instruments: Dict[str, Instrument] = {}
        self._by_isin: Dict[str, Instrument] = {}
        self._by_instrument_key: Dict[str, Instrument] = {}
        self._loaded = False
        self._last_updated: Optional[datetime] = None
        
        os.makedirs(CACHE_DIR, exist_ok=True)
        
        if auto_load:
            self.load()
    
    def load(self, force_refresh: bool = False) -> bool:
        """Load instruments from cache or fetch fresh."""
        # Check if we have a valid cache
        if not force_refresh and self._load_from_cache():
            return True
        
        # Fetch fresh data
        return self._fetch_from_upstox()
    
    def _load_from_cache(self) -> bool:
        """Load from local cache if valid."""
        if not os.path.exists(CACHE_FILE):
            return False
        
        try:
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
            
            # Check expiry
            cached_time = datetime.fromisoformat(data.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_time > timedelta(hours=CACHE_EXPIRY_HOURS):
                print("   Instrument cache expired, refreshing...")
                return False
            
            # Load instruments
            self._instruments = {}
            self._by_isin = {}
            self._by_instrument_key = {}
            
            for item in data.get("instruments", []):
                inst = Instrument(
                    symbol=item["symbol"],
                    name=item["name"],
                    isin=item["isin"],
                    instrument_key=item["instrument_key"],
                    segment=item["segment"],
                    instrument_type=item["instrument_type"],
                    lot_size=item["lot_size"],
                    exchange=item["exchange"],
                    tick_size=item["tick_size"],
                )
                self._instruments[inst.symbol.upper()] = inst
                self._by_isin[inst.isin] = inst
                self._by_instrument_key[inst.instrument_key] = inst
            
            self._loaded = True
            self._last_updated = cached_time
            print(f"   Loaded {len(self._instruments)} instruments from cache")
            return True
            
        except Exception as e:
            print(f"   Cache load error: {e}")
            return False
    
    def _fetch_from_upstox(self) -> bool:
        """Fetch fresh data from Upstox."""
        print("   Fetching instrument master from Upstox...")
        
        try:
            resp = requests.get(self.UPSTOX_NSE_URL, timeout=30)
            if resp.status_code != 200:
                print(f"   Failed to fetch: {resp.status_code}")
                return False
            
            raw_instruments = json.loads(gzip.decompress(resp.content))
            print(f"   Downloaded {len(raw_instruments)} instruments")
            
            # Process and filter
            self._instruments = {}
            self._by_isin = {}
            self._by_instrument_key = {}
            
            for item in raw_instruments:
                # We want equity (EQ), indices (INDEX), and some F&O
                segment = item.get("segment", "")
                inst_type = item.get("instrument_type", "")
                
                if segment not in ["NSE_EQ", "NSE_INDEX"]:
                    continue
                
                # Skip non-standard equity (bonds, ETFs with weird names, etc.)
                if segment == "NSE_EQ" and inst_type not in ["EQ", "INDEX"]:
                    # Keep ETFs
                    if "BEES" not in item.get("trading_symbol", ""):
                        continue
                
                symbol = item.get("trading_symbol", "")
                if not symbol:
                    continue
                
                inst = Instrument(
                    symbol=symbol,
                    name=item.get("name", ""),
                    isin=item.get("isin", ""),
                    instrument_key=item.get("instrument_key", ""),
                    segment=segment,
                    instrument_type=inst_type,
                    lot_size=item.get("lot_size", 1),
                    exchange=item.get("exchange", "NSE"),
                    tick_size=item.get("tick_size", 0.05),
                )
                
                self._instruments[inst.symbol.upper()] = inst
                if inst.isin:
                    self._by_isin[inst.isin] = inst
                self._by_instrument_key[inst.instrument_key] = inst
            
            self._loaded = True
            self._last_updated = datetime.now()
            
            # Save to cache
            self._save_to_cache()
            
            print(f"   Processed {len(self._instruments)} tradeable instruments")
            return True
            
        except Exception as e:
            print(f"   Fetch error: {e}")
            return False
    
    def _save_to_cache(self):
        """Save to local cache."""
        try:
            data = {
                "cached_at": self._last_updated.isoformat(),
                "instruments": [
                    {
                        "symbol": i.symbol,
                        "name": i.name,
                        "isin": i.isin,
                        "instrument_key": i.instrument_key,
                        "segment": i.segment,
                        "instrument_type": i.instrument_type,
                        "lot_size": i.lot_size,
                        "exchange": i.exchange,
                        "tick_size": i.tick_size,
                    }
                    for i in self._instruments.values()
                ]
            }
            
            with open(CACHE_FILE, 'w') as f:
                json.dump(data, f)
                
        except Exception as e:
            print(f"   Cache save error: {e}")
    
    def get(self, symbol: str) -> Optional[Instrument]:
        """Get instrument by symbol."""
        if not self._loaded:
            self.load()
        return self._instruments.get(symbol.upper())
    
    def get_by_isin(self, isin: str) -> Optional[Instrument]:
        """Get instrument by ISIN."""
        if not self._loaded:
            self.load()
        return self._by_isin.get(isin)
    
    def get_instrument_key(self, symbol: str) -> Optional[str]:
        """Get Upstox instrument key for a symbol."""
        inst = self.get(symbol)
        return inst.instrument_key if inst else None
    
    def get_isin(self, symbol: str) -> Optional[str]:
        """Get ISIN for a symbol."""
        inst = self.get(symbol)
        return inst.isin if inst else None
    
    def search(self, query: str, limit: int = 20) -> List[Instrument]:
        """Search instruments by name or symbol."""
        if not self._loaded:
            self.load()
        
        query = query.upper()
        results = []
        
        for symbol, inst in self._instruments.items():
            if query in symbol or query in inst.name.upper():
                results.append(inst)
                if len(results) >= limit:
                    break
        
        return results
    
    def get_all_equity(self) -> List[Instrument]:
        """Get all equity instruments."""
        if not self._loaded:
            self.load()
        return [i for i in self._instruments.values() if i.segment == "NSE_EQ" and i.instrument_type == "EQ"]
    
    def get_nifty50(self) -> List[str]:
        """Get NIFTY 50 constituent symbols."""
        # These are the NIFTY 50 stocks (as of recent composition)
        nifty50 = [
            "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
            "SBIN", "BHARTIARTL", "KOTAKBANK", "ITC", "LT", "AXISBANK",
            "ASIANPAINT", "MARUTI", "WIPRO", "HCLTECH", "BAJFINANCE",
            "SUNPHARMA", "TATAMOTORS", "TATASTEEL", "ONGC", "NTPC",
            "POWERGRID", "JSWSTEEL", "M&M", "ULTRACEMCO", "TECHM", "TITAN",
            "ADANIENT", "ADANIPORTS", "COALINDIA", "BAJAJFINSV", "GRASIM",
            "DRREDDY", "DIVISLAB", "NESTLEIND", "CIPLA", "BRITANNIA",
            "EICHERMOT", "HEROMOTOCO", "APOLLOHOSP", "SBILIFE", "HDFCLIFE",
            "BPCL", "INDUSINDBK", "UPL", "TATACONSUM", "HINDALCO", "VEDL", "LTIM",
        ]
        return [s for s in nifty50 if self.get(s)]
    
    def get_etfs(self) -> List[Instrument]:
        """Get ETF instruments."""
        if not self._loaded:
            self.load()
        return [i for i in self._instruments.values() if "BEES" in i.symbol or "ETF" in i.name.upper()]
    
    def get_indices(self) -> List[Instrument]:
        """Get index instruments."""
        if not self._loaded:
            self.load()
        return [i for i in self._instruments.values() if i.segment == "NSE_INDEX"]
    
    def stats(self) -> Dict[str, int]:
        """Get instrument statistics."""
        if not self._loaded:
            self.load()
        
        from collections import Counter
        segments = Counter(i.segment for i in self._instruments.values())
        types = Counter(i.instrument_type for i in self._instruments.values())
        
        return {
            "total": len(self._instruments),
            "by_segment": dict(segments),
            "by_type": dict(types),
            "last_updated": self._last_updated.isoformat() if self._last_updated else None,
        }


# Singleton
_master = None

def get_instrument_master() -> InstrumentMaster:
    """Get or create instrument master singleton."""
    global _master
    if _master is None:
        _master = InstrumentMaster()
    return _master


# Test
if __name__ == "__main__":
    master = get_instrument_master()
    
    print("\nInstrument Master Stats:")
    print(json.dumps(master.stats(), indent=2))
    
    print("\nSample lookups:")
    for sym in ["RELIANCE", "TCS", "NIFTYBEES", "INVALID"]:
        inst = master.get(sym)
        if inst:
            print(f"  {sym}: {inst.instrument_key}")
        else:
            print(f"  {sym}: NOT FOUND")
    
    print(f"\nNIFTY 50 stocks available: {len(master.get_nifty50())}")
    print(f"ETFs available: {len(master.get_etfs())}")
