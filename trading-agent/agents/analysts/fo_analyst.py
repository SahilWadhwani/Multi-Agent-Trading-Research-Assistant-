"""
F&O Analyst Agent (LLM-Powered)

Analyzes derivatives data and provides trading insights:
- Option chain analysis with Greeks interpretation
- PCR and OI analysis
- Max Pain and expected move calculations
- IV analysis and skew patterns
- Recommends optimal strikes and strategies

Uses LLM (GPT-5.5/Gemini via Proxima or Ollama) for intelligent analysis.
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from data_feeds.fo_data_feed import get_fo_data_feed
from data_feeds.options_greeks import get_greeks_calculator, OptionType
from llm.client import get_llm_client
from llm.schemas import parse_json_response, normalize_fo_from_json, FO_ANALYSIS_JSON_SCHEMA


class FOAnalyst:
    """
    F&O Analyst Agent - LLM-powered derivatives analysis.
    
    Responsibilities:
    - Analyze option chains and interpret OI data
    - Calculate and interpret Greeks
    - Identify support/resistance from OI
    - Detect IV patterns and skew
    - Recommend option strategies
    - Provide expected move estimates
    """
    
    def __init__(self):
        self.fo_feed = get_fo_data_feed()
        self.greeks_calc = get_greeks_calculator()
        self.llm = get_llm_client()
        self._llm_available = self.llm.is_available()
        
        if self._llm_available:
            print(f"   📊 F&O Analyst: LLM-powered ({self.llm.model})")
        else:
            print("   📊 F&O Analyst: Rule-based (install Proxima/Ollama for AI analysis)")
    
    def analyze(self, symbol: str, expiry: str = None) -> Dict[str, Any]:
        """
        Perform comprehensive F&O analysis.
        
        Args:
            symbol: NIFTY, BANKNIFTY, etc.
            expiry: Expiry date (default: nearest)
        
        Returns:
            Complete F&O analysis report
        """
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol.upper(),
            "analyst": "F&O Analyst",
            "expiry": None,
            "spot_price": 0,
            "option_chain": {},
            "oi_analysis": {},
            "greeks_analysis": {},
            "iv_analysis": {},
            "expected_move": {},
            "support_resistance": {},
            "signals": [],
            "strategy_suggestions": [],
            "bias": "NEUTRAL",
            "confidence": 0.0,
            "summary": "",
            "llm_analysis": None,
        }
        
        # Fetch option chain
        chain = self.fo_feed.get_option_chain(symbol, expiry)
        
        if "error" in chain:
            report["error"] = chain["error"]
            report["summary"] = f"Could not analyze {symbol}: {chain['error']}"
            return report
        
        report["expiry"] = chain["expiry"]
        report["spot_price"] = chain["spot_price"]
        report["days_to_expiry"] = chain["days_to_expiry"]
        report["lot_size"] = chain["lot_size"]
        report["atm_strike"] = chain["atm_strike"]
        
        # OI Analysis
        report["oi_analysis"] = self._analyze_oi(chain)
        
        # Greeks Analysis
        report["greeks_analysis"] = self._analyze_greeks(chain)
        
        # IV Analysis
        report["iv_analysis"] = self._analyze_iv(chain)
        
        # Expected Move
        report["expected_move"] = self._calculate_expected_move(chain)
        
        # Support/Resistance from OI
        report["support_resistance"] = self._find_oi_levels(chain)
        
        # Generate Signals
        report["signals"] = self._generate_signals(report)
        
        # Determine Bias
        report["bias"], report["confidence"] = self._determine_bias(report)
        
        # Strategy Suggestions
        report["strategy_suggestions"] = self._suggest_strategies(report)
        
        # LLM Analysis (if available)
        if self._llm_available:
            report["llm_analysis"] = self._get_llm_analysis(symbol, report)
            if report["llm_analysis"]:
                # LLM can override/refine bias
                llm_bias = report["llm_analysis"].get("bias")
                if llm_bias:
                    report["bias"] = llm_bias
                    report["confidence"] = min(1.0, report["confidence"] + 0.1)
        
        # Generate Summary
        report["summary"] = self._generate_summary(report)
        
        return report
    
    def _analyze_oi(self, chain: Dict) -> Dict:
        """Analyze Open Interest patterns."""
        summary = chain.get("summary", {})
        
        pcr_oi = summary.get("pcr_oi", 1.0)
        total_call_oi = summary.get("total_call_oi", 0)
        total_put_oi = summary.get("total_put_oi", 0)
        highest_call_strike = summary.get("highest_oi_call_strike", chain["atm_strike"])
        highest_put_strike = summary.get("highest_oi_put_strike", chain["atm_strike"])
        
        # PCR interpretation
        if pcr_oi > 1.5:
            pcr_signal = "VERY_BULLISH"
            pcr_interpretation = "High put writing indicates strong support. Bulls in control."
        elif pcr_oi > 1.2:
            pcr_signal = "BULLISH"
            pcr_interpretation = "More puts than calls. Mildly bullish bias."
        elif pcr_oi < 0.7:
            pcr_signal = "VERY_BEARISH"
            pcr_interpretation = "High call writing indicates resistance. Bears in control."
        elif pcr_oi < 0.9:
            pcr_signal = "BEARISH"
            pcr_interpretation = "More calls than puts. Mildly bearish bias."
        else:
            pcr_signal = "NEUTRAL"
            pcr_interpretation = "Balanced PCR. No clear directional bias."
        
        return {
            "pcr_oi": pcr_oi,
            "pcr_volume": summary.get("pcr_volume", 1.0),
            "pcr_signal": pcr_signal,
            "pcr_interpretation": pcr_interpretation,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "highest_oi_call_strike": highest_call_strike,
            "highest_oi_put_strike": highest_put_strike,
            "immediate_resistance": highest_call_strike,
            "immediate_support": highest_put_strike,
        }
    
    def _analyze_greeks(self, chain: Dict) -> Dict:
        """Analyze Greeks patterns across the chain."""
        calls = chain.get("calls", [])
        puts = chain.get("puts", [])
        atm_strike = chain["atm_strike"]
        
        # Find ATM options
        atm_call = next((c for c in calls if c["strike"] == atm_strike), None)
        atm_put = next((p for p in puts if p["strike"] == atm_strike), None)
        
        # Calculate net delta (market maker perspective)
        total_call_delta = sum((c.get("delta") or 0) * c.get("oi", 0) for c in calls)
        total_put_delta = sum((p.get("delta") or 0) * p.get("oi", 0) for p in puts)
        net_delta = total_call_delta + total_put_delta
        
        # Calculate total gamma
        total_gamma = sum((c.get("gamma") or 0) * c.get("oi", 0) for c in calls)
        total_gamma += sum((p.get("gamma") or 0) * p.get("oi", 0) for p in puts)
        
        # Delta interpretation
        if abs(net_delta) < total_call_delta * 0.1:
            delta_bias = "NEUTRAL"
        elif net_delta > 0:
            delta_bias = "BULLISH"
        else:
            delta_bias = "BEARISH"
        
        return {
            "atm_call_delta": atm_call.get("delta") if atm_call else None,
            "atm_put_delta": atm_put.get("delta") if atm_put else None,
            "atm_call_theta": atm_call.get("theta") if atm_call else None,
            "atm_put_theta": atm_put.get("theta") if atm_put else None,
            "atm_call_vega": atm_call.get("vega") if atm_call else None,
            "atm_gamma": atm_call.get("gamma") if atm_call else None,
            "net_delta_exposure": net_delta,
            "total_gamma_exposure": total_gamma,
            "delta_bias": delta_bias,
            "gamma_risk": "HIGH" if abs(total_gamma) > 10000 else "MODERATE" if abs(total_gamma) > 5000 else "LOW",
        }
    
    def _analyze_iv(self, chain: Dict) -> Dict:
        """Analyze Implied Volatility patterns."""
        calls = chain.get("calls", [])
        puts = chain.get("puts", [])
        atm_strike = chain["atm_strike"]
        summary = chain.get("summary", {})
        
        # Get ATM IV
        atm_call = next((c for c in calls if c["strike"] == atm_strike), None)
        atm_put = next((p for p in puts if p["strike"] == atm_strike), None)
        
        atm_iv = None
        if atm_call and atm_call.get("iv"):
            atm_iv = atm_call["iv"]
        elif atm_put and atm_put.get("iv"):
            atm_iv = atm_put["iv"]
        
        # Calculate average IV
        all_ivs = [c.get("iv") for c in calls if c.get("iv")] + [p.get("iv") for p in puts if p.get("iv")]
        avg_iv = sum(all_ivs) / len(all_ivs) if all_ivs else None
        
        # IV skew
        iv_skew = summary.get("iv_skew", "UNKNOWN")
        
        # IV interpretation
        if atm_iv:
            if atm_iv > 0.25:
                iv_level = "HIGH"
                iv_interpretation = "High volatility - options expensive. Consider selling strategies."
            elif atm_iv > 0.15:
                iv_level = "MODERATE"
                iv_interpretation = "Normal volatility. Directional plays viable."
            else:
                iv_level = "LOW"
                iv_interpretation = "Low volatility - options cheap. Consider buying strategies."
        else:
            iv_level = "UNKNOWN"
            iv_interpretation = "Could not determine IV level."
        
        return {
            "atm_iv": round(atm_iv * 100, 2) if atm_iv else None,  # As percentage
            "avg_iv": round(avg_iv * 100, 2) if avg_iv else None,
            "iv_level": iv_level,
            "iv_interpretation": iv_interpretation,
            "iv_skew": iv_skew,
            "skew_interpretation": self._interpret_skew(iv_skew),
        }
    
    def _interpret_skew(self, skew: str) -> str:
        """Interpret IV skew pattern."""
        interpretations = {
            "FLAT": "Market expects symmetric move. No clear direction.",
            "SMILE": "High volatility expected. Big move anticipated either way.",
            "PUT_SKEW": "Downside protection expensive. Bearish hedging active.",
            "CALL_SKEW": "Upside calls expensive. Bullish speculation active.",
            "UNKNOWN": "Could not determine skew pattern.",
        }
        return interpretations.get(skew, "Unknown pattern")
    
    def _calculate_expected_move(self, chain: Dict) -> Dict:
        """Calculate expected move from straddle price."""
        straddle = self.fo_feed.get_straddle_price(chain["symbol"], chain["atm_strike"], chain["expiry"])
        
        if "error" in straddle:
            return {"error": straddle["error"]}
        
        return {
            "straddle_price": straddle["straddle_price"],
            "expected_move_points": straddle["expected_move_points"],
            "expected_move_percent": straddle["expected_move_percent"],
            "upper_breakeven": straddle["upper_breakeven"],
            "lower_breakeven": straddle["lower_breakeven"],
            "range": f"{straddle['lower_breakeven']:.0f} - {straddle['upper_breakeven']:.0f}",
        }
    
    def _find_oi_levels(self, chain: Dict) -> Dict:
        """Find support/resistance levels from OI data."""
        calls = chain.get("calls", [])
        puts = chain.get("puts", [])
        spot = chain["spot_price"]
        
        # Sort by OI to find key levels
        call_levels = sorted(calls, key=lambda x: x.get("oi", 0), reverse=True)[:5]
        put_levels = sorted(puts, key=lambda x: x.get("oi", 0), reverse=True)[:5]
        
        # Resistance = High OI call strikes above spot
        resistance_levels = [c["strike"] for c in call_levels if c["strike"] > spot]
        
        # Support = High OI put strikes below spot
        support_levels = [p["strike"] for p in put_levels if p["strike"] < spot]
        
        return {
            "key_resistance": resistance_levels[:3],
            "key_support": support_levels[:3],
            "immediate_resistance": resistance_levels[0] if resistance_levels else chain["atm_strike"] + chain["strike_interval"],
            "immediate_support": support_levels[0] if support_levels else chain["atm_strike"] - chain["strike_interval"],
            "max_pain": chain.get("summary", {}).get("max_pain", chain["atm_strike"]),
        }
    
    def _generate_signals(self, report: Dict) -> List[Dict]:
        """Generate trading signals from analysis."""
        signals = []
        
        oi = report.get("oi_analysis", {})
        iv = report.get("iv_analysis", {})
        greeks = report.get("greeks_analysis", {})
        sr = report.get("support_resistance", {})
        expected = report.get("expected_move", {})
        
        # PCR Signal
        pcr_signal = oi.get("pcr_signal", "NEUTRAL")
        if pcr_signal in ["BULLISH", "VERY_BULLISH"]:
            signals.append({
                "type": "PCR",
                "direction": "BULLISH",
                "strength": "STRONG" if pcr_signal == "VERY_BULLISH" else "MODERATE",
                "description": oi.get("pcr_interpretation", ""),
            })
        elif pcr_signal in ["BEARISH", "VERY_BEARISH"]:
            signals.append({
                "type": "PCR",
                "direction": "BEARISH",
                "strength": "STRONG" if pcr_signal == "VERY_BEARISH" else "MODERATE",
                "description": oi.get("pcr_interpretation", ""),
            })
        
        # IV Signal
        iv_level = iv.get("iv_level", "MODERATE")
        if iv_level == "HIGH":
            signals.append({
                "type": "IV",
                "direction": "NEUTRAL",
                "strength": "STRONG",
                "description": "High IV - Consider selling premium",
            })
        elif iv_level == "LOW":
            signals.append({
                "type": "IV",
                "direction": "NEUTRAL",
                "strength": "MODERATE",
                "description": "Low IV - Consider buying options",
            })
        
        # Max Pain Signal
        max_pain = sr.get("max_pain", 0)
        spot = report.get("spot_price", 0)
        if max_pain and spot:
            diff_percent = ((max_pain - spot) / spot) * 100
            if diff_percent > 1:
                signals.append({
                    "type": "MAX_PAIN",
                    "direction": "BULLISH",
                    "strength": "MODERATE",
                    "description": f"Max Pain at {max_pain} ({diff_percent:.1f}% above spot)",
                })
            elif diff_percent < -1:
                signals.append({
                    "type": "MAX_PAIN",
                    "direction": "BEARISH",
                    "strength": "MODERATE",
                    "description": f"Max Pain at {max_pain} ({abs(diff_percent):.1f}% below spot)",
                })
        
        # Skew Signal
        iv_skew = iv.get("iv_skew", "FLAT")
        if iv_skew == "PUT_SKEW":
            signals.append({
                "type": "IV_SKEW",
                "direction": "BEARISH",
                "strength": "MODERATE",
                "description": "Put skew indicates hedging/fear",
            })
        elif iv_skew == "CALL_SKEW":
            signals.append({
                "type": "IV_SKEW",
                "direction": "BULLISH",
                "strength": "MODERATE",
                "description": "Call skew indicates bullish speculation",
            })
        
        return signals
    
    def _determine_bias(self, report: Dict) -> tuple:
        """Determine overall bias from all signals."""
        signals = report.get("signals", [])
        
        bullish_score = 0
        bearish_score = 0
        total_weight = 0
        
        for signal in signals:
            weight = 2 if signal["strength"] == "STRONG" else 1
            total_weight += weight
            
            if signal["direction"] == "BULLISH":
                bullish_score += weight
            elif signal["direction"] == "BEARISH":
                bearish_score += weight
        
        if total_weight == 0:
            return "NEUTRAL", 0.5
        
        # Calculate bias
        net_score = bullish_score - bearish_score
        confidence = min(1.0, 0.5 + abs(net_score) / (total_weight * 2))
        
        if net_score >= 2:
            return "BULLISH", confidence
        elif net_score <= -2:
            return "BEARISH", confidence
        else:
            return "NEUTRAL", 0.5
    
    def _suggest_strategies(self, report: Dict) -> List[Dict]:
        """Suggest option strategies based on analysis."""
        strategies = []
        
        bias = report.get("bias", "NEUTRAL")
        iv = report.get("iv_analysis", {})
        expected = report.get("expected_move", {})
        oi = report.get("oi_analysis", {})
        days_to_exp = report.get("days_to_expiry", 7)
        
        iv_level = iv.get("iv_level", "MODERATE")
        atm_strike = report.get("atm_strike", 0)
        spot = report.get("spot_price", 0)
        
        # Strategy suggestions based on bias + IV
        if bias == "BULLISH":
            if iv_level == "HIGH":
                strategies.append({
                    "name": "Bull Put Spread",
                    "type": "CREDIT_SPREAD",
                    "legs": [
                        {"action": "SELL", "strike": atm_strike, "type": "PE"},
                        {"action": "BUY", "strike": atm_strike - 100, "type": "PE"},
                    ],
                    "max_profit": "Premium received",
                    "max_loss": "Spread width - Premium",
                    "reasoning": "Bullish bias + High IV = Sell puts",
                })
            else:
                strategies.append({
                    "name": "Long Call",
                    "type": "DIRECTIONAL",
                    "legs": [
                        {"action": "BUY", "strike": atm_strike, "type": "CE"},
                    ],
                    "max_profit": "Unlimited",
                    "max_loss": "Premium paid",
                    "reasoning": "Bullish bias + Normal/Low IV",
                })
        
        elif bias == "BEARISH":
            if iv_level == "HIGH":
                strategies.append({
                    "name": "Bear Call Spread",
                    "type": "CREDIT_SPREAD",
                    "legs": [
                        {"action": "SELL", "strike": atm_strike, "type": "CE"},
                        {"action": "BUY", "strike": atm_strike + 100, "type": "CE"},
                    ],
                    "max_profit": "Premium received",
                    "max_loss": "Spread width - Premium",
                    "reasoning": "Bearish bias + High IV = Sell calls",
                })
            else:
                strategies.append({
                    "name": "Long Put",
                    "type": "DIRECTIONAL",
                    "legs": [
                        {"action": "BUY", "strike": atm_strike, "type": "PE"},
                    ],
                    "max_profit": "Strike - Premium (if spot goes to 0)",
                    "max_loss": "Premium paid",
                    "reasoning": "Bearish bias + Normal/Low IV",
                })
        
        else:  # NEUTRAL
            if iv_level == "HIGH":
                strategies.append({
                    "name": "Short Straddle",
                    "type": "PREMIUM_SELLING",
                    "legs": [
                        {"action": "SELL", "strike": atm_strike, "type": "CE"},
                        {"action": "SELL", "strike": atm_strike, "type": "PE"},
                    ],
                    "max_profit": "Premium received",
                    "max_loss": "Unlimited",
                    "reasoning": "Neutral + High IV = Sell volatility",
                    "warning": "High risk - use with strict SL",
                })
                strategies.append({
                    "name": "Iron Condor",
                    "type": "DEFINED_RISK",
                    "legs": [
                        {"action": "SELL", "strike": atm_strike + 100, "type": "CE"},
                        {"action": "BUY", "strike": atm_strike + 200, "type": "CE"},
                        {"action": "SELL", "strike": atm_strike - 100, "type": "PE"},
                        {"action": "BUY", "strike": atm_strike - 200, "type": "PE"},
                    ],
                    "max_profit": "Net premium",
                    "max_loss": "Spread width - Premium",
                    "reasoning": "Neutral + defined risk + High IV",
                })
            else:
                strategies.append({
                    "name": "Long Straddle",
                    "type": "VOLATILITY_BUYING",
                    "legs": [
                        {"action": "BUY", "strike": atm_strike, "type": "CE"},
                        {"action": "BUY", "strike": atm_strike, "type": "PE"},
                    ],
                    "max_profit": "Unlimited",
                    "max_loss": "Premium paid",
                    "reasoning": "Neutral + Low IV = Buy volatility for breakout",
                })
        
        # Weekly expiry specific
        if days_to_exp <= 3:
            strategies.append({
                "name": "Weekly Expiry Play",
                "type": "EXPIRY_SPECIAL",
                "note": f"Only {days_to_exp} days to expiry - Theta decay accelerates",
                "suggestion": "Prefer selling OTM options or stay directional",
            })
        
        return strategies
    
    def _get_llm_analysis(self, symbol: str, report: Dict) -> Optional[Dict]:
        """Get LLM-powered analysis."""
        try:
            context = {
                "symbol": symbol,
                "spot_price": report["spot_price"],
                "expiry": report["expiry"],
                "days_to_expiry": report["days_to_expiry"],
                "pcr": report["oi_analysis"]["pcr_oi"],
                "max_pain": report["support_resistance"]["max_pain"],
                "atm_iv": report["iv_analysis"]["atm_iv"],
                "iv_level": report["iv_analysis"]["iv_level"],
                "iv_skew": report["iv_analysis"]["iv_skew"],
                "expected_move": report["expected_move"].get("expected_move_percent", 0),
                "signals": report["signals"],
                "rule_based_bias": report["bias"],
            }
            
            # Phase F: Add OI buildup + VWAP context if available
            oi_section = ""
            oi_data = report.get("oi_analysis", {})
            if oi_data.get("oi_buildup") or oi_data.get("oi_bias"):
                oi_section = (
                    f"\nOI Buildup: {oi_data.get('oi_buildup', 'N/A')}\n"
                    f"OI Bias: {oi_data.get('oi_bias', 'N/A')}\n"
                    f"VWAP: {oi_data.get('vwap', 'N/A')} (spot {oi_data.get('spot_vs_vwap', 'unknown')} VWAP)\n"
                )

            prompt = f"""Analyze this F&O data for {symbol}:

