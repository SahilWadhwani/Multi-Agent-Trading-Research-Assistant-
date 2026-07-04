"""
F&O Data Feed for Indian Markets.

Provides:
- Option chain data with live prices
- Futures prices and OI
- Greeks calculation for all strikes
- PCR, Max Pain, IV analysis
- Expiry calendar management

Supports: Nifty, Bank Nifty, FinNifty, Stock F&O
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.upstox_client import get_upstox_client
from data_feeds.options_greeks import get_greeks_calculator, OptionType


class UnderlyingType(Enum):
    INDEX = "INDEX"
    STOCK = "STOCK"


@dataclass
class FuturesQuote:
    """Futures contract quote."""
    symbol: str
    expiry: str
    ltp: float
    change: float
    change_percent: float
    oi: int
    oi_change: int
    volume: int
    bid: float
    ask: float
    lot_size: int
    basis: float  # Futures premium/discount to spot


@dataclass
class OptionQuote:
    """Individual option quote with Greeks."""
    strike: float
    option_type: str  # CE or PE
    ltp: float
    bid: float
    ask: float
    oi: int
    oi_change: int
    volume: int
    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    intrinsic: float
    time_value: float
    moneyness: str  # ITM, ATM, OTM


@dataclass
class OptionChainSummary:
    """Summary of option chain analysis."""
    underlying: str
    spot_price: float
    expiry: str
    days_to_expiry: int
    atm_strike: float
    pcr_oi: float
    pcr_volume: float
    max_pain: float
    iv_percentile: Optional[float]
    iv_rank: Optional[float]
    total_call_oi: int
    total_put_oi: int
    total_call_volume: int
    total_put_volume: int
    highest_oi_call_strike: float
    highest_oi_put_strike: float
    iv_skew: str
    market_sentiment: str


class FODataFeed:
    """
    F&O Data Feed for real-time derivatives data.
    
    Key Features:
    - Fetches live option chains from Upstox
    - Calculates Greeks for all strikes
    - Provides PCR, Max Pain, IV analysis
    - Supports multiple indices and stocks
    """
    
    # Index mapping for Upstox
    INDEX_KEYS = {
        "NIFTY": "NSE_INDEX|Nifty 50",
        "BANKNIFTY": "NSE_INDEX|Nifty Bank",
        "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
        "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    }
    
    # Lot sizes
    LOT_SIZES = {
        "NIFTY": 65,
        "BANKNIFTY": 30,
        "FINNIFTY": 40,
        "MIDCPNIFTY": 75,
    }
    
    # Strike intervals (NSE standard step between consecutive strikes)
    STRIKE_INTERVALS = {
        "NIFTY": 50,
        "BANKNIFTY": 100,
        "FINNIFTY": 50,
        "MIDCPNIFTY": 25,
    }
    
    def __init__(self):
        self.client = get_upstox_client()
        self.greeks_calc = get_greeks_calculator()
        self._cache = {}
        self._cache_expiry = 30  # seconds
        self._last_contracts_error: Optional[str] = None
    
    def _is_cache_valid(self, key: str, max_age_s: Optional[float] = None) -> bool:
        """Check if cached data is still valid."""
        if key not in self._cache:
            return False
        cached_time = self._cache[key].get("timestamp", 0)
        ttl = max_age_s if max_age_s is not None else self._cache_expiry
        return (datetime.now().timestamp() - cached_time) < ttl
    
    def get_spot_price(self, symbol: str) -> Dict[str, Any]:
        """
        Get current spot price for an underlying.
        
        Args:
            symbol: NIFTY, BANKNIFTY, etc.
        
        Returns:
            Spot price data
        """
        symbol_upper = symbol.upper()
        instrument_key = self.INDEX_KEYS.get(symbol_upper)
        
        if not instrument_key:
            # Try as stock
            instrument_key = f"NSE_EQ|{symbol_upper}"
        
        try:
            result = self.client.get_full_market_quote(instrument_key)
            if result.get("status") == "success":
                # Upstox returns key with colon instead of pipe
                data_key = instrument_key.replace("|", ":")
                data = result.get("data", {}).get(data_key, {})
                
                # Fallback: try original key or first key in data
                if not data:
                    data = result.get("data", {}).get(instrument_key, {})
                if not data and result.get("data"):
                    data = list(result.get("data", {}).values())[0]
                
                return {
                    "symbol": symbol_upper,
                    "ltp": data.get("last_price", 0),
                    "change": data.get("net_change", 0),
                    "change_percent": data.get("percentage_change", 0),
                    "open": data.get("ohlc", {}).get("open", 0),
                    "high": data.get("ohlc", {}).get("high", 0),
                    "low": data.get("ohlc", {}).get("low", 0),
                    "close": data.get("ohlc", {}).get("close", 0),
                    "volume": data.get("volume", 0),
                }
        except Exception as e:
            return {"error": str(e), "symbol": symbol_upper}
        
        return {"error": "Could not fetch spot price", "symbol": symbol_upper}
    
    INDIA_VIX_KEY = "NSE_INDEX|India VIX"
    
    def get_india_vix(self) -> Dict[str, Any]:
        """
        India VIX (volatility index). Cached 60 seconds.
        Returns vix, vix_change_pct, or error.
        """
        cache_key = "india_vix_quote"
        now_ts = datetime.now().timestamp()
        if cache_key in self._cache:
            ent = self._cache[cache_key]
            if now_ts - ent.get("timestamp", 0) < 60:
                return ent["data"]
        try:
            result = self.client.get_full_market_quote(self.INDIA_VIX_KEY)
            if result.get("status") != "success":
                out = {"error": result.get("message") or "quote_failed", "vix": 0.0, "vix_change_pct": 0.0}
                self._cache[cache_key] = {"data": out, "timestamp": now_ts}
                return out
            ik = self.INDIA_VIX_KEY
            data_key = ik.replace("|", ":")
            data = result.get("data", {}).get(data_key, {})
            if not data:
                data = result.get("data", {}).get(ik, {})
            if not data and result.get("data"):
                data = list(result.get("data", {}).values())[0]
            vix = float(data.get("last_price") or 0)
            ch_pct = float(data.get("percentage_change") or 0)
            out = {"vix": vix, "vix_change_pct": ch_pct, "error": None}
            self._cache[cache_key] = {"data": out, "timestamp": now_ts}
            return out
        except Exception as e:
            out = {"error": str(e), "vix": 0.0, "vix_change_pct": 0.0}
            self._cache[cache_key] = {"data": out, "timestamp": now_ts}
            return out
    
    def get_expiries(self, symbol: str) -> List[str]:
        """
        Get available expiry dates for a symbol.
        
        Args:
            symbol: NIFTY, BANKNIFTY, etc.
        
        Returns:
            List of expiry dates in YYYY-MM-DD format
        """
        symbol_upper = symbol.upper()
        instrument_key = self.INDEX_KEYS.get(symbol_upper)
        
        if not instrument_key:
            return []
        
        cache_key = f"expiries_{symbol_upper}"
        if self._is_cache_valid(cache_key):
            return self._cache[cache_key]["data"]
        
        try:
            result = self.client.get_option_contracts(instrument_key)
            self._last_contracts_error = None
            if result.get("status") == "success":
                contracts = result.get("data", [])
                raw_exps = [c.get("expiry") for c in contracts if c.get("expiry")]
                expiries = sorted(list(set(self._normalize_expiry_string(e) for e in raw_exps)))
                expiries = [e for e in expiries if e]
                self._cache[cache_key] = {"data": expiries, "timestamp": datetime.now().timestamp()}
                return expiries
            errs = result.get("errors") or []
            if errs:
                code = errs[0].get("errorCode") or errs[0].get("error_code") or ""
                msg = errs[0].get("message") or str(errs[0])
                self._last_contracts_error = f"{msg}" + (f" ({code})" if code else "")
        except Exception:
            pass
        
        return []

    @staticmethod
    def _normalize_expiry_string(exp: Any) -> Optional[str]:
        """Return YYYY-MM-DD if exp is a string or epoch ms."""
        if exp is None:
            return None
        if isinstance(exp, (int, float)):
            try:
                return datetime.utcfromtimestamp(exp / 1000.0).strftime("%Y-%m-%d")
            except (OverflowError, OSError, ValueError):
                return None
        s = str(exp).strip()
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        return s or None
    
    def get_nearest_expiry(self, symbol: str) -> Optional[str]:
        """Get the nearest expiry date."""
        expiries = self.get_expiries(symbol)
        if expiries:
            ist = pytz.timezone("Asia/Kolkata")
            today = datetime.now(ist).strftime("%Y-%m-%d")
            future_expiries = [e for e in expiries if e >= today]
            return future_expiries[0] if future_expiries else expiries[-1]
        return None
    
    def get_weekly_expiry(self, symbol: str) -> Optional[str]:
        """Get the current week's expiry (Thursday for Nifty/BankNifty)."""
        return self.get_nearest_expiry(symbol)
    
    def get_monthly_expiry(self, symbol: str) -> Optional[str]:
        """Get the current month's last Thursday expiry."""
        expiries = self.get_expiries(symbol)
        if not expiries:
            return None
        
        # Find expiries that are on the last Thursday of the month
        today = datetime.now()
        current_month = today.month
        
        monthly_expiries = []
        for exp in expiries:
            try:
                exp_date = datetime.strptime(exp, "%Y-%m-%d")
                # Check if this is likely a monthly expiry (last Thursday)
                # Monthly expiries typically have more days between them
                if exp_date.month >= current_month:
                    monthly_expiries.append(exp)
            except:
                continue
        
        return monthly_expiries[0] if monthly_expiries else None
    
    def _days_to_expiry(self, expiry_date: str) -> int:
        """Calculate days to expiry."""
        try:
            exp = datetime.strptime(expiry_date, "%Y-%m-%d")
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            delta = exp - today
            return max(0, delta.days)
        except:
            return 0
    
    @staticmethod
    def _option_leg_instrument_key(leg: Optional[Dict[str, Any]]) -> Optional[str]:
        """Extract Upstox instrument_key from option chain leg payload."""
        if not leg or not isinstance(leg, dict):
            return None
        for key in ("instrument_key", "instrument_token"):
            v = leg.get(key)
            if v:
                return str(v)
        inst = leg.get("instrument") or {}
        if isinstance(inst, dict):
            for key in ("instrument_key", "instrument_token"):
                v = inst.get(key)
                if v:
                    return str(v)
        return None
    
    def get_option_chain(
        self,
        symbol: str,
        expiry: str = None,
        strikes_around_atm: int = 10,
    ) -> Dict[str, Any]:
        """
        Get full option chain with Greeks.
        
        Args:
            symbol: NIFTY, BANKNIFTY, etc.
            expiry: Expiry date (default: nearest)
            strikes_around_atm: Number of strikes to fetch around ATM
        
        Returns:
            Complete option chain with Greeks, PCR, Max Pain
        """
        symbol_upper = symbol.upper()
        instrument_key = self.INDEX_KEYS.get(symbol_upper)
        
        if not instrument_key:
            return {"error": f"Unknown symbol: {symbol}"}
        
        # Get expiry
        if not expiry:
            expiry = self.get_nearest_expiry(symbol_upper)
        
        if not expiry:
            if self._last_contracts_error:
                return {
                    "error": f"Upstox API error (expiry list): {self._last_contracts_error}. "
                    "Refresh your access token if this mentions invalid token."
                }
            return {"error": "Could not determine expiry date (empty contract list)"}
        
        # Get spot price
        spot_data = self.get_spot_price(symbol_upper)
        spot_price = spot_data.get("ltp", 0)
        
        if not spot_price:
            err = spot_data.get("error")
            if err:
                return {"error": f"Could not fetch spot price: {err}"}
            return {"error": "Could not fetch spot price"}
        
        days_to_exp = self._days_to_expiry(expiry)
        time_to_expiry = self.greeks_calc.days_to_years(days_to_exp)
        
        try:
            # Fetch option chain from Upstox
            result = self.client.get_option_chain(instrument_key, expiry)
            
            if result.get("status") != "success":
                return {"error": result.get("message", "Failed to fetch option chain")}
            
            chain_data = result.get("data", [])
            
            # Find ATM strike
            strike_interval = self.STRIKE_INTERVALS.get(symbol_upper, 50)
            atm_strike = round(spot_price / strike_interval) * strike_interval
            
            # Process chain
            calls = []
            puts = []
            total_call_oi = 0
            total_put_oi = 0
            total_call_vol = 0
            total_put_vol = 0
            max_call_oi = 0
            max_put_oi = 0
            highest_oi_call_strike = atm_strike
            highest_oi_put_strike = atm_strike
            
            for strike_data in chain_data:
                strike = strike_data.get("strike_price", 0)
                
                # Filter to strikes around ATM
                if abs(strike - atm_strike) > strikes_around_atm * strike_interval:
                    continue
                
                # Process Call
                call_data = strike_data.get("call_options", {})
                if call_data:
                    call_ltp = call_data.get("market_data", {}).get("ltp", 0) or call_data.get("ltp", 0)
                    call_oi = call_data.get("market_data", {}).get("oi", 0) or call_data.get("oi", 0)
                    call_vol = call_data.get("market_data", {}).get("volume", 0) or call_data.get("volume", 0)
                    
                    total_call_oi += call_oi
                    total_call_vol += call_vol
                    
                    if call_oi > max_call_oi:
                        max_call_oi = call_oi
                        highest_oi_call_strike = strike
                    
                    # Calculate Greeks
                    if call_ltp > 0 and time_to_expiry > 0:
                        greeks = self.greeks_calc.calculate_greeks(
                            spot=spot_price,
                            strike=strike,
                            time_to_expiry=time_to_expiry,
                            volatility=0.15,  # Initial, will be updated with IV
                            option_type=OptionType.CALL,
                            market_price=call_ltp,
                        )
                    else:
                        greeks = None
                    
                    calls.append({
                        "strike": strike,
                        "instrument_key": self._option_leg_instrument_key(call_data),
                        "ltp": call_ltp,
                        "bid": call_data.get("market_data", {}).get("bid_price", 0),
                        "ask": call_data.get("market_data", {}).get("ask_price", 0),
                        "oi": call_oi,
                        "oi_change": call_data.get("market_data", {}).get("oi_day_change", 0),
                        "volume": call_vol,
                        "iv": greeks.iv if greeks else None,
                        "delta": greeks.delta if greeks else None,
                        "gamma": greeks.gamma if greeks else None,
                        "theta": greeks.theta if greeks else None,
                        "vega": greeks.vega if greeks else None,
                        "intrinsic": max(0, spot_price - strike),
                        "time_value": call_ltp - max(0, spot_price - strike) if call_ltp else 0,
                        "moneyness": "ITM" if spot_price > strike else "OTM" if spot_price < strike else "ATM",
                    })
                
                # Process Put
                put_data = strike_data.get("put_options", {})
                if put_data:
                    put_ltp = put_data.get("market_data", {}).get("ltp", 0) or put_data.get("ltp", 0)
                    put_oi = put_data.get("market_data", {}).get("oi", 0) or put_data.get("oi", 0)
                    put_vol = put_data.get("market_data", {}).get("volume", 0) or put_data.get("volume", 0)
                    
                    total_put_oi += put_oi
                    total_put_vol += put_vol
                    
                    if put_oi > max_put_oi:
                        max_put_oi = put_oi
                        highest_oi_put_strike = strike
                    
                    # Calculate Greeks
                    if put_ltp > 0 and time_to_expiry > 0:
                        greeks = self.greeks_calc.calculate_greeks(
                            spot=spot_price,
                            strike=strike,
                            time_to_expiry=time_to_expiry,
                            volatility=0.15,
                            option_type=OptionType.PUT,
                            market_price=put_ltp,
                        )
                    else:
                        greeks = None
                    
                    puts.append({
                        "strike": strike,
                        "instrument_key": self._option_leg_instrument_key(put_data),
                        "ltp": put_ltp,
                        "bid": put_data.get("market_data", {}).get("bid_price", 0),
                        "ask": put_data.get("market_data", {}).get("ask_price", 0),
                        "oi": put_oi,
                        "oi_change": put_data.get("market_data", {}).get("oi_day_change", 0),
                        "volume": put_vol,
                        "iv": greeks.iv if greeks else None,
                        "delta": greeks.delta if greeks else None,
                        "gamma": greeks.gamma if greeks else None,
                        "theta": greeks.theta if greeks else None,
                        "vega": greeks.vega if greeks else None,
                        "intrinsic": max(0, strike - spot_price),
                        "time_value": put_ltp - max(0, strike - spot_price) if put_ltp else 0,
                        "moneyness": "ITM" if spot_price < strike else "OTM" if spot_price > strike else "ATM",
                    })
            
            # Sort by strike
            calls.sort(key=lambda x: x["strike"])
            puts.sort(key=lambda x: x["strike"])
            
            # Calculate PCR
            pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else 0
            pcr_vol = total_put_vol / total_call_vol if total_call_vol > 0 else 0
            
            # Calculate Max Pain
            max_pain = self._calculate_max_pain(calls, puts, spot_price)
            
            # Determine IV skew
            iv_skew = self._determine_iv_skew(calls, puts, atm_strike)
            
            # Determine market sentiment
            sentiment = self._determine_sentiment(pcr_oi, max_pain, spot_price, highest_oi_call_strike, highest_oi_put_strike)
            
            return {
                "symbol": symbol_upper,
                "spot_price": spot_price,
                "expiry": expiry,
                "days_to_expiry": days_to_exp,
                "atm_strike": atm_strike,
                "lot_size": self.LOT_SIZES.get(symbol_upper, 50),
                "strike_interval": strike_interval,
                "calls": calls,
                "puts": puts,
                "summary": {
                    "pcr_oi": round(pcr_oi, 2),
                    "pcr_volume": round(pcr_vol, 2),
                    "max_pain": max_pain,
                    "total_call_oi": total_call_oi,
                    "total_put_oi": total_put_oi,
                    "total_call_volume": total_call_vol,
                    "total_put_volume": total_put_vol,
                    "highest_oi_call_strike": highest_oi_call_strike,
                    "highest_oi_put_strike": highest_oi_put_strike,
                    "iv_skew": iv_skew,
                    "sentiment": sentiment,
                },
                "timestamp": datetime.now().isoformat(),
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    def _calculate_max_pain(self, calls: list, puts: list, spot: float) -> float:
        """Calculate Max Pain strike."""
        if not calls and not puts:
            return spot
        
        strikes = sorted(set([c["strike"] for c in calls] + [p["strike"] for p in puts]))
        
        min_pain_value = float('inf')
        max_pain_strike = strikes[0] if strikes else spot
        
        for test_strike in strikes:
            total_pain = 0
            
            for call in calls:
                if test_strike > call["strike"]:
                    total_pain += (test_strike - call["strike"]) * call["oi"]
            
            for put in puts:
                if test_strike < put["strike"]:
                    total_pain += (put["strike"] - test_strike) * put["oi"]
            
            if total_pain < min_pain_value:
                min_pain_value = total_pain
                max_pain_strike = test_strike
        
        return max_pain_strike
    
    def _determine_iv_skew(self, calls: list, puts: list, atm_strike: float) -> str:
        """Determine IV skew pattern."""
        atm_call = next((c for c in calls if c["strike"] == atm_strike), None)
        atm_put = next((p for p in puts if p["strike"] == atm_strike), None)
        
        atm_iv = None
        if atm_call and atm_call.get("iv"):
            atm_iv = atm_call["iv"]
        elif atm_put and atm_put.get("iv"):
            atm_iv = atm_put["iv"]
        
        if not atm_iv:
            return "UNKNOWN"
        
        # Check OTM puts and calls
        otm_puts = [p for p in puts if p["strike"] < atm_strike and p.get("iv")]
        otm_calls = [c for c in calls if c["strike"] > atm_strike and c.get("iv")]
        
        avg_otm_put_iv = sum(p["iv"] for p in otm_puts) / len(otm_puts) if otm_puts else 0
        avg_otm_call_iv = sum(c["iv"] for c in otm_calls) / len(otm_calls) if otm_calls else 0
        
        if avg_otm_put_iv > atm_iv * 1.1 and avg_otm_call_iv > atm_iv * 1.1:
            return "SMILE"
        elif avg_otm_put_iv > atm_iv * 1.1:
            return "PUT_SKEW"
        elif avg_otm_call_iv > atm_iv * 1.1:
            return "CALL_SKEW"
        else:
            return "FLAT"
    
    def _determine_sentiment(
        self,
        pcr_oi: float,
        max_pain: float,
        spot: float,
        highest_call_strike: float,
        highest_put_strike: float,
    ) -> str:
        """
        Determine market sentiment from options data.
        
        Factors:
        - PCR > 1.2 = Bullish (more puts = hedging = bullish)
        - PCR < 0.8 = Bearish
        - Max Pain > Spot = Bullish bias
        - Highest OI levels indicate support/resistance
        """
        bullish_signals = 0
        bearish_signals = 0
        
        # PCR analysis
        if pcr_oi > 1.2:
            bullish_signals += 1
        elif pcr_oi < 0.8:
            bearish_signals += 1
        
        # Max Pain vs Spot
        if max_pain > spot * 1.01:
            bullish_signals += 1
        elif max_pain < spot * 0.99:
            bearish_signals += 1
        
        # OI distribution
        if highest_put_strike < spot:
            bullish_signals += 1  # Strong put support below
        if highest_call_strike > spot:
            bearish_signals += 1  # Strong call resistance above
        
        if bullish_signals >= 2:
            return "BULLISH"
        elif bearish_signals >= 2:
            return "BEARISH"
        else:
            return "NEUTRAL"
    
    def get_straddle_price(self, symbol: str, strike: float = None, expiry: str = None) -> Dict:
        """
        Get ATM straddle price (Call + Put at same strike).
        
        Useful for gauging expected move.
        """
        chain = self.get_option_chain(symbol, expiry)
        
        if "error" in chain:
            return chain
        
        target_strike = strike or chain["atm_strike"]
        
        call = next((c for c in chain["calls"] if c["strike"] == target_strike), None)
        put = next((p for p in chain["puts"] if p["strike"] == target_strike), None)
        
        if not call or not put:
            return {"error": f"Strike {target_strike} not found in chain"}
        
        straddle_price = call["ltp"] + put["ltp"]
        expected_move_percent = (straddle_price / chain["spot_price"]) * 100
        
        return {
            "strike": target_strike,
            "call_price": call["ltp"],
            "put_price": put["ltp"],
            "straddle_price": straddle_price,
            "expected_move_points": straddle_price,
            "expected_move_percent": round(expected_move_percent, 2),
            "upper_breakeven": target_strike + straddle_price,
            "lower_breakeven": target_strike - straddle_price,
            "spot_price": chain["spot_price"],
            "expiry": chain["expiry"],
            "days_to_expiry": chain["days_to_expiry"],
        }


    def get_intraday_vwap(self, symbol: str) -> Dict[str, Any]:
        """
        Compute intraday VWAP for an index using 1-minute candles.
        Cached for 2 minutes.

        Returns: {"vwap": float, "spot": float, "spot_vs_vwap": str, "error": None}
        """
        symbol_upper = symbol.upper()
        cache_key = f"vwap_{symbol_upper}"
        if self._is_cache_valid(cache_key, max_age_s=120):
            return self._cache[cache_key]["data"]

        instrument_key = self.INDEX_KEYS.get(symbol_upper)
        if not instrument_key:
            return {"vwap": 0.0, "spot": 0.0, "spot_vs_vwap": "unknown", "error": "Unknown symbol"}

        try:
            today = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
            result = self.client.get_historical_candles(
                instrument_key=instrument_key,
                interval="1minute",
                from_date=today,
                to_date=today,
            )
            candles = []
            if result.get("status") == "success":
                candles = result.get("data", {}).get("candles", [])
            if not candles:
                return {"vwap": 0.0, "spot": 0.0, "spot_vs_vwap": "unknown", "error": "No candle data"}

            from data_feeds.technical_indicators import TechnicalIndicators

            highs = [float(c[2]) for c in candles]
            lows = [float(c[3]) for c in candles]
            closes = [float(c[4]) for c in candles]
            volumes = [float(c[5]) if len(c) > 5 else 1.0 for c in candles]

            vwap_series = TechnicalIndicators.calculate_vwap(highs, lows, closes, volumes)
            vwap = vwap_series[-1] if vwap_series and vwap_series[-1] else 0.0
            spot = closes[-1] if closes else 0.0

            if vwap > 0 and spot > 0:
                diff_pct = ((spot - vwap) / vwap) * 100
                if diff_pct > 0.15:
                    pos = "above"
                elif diff_pct < -0.15:
                    pos = "below"
                else:
                    pos = "at"
            else:
                pos = "unknown"

            out = {"vwap": round(vwap, 2), "spot": spot, "spot_vs_vwap": pos, "error": None}
            self._cache[cache_key] = {"data": out, "timestamp": datetime.now().timestamp()}
            return out

        except Exception as e:
            return {"vwap": 0.0, "spot": 0.0, "spot_vs_vwap": "unknown", "error": str(e)}

    def get_spot_atr(self, symbol: str) -> Dict[str, Any]:
        """
        Compute intraday ATR using 30-minute candles.
        Cached for 5 minutes.

        Returns: {"atr": float, "atr_pct": float, "spot": float, "error": None}
        """
        symbol_upper = symbol.upper()
        cache_key = f"atr_{symbol_upper}"
        if self._is_cache_valid(cache_key, max_age_s=300):
            return self._cache[cache_key]["data"]

        instrument_key = self.INDEX_KEYS.get(symbol_upper)
        if not instrument_key:
            return {"atr": 0.0, "atr_pct": 0.0, "spot": 0.0, "error": "Unknown symbol"}

        try:
            today = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")
            yesterday = (datetime.now(pytz.timezone("Asia/Kolkata")) - timedelta(days=3)).strftime("%Y-%m-%d")
            result = self.client.get_historical_candles(
                instrument_key=instrument_key,
                interval="30minute",
                from_date=yesterday,
                to_date=today,
            )
            candles = []
            if result.get("status") == "success":
                candles = result.get("data", {}).get("candles", [])
            if not candles or len(candles) < 3:
                return {"atr": 0.0, "atr_pct": 0.0, "spot": 0.0, "error": "Insufficient candle data"}

            from data_feeds.technical_indicators import TechnicalIndicators

            highs = [float(c[2]) for c in candles]
            lows = [float(c[3]) for c in candles]
            closes = [float(c[4]) for c in candles]

            atr_series = TechnicalIndicators.calculate_atr(highs, lows, closes, period=min(14, len(candles) - 1))
            atr = 0.0
            for v in reversed(atr_series):
                if v is not None:
                    atr = v
                    break

            spot = closes[-1] if closes else 0.0
            atr_pct = (atr / spot * 100) if spot > 0 else 0.0

            out = {"atr": round(atr, 2), "atr_pct": round(atr_pct, 3), "spot": spot, "error": None}
            self._cache[cache_key] = {"data": out, "timestamp": datetime.now().timestamp()}
            return out

        except Exception as e:
            return {"atr": 0.0, "atr_pct": 0.0, "spot": 0.0, "error": str(e)}


# Singleton
_fo_feed = None

def get_fo_data_feed() -> FODataFeed:
    """Get or create the F&O data feed singleton."""
    global _fo_feed
    if _fo_feed is None:
        _fo_feed = FODataFeed()
    return _fo_feed
