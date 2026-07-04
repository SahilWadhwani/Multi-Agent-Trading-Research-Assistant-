"""
Smart Analyzer - LLM-Powered Analysis for QUANT-1

This replaces the basic keyword-matching with actual AI reasoning.
Uses local LLMs (Ollama) for intelligent analysis.

Falls back to rule-based analysis if no LLM is available.
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.client import get_llm_client, check_llm_status, LLMBackend


class SmartAnalyzer:
    """
    Intelligent analyzer using local LLMs.
    
    Capabilities:
    - News sentiment analysis with reasoning
    - Multi-source sentiment aggregation
    - Market context understanding
    - Trading signal generation with explanations
    
    Falls back to rule-based analysis if LLM unavailable.
    """
    
    def __init__(self):
        self.llm = get_llm_client()
        self._check_status()
    
    def _check_status(self):
        """Check and log LLM status."""
        status = check_llm_status()
        if status["available"]:
            print(f"🧠 LLM Active: {status['backend']} - {status['model']}")
        else:
            print("⚠️  No LLM available - using rule-based fallback")
            print("   Install Ollama for smart analysis: https://ollama.ai")
    
    def analyze_news_smart(
        self,
        news_items: List[Dict],
        symbol: str,
    ) -> Dict[str, Any]:
        """
        Analyze news using LLM reasoning.
        
        This is the SMART version - uses actual AI to understand
        news context, implications, and trading signals.
        
        Args:
            news_items: List of news items with title/description
            symbol: Stock symbol being analyzed
        
        Returns:
            Intelligent analysis with reasoning
        """
        result = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "analyzer": "SmartAnalyzer",
            "llm_used": False,
        }
        
        if not news_items:
            result.update({
                "sentiment": "NEUTRAL",
                "confidence": 0,
                "reasoning": "No news data available for analysis",
                "signals": [],
            })
            return result
        
        # Try LLM analysis
        if self.llm.is_available():
            llm_result = self.llm.analyze_news(news_items, symbol)
            result.update(llm_result)
            result["method"] = "LLM_REASONING"
        else:
            # Fallback to rule-based
            result.update(self._fallback_news_analysis(news_items, symbol))
            result["method"] = "RULE_BASED_FALLBACK"
        
        return result
    
    def aggregate_sentiment_smart(
        self,
        technical_data: Dict,
        news_data: Dict,
        symbol: str,
    ) -> Dict[str, Any]:
        """
        Intelligently aggregate sentiment from multiple sources.
        
        Uses LLM to reason about:
        - Agreement/disagreement between sources
        - Which source to weight more given context
        - Overall trading implication
        
        Args:
            technical_data: Technical analysis results
            news_data: News analysis results
            symbol: Stock symbol
        
        Returns:
            Aggregated sentiment with reasoning
        """
        result = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "sources": {
                "technical": technical_data.get("bias", "N/A"),
                "news": news_data.get("sentiment", "N/A"),
            },
        }
        
        if self.llm.is_available():
            llm_result = self.llm.aggregate_sentiment(
                technical_sentiment=technical_data,
                news_sentiment=news_data,
                symbol=symbol,
            )
            result.update(llm_result)
            result["method"] = "LLM_REASONING"
        else:
            result.update(self._fallback_aggregation(technical_data, news_data))
            result["method"] = "RULE_BASED_FALLBACK"
        
        return result
    
    def get_trading_insight(
        self,
        symbol: str,
        price: float,
        technical_report: Dict,
        news_report: Dict,
        sentiment_report: Dict,
    ) -> Dict[str, Any]:
        """
        Generate actionable trading insight using LLM.
        
        This is the highest-level analysis that combines all inputs
        and produces a specific trading recommendation.
        """
        if not self.llm.is_available():
            return {
                "symbol": symbol,
                "decision": "HOLD",
                "confidence": 0,
                "reasoning": "LLM not available for intelligent analysis",
                "llm_used": False,
            }
        
        prompt = f"""As a senior trading analyst, provide a specific trading recommendation for {symbol} at ₹{price:.2f}.

TECHNICAL ANALYSIS:
- Trend: {technical_report.get('trend_analysis', {}).get('direction', 'N/A')}
- Bias: {technical_report.get('bias', 'N/A')}
- Confidence: {technical_report.get('confidence', 0):.0%}
- RSI: {technical_report.get('indicators', {}).get('rsi', 'N/A')}
- Support: {technical_report.get('support_resistance', {}).get('nearest_support', {}).get('level', 'N/A')}
- Resistance: {technical_report.get('support_resistance', {}).get('nearest_resistance', {}).get('level', 'N/A')}

NEWS ANALYSIS:
- Sentiment: {news_report.get('sentiment', 'N/A')}
- Confidence: {news_report.get('confidence', 0):.0%}
- Key Factors: {news_report.get('key_factors', [])}

AGGREGATED SENTIMENT:
- Overall: {sentiment_report.get('overall_sentiment', 'N/A')}
- Signal Alignment: {sentiment_report.get('signal_alignment', 'N/A')}

