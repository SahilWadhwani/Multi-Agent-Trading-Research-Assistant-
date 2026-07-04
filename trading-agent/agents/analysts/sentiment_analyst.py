"""
Sentiment Analyst Agent (LLM-Powered)
Analyzes overall market sentiment using AI reasoning.

Combines:
- News sentiment
- Technical momentum (from Technical Analyst)
- Market breadth indicators
- FII/DII flow sentiment (when available)

Uses local LLM (Ollama) for intelligent aggregation and reasoning.
Falls back to weighted averaging if LLM unavailable.

NO HALLUCINATION RULE:
- Only uses actual data provided
- Clearly states what data was/wasn't available
- Never invents sentiment indicators
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from data_feeds.sentiment_analyzer import (
    get_sentiment_analyzer,
    SentimentLevel,
)
from data_feeds.news_feed import get_news_feed
from llm.client import get_llm_client


class SentimentAnalyst:
    """
    Sentiment Analyst Agent.
    
    Aggregates sentiment from multiple sources:
    1. News sentiment (from News Analyst)
    2. Technical momentum (from indicators)
    3. Social sentiment hints (from news titles)
    
    STRICT RULES:
    - Only reports on data actually received
    - Clearly indicates missing data sources
    - Does NOT assume or fabricate sentiment
    """
    
    def __init__(self):
        self.sentiment_analyzer = get_sentiment_analyzer()
        self.news_feed = get_news_feed()
        self.llm = get_llm_client()
        self._llm_available = self.llm.is_available()
        if self._llm_available:
            # Smart routing: Sentiment aggregation uses GPT-5.5 (best reasoning)
            model_name = self.llm._get_model_for_task("sentiment_aggregation") if hasattr(self.llm, '_get_model_for_task') else self.llm.model
            print(f"   💭 Sentiment Analyst: LLM-powered ({model_name})")
        else:
            print("   💭 Sentiment Analyst: Rule-based (install Ollama for smart aggregation)")
    
    def analyze(
        self,
        symbol: str,
        technical_report: Optional[Dict[str, Any]] = None,
        news_report: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Comprehensive sentiment analysis combining multiple sources.
        Uses LLM for intelligent aggregation when available.
        
        Args:
            symbol: Stock symbol
            technical_report: Optional report from Technical Analyst
            news_report: Optional report from News Analyst
        
        Returns:
            Aggregated sentiment analysis with AI reasoning
        """
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "analyst": "Sentiment Analyst",
            "analysis_method": "LLM" if self._llm_available else "RULE_BASED",
            "data_sources": [],
            "missing_sources": [],
            "component_sentiments": {},
            "aggregated_sentiment": {},
            "signals": [],
            "summary": "",
            "bias": "NEUTRAL",
            "confidence": 0.0,
        }
        
        sentiment_scores = []
        weights = []
        
        # 1. Technical Momentum Sentiment
        if technical_report and "error" not in technical_report:
            tech_sentiment = self._extract_technical_sentiment(technical_report)
            report["component_sentiments"]["technical"] = tech_sentiment
            report["data_sources"].append("TECHNICAL")
            
            if tech_sentiment["confidence"] > 0:
                sentiment_scores.append(tech_sentiment["score"])
                weights.append(tech_sentiment["confidence"] * 0.4)  # 40% weight
        else:
            report["missing_sources"].append("TECHNICAL")
        
        # 2. News Sentiment
        if news_report and "error" not in news_report:
            news_sentiment = self._extract_news_sentiment(news_report)
            report["component_sentiments"]["news"] = news_sentiment
            report["data_sources"].append("NEWS")
            
            if news_sentiment["confidence"] > 0:
                sentiment_scores.append(news_sentiment["score"])
                weights.append(news_sentiment["confidence"] * 0.35)  # 35% weight
        else:
            report["missing_sources"].append("NEWS")
            # Try to fetch news ourselves if not provided
            fresh_news = self._fetch_fresh_news_sentiment(symbol)
            if fresh_news["confidence"] > 0:
                report["component_sentiments"]["news"] = fresh_news
                report["data_sources"].append("NEWS")
                report["missing_sources"].remove("NEWS")
                sentiment_scores.append(fresh_news["score"])
                weights.append(fresh_news["confidence"] * 0.35)
        
        # 3. Social/Title Sentiment (quick sentiment from news titles)
        title_sentiment = self._analyze_title_sentiment(symbol)
        if title_sentiment["confidence"] > 0:
            report["component_sentiments"]["social_proxy"] = title_sentiment
            report["data_sources"].append("SOCIAL_PROXY")
            sentiment_scores.append(title_sentiment["score"])
            weights.append(title_sentiment["confidence"] * 0.25)  # 25% weight
        
        # Calculate aggregated sentiment
        if sentiment_scores and sum(weights) > 0:
            weighted_score = sum(s * w for s, w in zip(sentiment_scores, weights)) / sum(weights)
            avg_confidence = sum(weights) / len(weights)
            
            report["aggregated_sentiment"] = {
                "score": round(weighted_score, 3),
                "confidence": round(avg_confidence, 3),
                "sources_used": len(sentiment_scores),
            }
            
            # Use LLM for intelligent aggregation if available
            if self._llm_available and len(report["component_sentiments"]) > 1:
                llm_result = self._llm_aggregate(symbol, report["component_sentiments"], technical_report, news_report)
                if llm_result:
                    report["llm_analysis"] = llm_result
                    report["bias"] = llm_result.get("final_sentiment", self._score_to_bias(weighted_score))
                    report["confidence"] = llm_result.get("confidence", avg_confidence * 100) / 100
                    report["llm_reasoning"] = llm_result.get("reasoning", "")
                else:
                    report["bias"] = self._score_to_bias(weighted_score)
                    report["confidence"] = avg_confidence
            else:
                report["bias"] = self._score_to_bias(weighted_score)
                report["confidence"] = avg_confidence
        else:
            report["aggregated_sentiment"] = {
                "score": 0.0,
                "confidence": 0.0,
                "sources_used": 0,
            }
            report["bias"] = "NEUTRAL"
            report["confidence"] = 0.0
        
        # Generate signals
        report["signals"] = self._generate_signals(report)
        
        # Generate summary
        report["summary"] = self._generate_summary(report)
        
        return report
    
    def _llm_aggregate(
        self,
        symbol: str,
        component_sentiments: Dict,
        technical_report: Optional[Dict],
        news_report: Optional[Dict],
    ) -> Optional[Dict]:
        """Use LLM to intelligently aggregate sentiment signals."""
        try:
            # Build technical sentiment dict
            tech_sentiment = {}
            if "technical" in component_sentiments:
                tech = component_sentiments["technical"]
                tech_sentiment = {
                    "bias": "BULLISH" if tech["score"] > 0.2 else "BEARISH" if tech["score"] < -0.2 else "NEUTRAL",
                    "confidence": tech["confidence"] * 100,  # Convert to percentage
                    "indicators": tech.get("details", {}),
                }
            elif technical_report:
                tech_sentiment = {
                    "bias": technical_report.get("bias", "NEUTRAL"),
                    "confidence": technical_report.get("confidence", 0) * 100,
                    "indicators": technical_report.get("indicators", {}),
                }
            
            # Build news sentiment dict
            news_sentiment = {}
            if "news" in component_sentiments:
                news = component_sentiments["news"]
                news_sentiment = {
                    "sentiment": "BULLISH" if news["score"] > 0.2 else "BEARISH" if news["score"] < -0.2 else "NEUTRAL",
                    "confidence": news["confidence"] * 100,
                    "key_factors": news.get("details", {}).get("key_factors", []),
                }
            elif news_report:
                news_sentiment = {
                    "sentiment": news_report.get("bias", "NEUTRAL"),
                    "confidence": news_report.get("confidence", 0) * 100,
                    "key_factors": [t.get("theme", "") for t in news_report.get("key_themes", [])],
                }
            
            # Call LLM for aggregation
            result = self.llm.aggregate_sentiment(tech_sentiment, news_sentiment, symbol)
            
            # Map result to our format
            return {
                "final_sentiment": result.get("overall_sentiment", "NEUTRAL"),
                "confidence": result.get("confidence", 50),
                "reasoning": result.get("reasoning", ""),
                "signal_alignment": result.get("signal_alignment", "MIXED"),
            }
            
        except Exception as e:
            print(f"   ⚠️ LLM aggregation failed: {e}")
            return None
    
    def _extract_technical_sentiment(self, tech_report: Dict) -> Dict[str, Any]:
        """Extract sentiment from technical analysis report."""
        bias = tech_report.get("bias", "NEUTRAL")
        confidence = tech_report.get("confidence", 0)
        
        # Convert bias to score
        bias_to_score = {
            "BULLISH": 0.6,
            "BEARISH": -0.6,
            "NEUTRAL": 0.0,
        }
        score = bias_to_score.get(bias, 0.0)
        
        # Adjust based on RSI if available
        indicators = tech_report.get("indicators", {})
        rsi = indicators.get("rsi")
        
        if rsi is not None:
            if rsi > 70:
                score -= 0.2  # Overbought
            elif rsi < 30:
                score += 0.2  # Oversold
        
        return {
            "source": "TECHNICAL",
            "score": max(-1, min(1, score)),  # Clamp to [-1, 1]
            "confidence": confidence,
            "details": {
                "bias": bias,
                "rsi": rsi,
            },
        }
    
    def _extract_news_sentiment(self, news_report: Dict) -> Dict[str, Any]:
        """Extract sentiment from news analysis report."""
        sentiment = news_report.get("sentiment", {})
        
        return {
            "source": "NEWS",
            "score": sentiment.get("overall_score", 0),
            "confidence": sentiment.get("confidence", 0),
            "details": {
                "news_count": sentiment.get("news_analyzed", 0),
                "overall": sentiment.get("overall_sentiment", "NEUTRAL"),
            },
        }
    
    def _fetch_fresh_news_sentiment(self, symbol: str) -> Dict[str, Any]:
        """Fetch and analyze fresh news if news report not provided."""
        try:
            news_result = self.news_feed.fetch_stock_news(symbol, max_results=10)
            news_items = news_result.get("news", [])
            
            if not news_items:
                return {"score": 0, "confidence": 0, "source": "NEWS"}
            
            sentiment = self.sentiment_analyzer.analyze_news_batch(news_items)
            
            return {
                "source": "NEWS",
                "score": sentiment.get("overall_score", 0),
                "confidence": sentiment.get("confidence", 0),
                "details": {
                    "news_count": len(news_items),
                    "overall": sentiment.get("overall_sentiment", "NEUTRAL"),
                },
            }
        except Exception:
            return {"score": 0, "confidence": 0, "source": "NEWS"}
    
    def _analyze_title_sentiment(self, symbol: str) -> Dict[str, Any]:
        """
        Quick sentiment analysis from news titles.
        Acts as a proxy for social sentiment.
        """
        try:
            news_result = self.news_feed.fetch_google_news(f"{symbol} stock India", max_results=10)
            
            if not news_result:
                return {"score": 0, "confidence": 0, "source": "SOCIAL_PROXY"}
            
            # Analyze just titles for quick sentiment
            positive = 0
            negative = 0
            
            for item in news_result:
                hint = item.sentiment_hint
                if hint == "POSITIVE":
                    positive += 1
                elif hint == "NEGATIVE":
                    negative += 1
            
            total = positive + negative
            if total == 0:
                return {"score": 0, "confidence": 0.1, "source": "SOCIAL_PROXY"}
            
            score = (positive - negative) / total
            confidence = min(0.5, total / 20)  # Max 50% confidence from titles alone
            
            return {
                "source": "SOCIAL_PROXY",
                "score": round(score, 3),
                "confidence": round(confidence, 3),
                "details": {
                    "positive_titles": positive,
                    "negative_titles": negative,
                    "total_analyzed": len(news_result),
                },
            }
        except Exception:
            return {"score": 0, "confidence": 0, "source": "SOCIAL_PROXY"}
    
    def _score_to_bias(self, score: float) -> str:
        """Convert score to bias label."""
        if score > 0.2:
            return "BULLISH"
        elif score < -0.2:
            return "BEARISH"
        return "NEUTRAL"
    
    def _generate_signals(self, report: Dict) -> List[Dict[str, Any]]:
        """Generate trading signals from sentiment analysis."""
        signals = []
        
        agg = report.get("aggregated_sentiment", {})
        score = agg.get("score", 0)
        confidence = agg.get("confidence", 0)
        
        if confidence < 0.2:
            signals.append({
                "type": "DATA_QUALITY",
                "signal": "LOW_CONFIDENCE",
                "reason": "Insufficient data for reliable sentiment analysis",
            })
            return signals
        
        # Overall sentiment signal
        if score > 0.3:
            signals.append({
                "type": "SENTIMENT",
                "signal": "BULLISH",
                "strength": "STRONG" if score > 0.5 else "MODERATE",
                "reason": f"Positive sentiment across {agg.get('sources_used', 0)} sources",
            })
        elif score < -0.3:
            signals.append({
                "type": "SENTIMENT",
                "signal": "BEARISH",
                "strength": "STRONG" if score < -0.5 else "MODERATE",
                "reason": f"Negative sentiment across {agg.get('sources_used', 0)} sources",
            })
        else:
            signals.append({
                "type": "SENTIMENT",
                "signal": "NEUTRAL",
                "strength": "WEAK",
                "reason": "Mixed or neutral sentiment",
            })
        
        # Check for sentiment divergence between sources
        components = report.get("component_sentiments", {})
        if len(components) >= 2:
            scores = [c.get("score", 0) for c in components.values()]
            if max(scores) - min(scores) > 0.5:
                signals.append({
                    "type": "DIVERGENCE",
                    "signal": "CAUTION",
                    "reason": "Sentiment divergence between data sources",
                })
        
        return signals
    
    def _generate_summary(self, report: Dict) -> str:
        """Generate human-readable summary."""
        parts = [f"Sentiment Analysis for {report['symbol']}:"]
        
        # Data sources
        sources = report.get("data_sources", [])
        missing = report.get("missing_sources", [])
        
        parts.append(f"Data sources: {', '.join(sources) if sources else 'None'}")
        if missing:
            parts.append(f"Missing: {', '.join(missing)}")
        
        # Component sentiments
        components = report.get("component_sentiments", {})
        for name, comp in components.items():
            parts.append(f"{name.upper()}: {comp.get('score', 0):.2f}")
        
        # Aggregated
        agg = report.get("aggregated_sentiment", {})
        parts.append(f"AGGREGATED: {agg.get('score', 0):.2f} (confidence: {agg.get('confidence', 0):.0%})")
        
        # Conclusion
        parts.append(f"BIAS: {report.get('bias', 'NEUTRAL')}")
        
        return " | ".join(parts)


# Convenience function
def analyze_sentiment(
    symbol: str,
    technical_report: Optional[Dict] = None,
    news_report: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Quick sentiment analysis."""
    analyst = SentimentAnalyst()
    return analyst.analyze(symbol, technical_report, news_report)
