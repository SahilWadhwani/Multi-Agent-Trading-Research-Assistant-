"""
Options Greeks Calculator for Indian Markets.

Calculates:
- Delta: Rate of change of option price vs underlying
- Gamma: Rate of change of delta
- Theta: Time decay per day
- Vega: Sensitivity to volatility changes
- Implied Volatility (IV): Market-implied volatility from option price

Uses Black-Scholes model adapted for Indian markets.
"""

import math
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from scipy.stats import norm
from scipy.optimize import brentq


class OptionType(Enum):
    CALL = "CE"
    PUT = "PE"


@dataclass
class GreeksResult:
    """Option Greeks calculation result."""
    delta: float
    gamma: float
    theta: float  # Per day
    vega: float   # Per 1% vol change
    rho: float
    iv: Optional[float] = None  # Implied volatility
    theoretical_price: Optional[float] = None
    intrinsic_value: float = 0
    time_value: float = 0


class OptionsGreeksCalculator:
    """
    Calculate Option Greeks using Black-Scholes model.
    
    Adapted for Indian markets:
    - Uses RBI repo rate as risk-free rate (~6.5%)
    - Handles weekly and monthly expiries
    - Supports Nifty (50 lot) and Bank Nifty (15 lot)
    """
    
    # Default risk-free rate (RBI repo rate as of 2026)
    DEFAULT_RISK_FREE_RATE = 0.065  # 6.5%
    
    # Standard lot sizes for Indian F&O
    LOT_SIZES = {
        "NIFTY": 50,
        "BANKNIFTY": 15,
        "FINNIFTY": 40,
        "MIDCPNIFTY": 75,
        "SENSEX": 10,
    }
    
    def __init__(self, risk_free_rate: float = None):
        """
        Initialize calculator.
        
        Args:
            risk_free_rate: Annual risk-free rate (default: 6.5%)
        """
        self.risk_free_rate = risk_free_rate or self.DEFAULT_RISK_FREE_RATE
    
    @staticmethod
    def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
        """Calculate d1 for Black-Scholes."""
        if T <= 0 or sigma <= 0:
            return 0
        return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    
    @staticmethod
    def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
        """Calculate d2 for Black-Scholes."""
        if T <= 0 or sigma <= 0:
            return 0
        d1 = OptionsGreeksCalculator._d1(S, K, T, r, sigma)
        return d1 - sigma * math.sqrt(T)
    
    def black_scholes_price(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
        option_type: OptionType,
        risk_free_rate: float = None,
    ) -> float:
        """
        Calculate theoretical option price using Black-Scholes.
        
        Args:
            spot: Current underlying price
            strike: Option strike price
            time_to_expiry: Time to expiry in years
            volatility: Annualized volatility (e.g., 0.20 for 20%)
            option_type: CALL or PUT
            risk_free_rate: Override risk-free rate
        
        Returns:
            Theoretical option price
        """
        r = risk_free_rate or self.risk_free_rate
        S, K, T, sigma = spot, strike, time_to_expiry, volatility
        
        if T <= 0:
            # Expired option - only intrinsic value
            if option_type == OptionType.CALL:
                return max(0, S - K)
            else:
                return max(0, K - S)
        
        d1 = self._d1(S, K, T, r, sigma)
        d2 = self._d2(S, K, T, r, sigma)
        
        if option_type == OptionType.CALL:
            price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        else:
            price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        
        return max(0, price)
    
    def calculate_greeks(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
        option_type: OptionType,
        market_price: float = None,
        risk_free_rate: float = None,
    ) -> GreeksResult:
        """
        Calculate all Greeks for an option.
        
        Args:
            spot: Current underlying price
            strike: Option strike price
            time_to_expiry: Time to expiry in years (e.g., 7/365 for 7 days)
            volatility: Annualized volatility (0.15 = 15%)
            option_type: CALL or PUT
            market_price: Actual market price (for IV calculation)
            risk_free_rate: Override risk-free rate
        
        Returns:
            GreeksResult with all Greeks
        """
        r = risk_free_rate or self.risk_free_rate
        S, K, T, sigma = spot, strike, time_to_expiry, volatility
        
        # Handle edge cases
        if T <= 0:
            if option_type == OptionType.CALL:
                intrinsic = max(0, S - K)
                delta = 1.0 if S > K else 0.0
            else:
                intrinsic = max(0, K - S)
                delta = -1.0 if K > S else 0.0
            
            return GreeksResult(
                delta=delta,
                gamma=0,
                theta=0,
                vega=0,
                rho=0,
                iv=None,
                theoretical_price=intrinsic,
                intrinsic_value=intrinsic,
                time_value=0,
            )
        
        d1 = self._d1(S, K, T, r, sigma)
        d2 = self._d2(S, K, T, r, sigma)
        sqrt_T = math.sqrt(T)
        
        # Delta
        if option_type == OptionType.CALL:
            delta = norm.cdf(d1)
        else:
            delta = norm.cdf(d1) - 1
        
        # Gamma (same for calls and puts)
        gamma = norm.pdf(d1) / (S * sigma * sqrt_T)
        
        # Theta (per day)
        term1 = -(S * norm.pdf(d1) * sigma) / (2 * sqrt_T)
        if option_type == OptionType.CALL:
            term2 = r * K * math.exp(-r * T) * norm.cdf(d2)
            theta = (term1 - term2) / 365  # Convert to per day
        else:
            term2 = r * K * math.exp(-r * T) * norm.cdf(-d2)
            theta = (term1 + term2) / 365  # Convert to per day
        
        # Vega (per 1% vol change)
        vega = S * sqrt_T * norm.pdf(d1) / 100  # Per 1% change
        
        # Rho (per 1% rate change)
        if option_type == OptionType.CALL:
            rho = K * T * math.exp(-r * T) * norm.cdf(d2) / 100
        else:
            rho = -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100
        
        # Theoretical price
        theo_price = self.black_scholes_price(S, K, T, sigma, option_type, r)
        
        # Intrinsic and time value
        if option_type == OptionType.CALL:
            intrinsic = max(0, S - K)
        else:
            intrinsic = max(0, K - S)
        time_value = theo_price - intrinsic
        
        # Calculate IV if market price provided
        iv = None
        if market_price is not None and market_price > 0:
            iv = self.calculate_implied_volatility(S, K, T, market_price, option_type, r)
        
        return GreeksResult(
            delta=round(delta, 4),
            gamma=round(gamma, 6),
            theta=round(theta, 2),
            vega=round(vega, 2),
            rho=round(rho, 4),
            iv=round(iv, 4) if iv else None,
            theoretical_price=round(theo_price, 2),
            intrinsic_value=round(intrinsic, 2),
            time_value=round(time_value, 2),
        )
    
    def calculate_implied_volatility(
        self,
        spot: float,
        strike: float,
        time_to_expiry: float,
        market_price: float,
        option_type: OptionType,
        risk_free_rate: float = None,
        precision: float = 0.0001,
    ) -> Optional[float]:
        """
        Calculate Implied Volatility from market price.
        
        Uses Brent's method for fast convergence.
        
        Args:
            spot: Current underlying price
            strike: Option strike price
            time_to_expiry: Time to expiry in years
            market_price: Current market price of option
            option_type: CALL or PUT
            risk_free_rate: Override risk-free rate
            precision: Desired precision
        
        Returns:
            Implied volatility (e.g., 0.20 for 20%) or None if cannot calculate
        """
        r = risk_free_rate or self.risk_free_rate
        
        if market_price <= 0 or time_to_expiry <= 0:
            return None
        
        # Check intrinsic value bounds
        if option_type == OptionType.CALL:
            intrinsic = max(0, spot - strike)
        else:
            intrinsic = max(0, strike - spot)
        
        if market_price < intrinsic:
            return None  # Price below intrinsic - invalid
        
        def objective(sigma):
            """Objective function: theoretical price - market price."""
            theo = self.black_scholes_price(spot, strike, time_to_expiry, sigma, option_type, r)
            return theo - market_price
        
        try:
            # Search for IV in range [0.01, 5.0] (1% to 500% volatility)
            iv = brentq(objective, 0.01, 5.0, xtol=precision)
            return iv
        except (ValueError, RuntimeError):
            # Could not find IV in range
            return None
    
    def days_to_years(self, days: int) -> float:
        """Convert days to years for calculations."""
        return days / 365.0
    
    def get_lot_size(self, symbol: str) -> int:
        """Get standard lot size for an index/stock."""
        symbol_upper = symbol.upper().replace(" ", "")
        if symbol_upper in ["NIFTY", "NIFTY50"]:
            return self.LOT_SIZES["NIFTY"]
        elif symbol_upper in ["BANKNIFTY", "NIFTYBANK"]:
            return self.LOT_SIZES["BANKNIFTY"]
        elif symbol_upper == "FINNIFTY":
            return self.LOT_SIZES["FINNIFTY"]
        elif symbol_upper == "MIDCPNIFTY":
            return self.LOT_SIZES["MIDCPNIFTY"]
        elif symbol_upper == "SENSEX":
            return self.LOT_SIZES["SENSEX"]
        else:
            # Default to Nifty lot size for unknown
            return 50
    
    def analyze_option_chain(
        self,
        chain_data: Dict,
        spot_price: float,
        time_to_expiry_days: int,
    ) -> Dict:
        """
        Analyze a full option chain and calculate Greeks for all strikes.
        
        Args:
            chain_data: Option chain data from Upstox
            spot_price: Current underlying price
            time_to_expiry_days: Days to expiry
        
        Returns:
            Enhanced chain data with calculated Greeks
        """
        T = self.days_to_years(time_to_expiry_days)
        analyzed = {
            "spot": spot_price,
            "time_to_expiry_days": time_to_expiry_days,
            "atm_strike": self._find_atm_strike(chain_data, spot_price),
            "calls": [],
            "puts": [],
            "pcr": 0,  # Put-Call Ratio
            "max_pain": 0,
            "iv_skew": {},
        }
        
        total_call_oi = 0
        total_put_oi = 0
        
        # Process chain
        for strike_data in chain_data.get("data", []):
            strike = strike_data.get("strike_price", 0)
            
            # Call data
            call_info = strike_data.get("call_options", {})
            if call_info:
                call_price = call_info.get("ltp", 0)
                call_oi = call_info.get("oi", 0)
                total_call_oi += call_oi
                
                call_greeks = self.calculate_greeks(
                    spot=spot_price,
                    strike=strike,
                    time_to_expiry=T,
                    volatility=0.15,  # Initial guess, will be updated with IV
                    option_type=OptionType.CALL,
                    market_price=call_price,
                )
                
                analyzed["calls"].append({
                    "strike": strike,
                    "ltp": call_price,
                    "oi": call_oi,
                    "iv": call_greeks.iv,
                    "delta": call_greeks.delta,
                    "gamma": call_greeks.gamma,
                    "theta": call_greeks.theta,
                    "vega": call_greeks.vega,
                    "moneyness": "ITM" if spot_price > strike else "OTM" if spot_price < strike else "ATM",
                })
            
            # Put data
            put_info = strike_data.get("put_options", {})
            if put_info:
                put_price = put_info.get("ltp", 0)
                put_oi = put_info.get("oi", 0)
                total_put_oi += put_oi
                
                put_greeks = self.calculate_greeks(
                    spot=spot_price,
                    strike=strike,
                    time_to_expiry=T,
                    volatility=0.15,
                    option_type=OptionType.PUT,
                    market_price=put_price,
                )
                
                analyzed["puts"].append({
                    "strike": strike,
                    "ltp": put_price,
                    "oi": put_oi,
                    "iv": put_greeks.iv,
                    "delta": put_greeks.delta,
                    "gamma": put_greeks.gamma,
                    "theta": put_greeks.theta,
                    "vega": put_greeks.vega,
                    "moneyness": "ITM" if spot_price < strike else "OTM" if spot_price > strike else "ATM",
                })
        
        # Calculate PCR
        if total_call_oi > 0:
            analyzed["pcr"] = round(total_put_oi / total_call_oi, 2)
        
        # Calculate Max Pain (strike where option writers have minimum loss)
        analyzed["max_pain"] = self._calculate_max_pain(analyzed["calls"], analyzed["puts"], spot_price)
        
        # IV Skew analysis
        analyzed["iv_skew"] = self._analyze_iv_skew(analyzed["calls"], analyzed["puts"], analyzed["atm_strike"])
        
        return analyzed
    
    def _find_atm_strike(self, chain_data: Dict, spot_price: float) -> float:
        """Find the At-The-Money strike closest to spot."""
        strikes = [s.get("strike_price", 0) for s in chain_data.get("data", [])]
        if not strikes:
            return spot_price
        return min(strikes, key=lambda x: abs(x - spot_price))
    
    def _calculate_max_pain(
        self,
        calls: list,
        puts: list,
        spot: float,
    ) -> float:
        """
        Calculate Max Pain strike.
        
        Max Pain = strike where total option buyer loss is maximum
        (i.e., where option sellers have minimum payout)
        """
        if not calls and not puts:
            return spot
        
        strikes = sorted(set([c["strike"] for c in calls] + [p["strike"] for p in puts]))
        
        min_pain = float('inf')
        max_pain_strike = strikes[0] if strikes else spot
        
        for test_strike in strikes:
            total_pain = 0
            
            # Pain from calls (ITM calls pay out)
            for call in calls:
                if test_strike > call["strike"]:
                    total_pain += (test_strike - call["strike"]) * call["oi"]
            
            # Pain from puts (ITM puts pay out)
            for put in puts:
                if test_strike < put["strike"]:
                    total_pain += (put["strike"] - test_strike) * put["oi"]
            
            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = test_strike
        
        return max_pain_strike
    
    def _analyze_iv_skew(
        self,
        calls: list,
        puts: list,
        atm_strike: float,
    ) -> Dict:
        """
        Analyze IV skew pattern.
        
        Returns:
            Skew analysis: flat, smile, smirk, etc.
        """
        # Get ATM IVs
        atm_call_iv = None
        atm_put_iv = None
        
        for call in calls:
            if call["strike"] == atm_strike:
                atm_call_iv = call["iv"]
                break
        
        for put in puts:
            if put["strike"] == atm_strike:
                atm_put_iv = put["iv"]
                break
        
        atm_iv = atm_call_iv or atm_put_iv or 0.15
        
        # Get OTM IVs (5% away from ATM)
        otm_call_strike = atm_strike * 1.05
        otm_put_strike = atm_strike * 0.95
        
        otm_call_iv = None
        otm_put_iv = None
        
        for call in calls:
            if abs(call["strike"] - otm_call_strike) < atm_strike * 0.02:
                otm_call_iv = call["iv"]
                break
        
        for put in puts:
            if abs(put["strike"] - otm_put_strike) < atm_strike * 0.02:
                otm_put_iv = put["iv"]
                break
        
        skew_type = "FLAT"
        if otm_call_iv and otm_put_iv and atm_iv:
            if otm_put_iv > atm_iv and otm_call_iv > atm_iv:
                skew_type = "SMILE"  # Both sides higher - volatility smile
            elif otm_put_iv > atm_iv and otm_call_iv < atm_iv:
                skew_type = "PUT_SKEW"  # Bearish - puts expensive
            elif otm_call_iv > atm_iv and otm_put_iv < atm_iv:
                skew_type = "CALL_SKEW"  # Bullish - calls expensive
        
        return {
            "type": skew_type,
            "atm_iv": atm_iv,
            "otm_call_iv": otm_call_iv,
            "otm_put_iv": otm_put_iv,
            "interpretation": self._interpret_skew(skew_type),
        }
    
    def _interpret_skew(self, skew_type: str) -> str:
        """Interpret IV skew for trading."""
        interpretations = {
            "FLAT": "Neutral market expectations",
            "SMILE": "Expecting big move in either direction (high volatility event)",
            "PUT_SKEW": "Bearish sentiment - downside protection demand high",
            "CALL_SKEW": "Bullish sentiment - upside call buying active",
        }
        return interpretations.get(skew_type, "Unknown pattern")


# Singleton
_greeks_calculator = None

def get_greeks_calculator() -> OptionsGreeksCalculator:
    """Get or create the Greeks calculator singleton."""
    global _greeks_calculator
    if _greeks_calculator is None:
        _greeks_calculator = OptionsGreeksCalculator()
    return _greeks_calculator