Spot: {context['spot_price']}
Expiry: {context['expiry']} ({context['days_to_expiry']} days)
PCR (OI): {context['pcr']}
Max Pain: {context['max_pain']}
ATM IV: {context['atm_iv']}%
IV Level: {context['iv_level']}
IV Skew: {context['iv_skew']}
Expected Move: {context['expected_move']}%
Rule-based Bias: {context['rule_based_bias']}{oi_section}

Signals:
{chr(10).join([f"- {s['type']}: {s['direction']} ({s['strength']})" for s in context['signals']])}

Respond with ONLY valid JSON (no markdown) matching exactly:
{FO_ANALYSIS_JSON_SCHEMA}"""

            response = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                task_type="trade_decision",
                max_tokens=500,
            )
            if response.content.startswith("ERROR:"):
                return None

            parsed, _ = parse_json_response(response.content)
            if parsed:
                return normalize_fo_from_json(parsed, response.content, model=response.model)

            # Legacy fallback
            content = response.content.upper()
            if "BULLISH" in content and "BEARISH" not in content:
                llm_bias = "BULLISH"
            elif "BEARISH" in content:
                llm_bias = "BEARISH"
            else:
                llm_bias = "NEUTRAL"
            return {
                "bias": llm_bias,
                "analysis": response.content,
                "model": response.model,
            }
            
        except Exception as e:
            print(f"   ⚠️ LLM analysis failed: {e}")
            return None
    
    def _generate_summary(self, report: Dict) -> str:
        """Generate human-readable summary."""
        symbol = report["symbol"]
        spot = report["spot_price"]
        expiry = report["expiry"]
        days = report.get("days_to_expiry", 0)
        bias = report["bias"]
        confidence = report.get("confidence", 0.5)
        
        oi = report.get("oi_analysis", {})
        iv = report.get("iv_analysis", {})
        sr = report.get("support_resistance", {})
        expected = report.get("expected_move", {})
        
        summary = f"{symbol} F&O Analysis ({expiry}, {days}d to expiry)\n"
        summary += f"Spot: {spot:.2f} | Bias: {bias} ({confidence*100:.0f}%)\n"
        summary += f"PCR: {oi.get('pcr_oi', 'N/A')} ({oi.get('pcr_signal', 'N/A')})\n"
        summary += f"IV: {iv.get('atm_iv', 'N/A')}% ({iv.get('iv_level', 'N/A')}) | Skew: {iv.get('iv_skew', 'N/A')}\n"
        summary += f"Max Pain: {sr.get('max_pain', 'N/A')} | Expected Move: {expected.get('range', 'N/A')}\n"
        summary += f"Support: {sr.get('immediate_support', 'N/A')} | Resistance: {sr.get('immediate_resistance', 'N/A')}"
        
        return summary


# Singleton
_fo_analyst = None

def get_fo_analyst() -> FOAnalyst:
    """Get or create F&O Analyst singleton."""
    global _fo_analyst
    if _fo_analyst is None:
        _fo_analyst = FOAnalyst()
    return _fo_analyst