Based on this analysis, provide your trading recommendation following the format specified.
Remember: If uncertain, recommend NO TRADE. Capital preservation is priority."""

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            task_type="trade_decision",
            temperature=0.2,  # Low temp for consistent decisions
        )
        
        return self._parse_trading_insight(response.content, symbol, price)
    
    def _parse_trading_insight(
        self,
        response: str,
        symbol: str,
        price: float,
    ) -> Dict[str, Any]:
        """Parse LLM trading insight response."""
        result = {
            "symbol": symbol,
            "current_price": price,
            "decision": "HOLD",
            "confidence": 50,
            "entry": None,
            "stop_loss": None,
            "target": None,
            "position_size": None,
            "rationale": "",
            "raw_response": response,
            "llm_used": True,
        }
        
        lines = response.upper().split("\n")
        for line in lines:
            if "DECISION:" in line:
                for dec in ["BUY", "SELL", "HOLD", "NO_TRADE"]:
                    if dec in line:
                        result["decision"] = dec
                        break
            elif "CONFIDENCE:" in line:
                try:
                    conf = int(''.join(filter(str.isdigit, line.split(":")[-1])))
                    result["confidence"] = min(100, max(0, conf))
                except:
                    pass
            elif "ENTRY:" in line:
                try:
                    result["entry"] = float(''.join(c for c in line.split(":")[-1] if c.isdigit() or c == '.'))
                except:
                    pass
            elif "STOP_LOSS:" in line or "STOP-LOSS:" in line:
                try:
                    result["stop_loss"] = float(''.join(c for c in line.split(":")[-1] if c.isdigit() or c == '.'))
                except:
                    pass
            elif "TARGET:" in line:
                try:
                    result["target"] = float(''.join(c for c in line.split(":")[-1] if c.isdigit() or c == '.'))
                except:
                    pass
            elif "POSITION_SIZE:" in line or "POSITION SIZE:" in line:
                try:
                    result["position_size"] = float(''.join(c for c in line.split(":")[-1] if c.isdigit() or c == '.'))
                except:
                    pass
        
        # Extract rationale
        if "RATIONALE:" in response.upper():
            idx = response.upper().index("RATIONALE:")
            rationale = response[idx:].split(":", 1)[-1].strip()
            # Get until next section or end
            for marker in ["ENTRY:", "STOP", "TARGET:", "POSITION"]:
                if marker in rationale.upper():
                    rationale = rationale[:rationale.upper().index(marker)]
            result["rationale"] = rationale.strip()[:500]
        
        return result
    
    def _fallback_news_analysis(
        self,
        news_items: List[Dict],
        symbol: str,
    ) -> Dict[str, Any]:
        """Rule-based fallback when LLM unavailable."""
        from data_feeds.sentiment_analyzer import get_sentiment_analyzer
        
        analyzer = get_sentiment_analyzer()
        batch_result = analyzer.analyze_news_batch(news_items)
        
        return {
            "sentiment": batch_result.get("overall_sentiment", "NEUTRAL"),
            "confidence": int(batch_result.get("confidence", 0) * 100),
            "key_factors": batch_result.get("top_positive_signals", []) + batch_result.get("top_negative_signals", []),
            "trading_implication": "Rule-based analysis - install Ollama for intelligent insights",
            "risks": ["Analysis limited without LLM reasoning"],
            "llm_used": False,
        }
    
    def _fallback_aggregation(
        self,
        technical_data: Dict,
        news_data: Dict,
    ) -> Dict[str, Any]:
        """Rule-based fallback for aggregation."""
        tech_score = {"BULLISH": 0.6, "BEARISH": -0.6, "NEUTRAL": 0}.get(
            technical_data.get("bias", "NEUTRAL"), 0
        )
        
        news_score_map = {
            "VERY_BULLISH": 0.8, "BULLISH": 0.4, "NEUTRAL": 0,
            "BEARISH": -0.4, "VERY_BEARISH": -0.8,
        }
        news_score = news_score_map.get(news_data.get("sentiment", "NEUTRAL"), 0)
        
        # Simple weighted average
        combined = tech_score * 0.6 + news_score * 0.4
        
        if combined > 0.3:
            overall = "BULLISH"
        elif combined < -0.3:
            overall = "BEARISH"
        else:
            overall = "NEUTRAL"
        
        # Check alignment
        tech_direction = 1 if tech_score > 0 else (-1 if tech_score < 0 else 0)
        news_direction = 1 if news_score > 0 else (-1 if news_score < 0 else 0)
        
        if tech_direction == news_direction:
            alignment = "ALIGNED"
        elif tech_direction == 0 or news_direction == 0:
            alignment = "MIXED"
        else:
            alignment = "CONFLICTING"
        
        return {
            "overall_sentiment": overall,
            "confidence": abs(combined),
            "signal_alignment": alignment,
            "reasoning": "Simple weighted average (rule-based)",
            "recommendation": f"{overall} bias based on {alignment.lower()} signals",
            "llm_used": False,
        }


# Singleton
_smart_analyzer = None

def get_smart_analyzer() -> SmartAnalyzer:
    """Get smart analyzer singleton."""
    global _smart_analyzer
    if _smart_analyzer is None:
        _smart_analyzer = SmartAnalyzer()
    return _smart_analyzer
