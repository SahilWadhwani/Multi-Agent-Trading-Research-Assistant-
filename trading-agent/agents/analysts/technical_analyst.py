"""
Technical Analyst Agent
Analyzes price action, indicators, and patterns to provide technical signals.
Inspired by TradingAgents market_analyst but adapted for Indian markets.
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from data_feeds.technical_indicators import TechnicalIndicators
from data_feeds.market_data import get_market_feed


class TechnicalAnalyst:
    """
    Technical Analyst Agent.
    
    Responsibilities:
    - Analyze price action and chart patterns
    - Calculate and interpret technical indicators
    - Identify support/resistance levels
    - Detect trend direction and strength
    - Generate technical signals with confidence levels
    """
    
    def __init__(self):
        self.market_feed = get_market_feed()
        self.indicators = TechnicalIndicators()
    
    def analyze(self, symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """
        Perform comprehensive technical analysis on a symbol.
        
        Returns a structured report with:
        - Current price and quote data
        - Technical indicators
        - Trend analysis
        - Support/resistance levels
        - Technical signals
        - Confidence level
        - Recommended action bias
        """
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "exchange": exchange,
            "analyst": "Technical Analyst",
            "quote": {},
            "indicators": {},
            "trend_analysis": {},
            "support_resistance": {},
            "signals": [],
            "summary": "",
            "bias": "NEUTRAL",  # BULLISH, BEARISH, NEUTRAL
            "confidence": 0.0,  # 0.0 to 1.0
        }
        
        # Get current quote
        quote = self.market_feed.get_quote(symbol, exchange)
        if "error" in quote:
            report["error"] = quote["error"]
            report["summary"] = f"Unable to analyze {symbol}: {quote['error']}"
            return report
        
        report["quote"] = quote
        
        # Get historical data for indicator calculation
        historical = self.market_feed.get_historical_data(symbol, interval="day", days=200, exchange=exchange)
        
        if "error" in historical or not historical.get("closes"):
            # Use mock data for paper trading mode if no real data
            report["summary"] = f"Limited data for {symbol}. Using available quote data only."
            report["indicators"] = {
                "current_price": quote.get("ltp"),
                "change_percent": quote.get("change_percent"),
            }
            return report
        
        closes = historical["closes"]
        highs = historical["highs"]
        lows = historical["lows"]
        volumes = historical["volumes"]
        
        # Generate all technical signals
        signals = TechnicalIndicators.generate_signals(closes, highs, lows, volumes)
        
        report["indicators"] = signals["indicators"]
        report["indicators"]["current_price"] = quote.get("ltp", closes[-1])
        
        # Detailed trend analysis
        report["trend_analysis"] = self._analyze_trend(closes, signals)
        
        # Support and resistance
        report["support_resistance"] = self._find_support_resistance(closes, highs, lows)
        
        # Compile signals
        report["signals"] = self._compile_signals(signals, quote)
        
        # Generate summary and recommendation
        summary, bias, confidence = self._generate_summary(report)
        report["summary"] = summary
        report["bias"] = bias
        report["confidence"] = confidence
        
        return report
    
    def _analyze_trend(self, closes: List[float], signals: Dict) -> Dict[str, Any]:
        """Analyze trend direction and strength."""
        analysis = signals.get("analysis", {})
        
        # Trend strength based on multiple confirmations
        bullish_signals = 0
        bearish_signals = 0
        
        if "rsi" in analysis:
            if "BULLISH" in analysis["rsi"].upper() or "OVERSOLD" in analysis["rsi"].upper():
                bullish_signals += 1
            elif "BEARISH" in analysis["rsi"].upper() or "OVERBOUGHT" in analysis["rsi"].upper():
                bearish_signals += 1
        
        if "macd" in analysis:
            if "BULLISH" in analysis["macd"].upper():
                bullish_signals += 1
            else:
                bearish_signals += 1
        
        if "trend" in analysis:
            if analysis["trend"] == "BULLISH":
                bullish_signals += 2  # Weight trend higher
            elif analysis["trend"] == "BEARISH":
                bearish_signals += 2
        
        if "price_vs_sma50" in analysis:
            if "ABOVE" in analysis["price_vs_sma50"].upper():
                bullish_signals += 1
            else:
                bearish_signals += 1
        
        total = bullish_signals + bearish_signals
        trend_strength = abs(bullish_signals - bearish_signals) / max(total, 1)
        
        return {
            "direction": "BULLISH" if bullish_signals > bearish_signals else "BEARISH" if bearish_signals > bullish_signals else "NEUTRAL",
            "strength": trend_strength,
            "bullish_confirmations": bullish_signals,
            "bearish_confirmations": bearish_signals,
            "analysis_breakdown": analysis,
        }
    
    def _find_support_resistance(
        self,
        closes: List[float],
        highs: List[float],
        lows: List[float]
    ) -> Dict[str, Any]:
        """
        Find key support and resistance levels.
        Uses recent highs/lows and moving averages.
        """
        if not closes:
            return {}
        
        current_price = closes[-1]
        
        # Recent swing highs and lows (last 50 candles)
        recent_highs = highs[-50:] if len(highs) >= 50 else highs
        recent_lows = lows[-50:] if len(lows) >= 50 else lows
        
        # Moving average levels
        sma_20 = TechnicalIndicators.calculate_sma(closes, 20)[-1]
        sma_50 = TechnicalIndicators.calculate_sma(closes, 50)[-1] if len(closes) >= 50 else None
        sma_200 = TechnicalIndicators.calculate_sma(closes, 200)[-1] if len(closes) >= 200 else None
        
        # Find key levels
        resistance_levels = []
        support_levels = []
        
        if max(recent_highs) > current_price:
            resistance_levels.append({"level": max(recent_highs), "type": "Recent High"})
        
        if min(recent_lows) < current_price:
            support_levels.append({"level": min(recent_lows), "type": "Recent Low"})
        
        # Add MA levels
        for ma_name, ma_val in [("SMA 20", sma_20), ("SMA 50", sma_50), ("SMA 200", sma_200)]:
            if ma_val:
                if ma_val > current_price:
                    resistance_levels.append({"level": round(ma_val, 2), "type": ma_name})
                else:
                    support_levels.append({"level": round(ma_val, 2), "type": ma_name})
        
        # Sort by proximity to current price
        resistance_levels.sort(key=lambda x: x["level"])
        support_levels.sort(key=lambda x: x["level"], reverse=True)
        
        return {
            "current_price": round(current_price, 2),
            "nearest_resistance": resistance_levels[0] if resistance_levels else None,
            "nearest_support": support_levels[0] if support_levels else None,
            "all_resistance": resistance_levels[:3],
            "all_support": support_levels[:3],
        }
    
    def _compile_signals(self, signals: Dict, quote: Dict) -> List[Dict[str, Any]]:
        """Compile actionable signals from indicators."""
        compiled = []
        analysis = signals.get("analysis", {})
        indicators = signals.get("indicators", {})
        
        # RSI Signal
        rsi = indicators.get("rsi")
        if rsi is not None:
            signal = {
                "indicator": "RSI",
                "value": round(rsi, 2),
                "interpretation": analysis.get("rsi", "N/A"),
            }
            if rsi < 30:
                signal["action"] = "BUY"
                signal["strength"] = "STRONG"
            elif rsi > 70:
                signal["action"] = "SELL"
                signal["strength"] = "STRONG"
            elif rsi < 40:
                signal["action"] = "BUY"
                signal["strength"] = "WEAK"
            elif rsi > 60:
                signal["action"] = "SELL"
                signal["strength"] = "WEAK"
            else:
                signal["action"] = "HOLD"
                signal["strength"] = "NEUTRAL"
            compiled.append(signal)
        
        # MACD Signal
        macd = indicators.get("macd")
        macd_signal = indicators.get("macd_signal")
        if macd is not None and macd_signal is not None:
            signal = {
                "indicator": "MACD",
                "value": f"MACD: {round(macd, 2)}, Signal: {round(macd_signal, 2)}",
                "interpretation": analysis.get("macd", "N/A"),
            }
            if macd > macd_signal:
                signal["action"] = "BUY"
                signal["strength"] = "MEDIUM" if abs(macd - macd_signal) > 1 else "WEAK"
            else:
                signal["action"] = "SELL"
                signal["strength"] = "MEDIUM" if abs(macd - macd_signal) > 1 else "WEAK"
            compiled.append(signal)
        
        # Trend Signal
        trend = analysis.get("trend", "NEUTRAL")
        signal = {
            "indicator": "TREND",
            "value": trend,
            "interpretation": f"Overall trend is {trend}",
        }
        if trend == "BULLISH":
            signal["action"] = "BUY"
            signal["strength"] = "MEDIUM"
        elif trend == "BEARISH":
            signal["action"] = "SELL"
            signal["strength"] = "MEDIUM"
        else:
            signal["action"] = "HOLD"
            signal["strength"] = "NEUTRAL"
        compiled.append(signal)
        
        # Price vs Bollinger
        bollinger_analysis = analysis.get("bollinger")
        if bollinger_analysis:
            signal = {
                "indicator": "BOLLINGER",
                "value": bollinger_analysis,
                "interpretation": bollinger_analysis,
            }
            if "BELOW" in bollinger_analysis.upper():
                signal["action"] = "BUY"
                signal["strength"] = "STRONG"
            elif "ABOVE" in bollinger_analysis.upper():
                signal["action"] = "SELL"
                signal["strength"] = "STRONG"
            else:
                signal["action"] = "HOLD"
                signal["strength"] = "NEUTRAL"
            compiled.append(signal)
        
        return compiled
    
    def _generate_summary(self, report: Dict) -> tuple:
        """
        Generate human-readable summary and determine bias.
        Returns (summary, bias, confidence).
        """
        signals = report.get("signals", [])
        trend = report.get("trend_analysis", {})
        sr = report.get("support_resistance", {})
        quote = report.get("quote", {})
        
        current_price = quote.get("ltp", sr.get("current_price"))
        symbol = report.get("symbol")
        
        # Count signal directions
        buy_signals = sum(1 for s in signals if s.get("action") == "BUY")
        sell_signals = sum(1 for s in signals if s.get("action") == "SELL")
        strong_buy = sum(1 for s in signals if s.get("action") == "BUY" and s.get("strength") == "STRONG")
        strong_sell = sum(1 for s in signals if s.get("action") == "SELL" and s.get("strength") == "STRONG")
        
        # Determine bias
        total_signals = len(signals)
        if total_signals == 0:
            return f"Insufficient data to analyze {symbol}", "NEUTRAL", 0.0
        
        buy_ratio = buy_signals / total_signals
        sell_ratio = sell_signals / total_signals
        
        if buy_ratio > 0.6:
            bias = "BULLISH"
            confidence = min(0.5 + (strong_buy * 0.15) + (trend.get("strength", 0) * 0.2), 0.95)
        elif sell_ratio > 0.6:
            bias = "BEARISH"
            confidence = min(0.5 + (strong_sell * 0.15) + (trend.get("strength", 0) * 0.2), 0.95)
        else:
            bias = "NEUTRAL"
            confidence = 0.3 + (trend.get("strength", 0) * 0.2)
        
        # Build summary
        summary_parts = [f"Technical Analysis for {symbol} @ ₹{current_price}:"]
        
        # Trend
        trend_dir = trend.get("direction", "NEUTRAL")
        summary_parts.append(f"Trend: {trend_dir} (Strength: {trend.get('strength', 0):.1%})")
        
        # Key indicators
        indicators = report.get("indicators", {})
        if indicators.get("rsi"):
            rsi_val = indicators["rsi"]
            rsi_status = "Overbought" if rsi_val > 70 else "Oversold" if rsi_val < 30 else "Neutral"
            summary_parts.append(f"RSI: {rsi_val:.1f} ({rsi_status})")
        
        # Support/Resistance
        if sr.get("nearest_support"):
            summary_parts.append(f"Support: ₹{sr['nearest_support']['level']} ({sr['nearest_support']['type']})")
        if sr.get("nearest_resistance"):
            summary_parts.append(f"Resistance: ₹{sr['nearest_resistance']['level']} ({sr['nearest_resistance']['type']})")
        
        # Signal summary
        summary_parts.append(f"Signals: {buy_signals} Buy, {sell_signals} Sell, {total_signals - buy_signals - sell_signals} Hold")
        
        # Conclusion
        summary_parts.append(f"CONCLUSION: {bias} bias with {confidence:.0%} confidence")
        
        return " | ".join(summary_parts), bias, confidence


# Convenience function
def analyze_stock(symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
    """Quick analysis function."""
    analyst = TechnicalAnalyst()
    return analyst.analyze(symbol, exchange)
