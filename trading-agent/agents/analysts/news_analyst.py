"""
News Analyst Agent (LLM-Powered)
Analyzes news using actual AI reasoning, not keyword matching.

Uses local LLM (Ollama) for intelligent analysis.
Falls back to rule-based if LLM unavailable.

NO HALLUCINATION RULE:
- Only reports on actual fetched news
- If no news available, explicitly states "No news data"
- Does NOT invent or assume news stories
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from data_feeds.news_feed import get_news_feed, NewsItem
from data_feeds.sentiment_analyzer import get_sentiment_analyzer
from llm.client import get_llm_client, LLMBackend


class NewsAnalyst:
    """
    News Analyst Agent.
    
    Responsibilities:
    - Fetch relevant news for a stock/market
    - Analyze news sentiment
    - Identify key themes and events
    - Generate news-based signals
    
    STRICT RULES:
    - Only reports actual fetched news
    - Confidence is 0 if no news found
    - Never invents or assumes news content
    """
    
    def __init__(self):
        self.news_feed = get_news_feed()
        self.sentiment_analyzer = get_sentiment_analyzer()  # Fallback
        self.llm = get_llm_client()
        self._llm_available = self.llm.is_available()
        if self._llm_available:
            # Smart routing: News analysis uses GPT-5.5 (best for context)
            model_name = self.llm._get_model_for_task("news_analysis") if hasattr(self.llm, '_get_model_for_task') else self.llm.model
            print(f"   📰 News Analyst: LLM-powered ({model_name})")
        else:
            print("   📰 News Analyst: Rule-based (install Ollama for smart analysis)")
    
    def analyze(self, symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
        """
        Perform news analysis for a symbol using LLM reasoning.
        
        Args:
            symbol: Stock symbol (e.g., "RELIANCE")
            exchange: Exchange (NSE/BSE)
        
        Returns:
            News analysis report with intelligent insights
        """
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "exchange": exchange,
            "analyst": "News Analyst",
            "analysis_method": "LLM" if self._llm_available else "RULE_BASED",
            "news_data": {},
            "sentiment": {},
            "key_themes": [],
            "signals": [],
            "summary": "",
            "bias": "NEUTRAL",
            "confidence": 0.0,
            "data_quality": "NO_DATA",
        }
        
        # Fetch stock-specific news
        news_result = self.news_feed.fetch_stock_news(symbol, max_results=15)
        
        if news_result.get("errors"):
            report["errors"] = news_result["errors"]
        
        news_items = news_result.get("news", [])
        report["news_data"] = {
            "count": len(news_items),
            "sources": news_result.get("sources", []),
            "fetched_at": news_result.get("fetched_at"),
        }
        
        # Check if we have any news
        if not news_items:
            report["summary"] = f"No recent news found for {symbol}. Unable to provide news-based analysis."
            report["bias"] = "NEUTRAL"
            report["confidence"] = 0.0
            report["data_quality"] = "NO_DATA"
            return report
        
        # Use LLM for intelligent analysis if available
        if self._llm_available:
            llm_result = self.llm.analyze_news(news_items, symbol)
            report["sentiment"] = {
                "overall_sentiment": llm_result.get("sentiment", "NEUTRAL"),
                "overall_score": self._sentiment_to_score(llm_result.get("sentiment", "NEUTRAL")),
                "confidence": llm_result.get("confidence", 50) / 100,
                "llm_analysis": True,
            }
            report["key_themes"] = [{"theme": f, "mentions": 1} for f in llm_result.get("key_factors", [])]
            report["trading_implication"] = llm_result.get("trading_implication", "")
            report["risks"] = llm_result.get("risks", [])
            report["llm_reasoning"] = llm_result.get("raw_response", "")
            
            # Map LLM sentiment to bias
            sentiment = llm_result.get("sentiment", "NEUTRAL")
            if "BULLISH" in sentiment.upper():
                report["bias"] = "BULLISH"
            elif "BEARISH" in sentiment.upper():
                report["bias"] = "BEARISH"
            else:
                report["bias"] = "NEUTRAL"
            
            report["confidence"] = llm_result.get("confidence", 50) / 100
        else:
            # Fallback to rule-based analysis
            sentiment_result = self.sentiment_analyzer.analyze_news_batch(news_items)
            report["sentiment"] = sentiment_result
            report["key_themes"] = self._extract_themes(news_items)
            report["bias"], report["confidence"] = self._determine_bias(sentiment_result)
        
        # Generate signals from analysis
        report["signals"] = self._generate_signals(news_items, report["sentiment"])
        
        # Generate summary
        report["summary"] = self._generate_summary(
            symbol=symbol,
            news_count=len(news_items),
            sentiment=report["sentiment"],  # Use report's sentiment (works for both LLM and rule-based)
            themes=report["key_themes"],
        )
        
        # Data quality indicator
        if len(news_items) >= 10:
            report["data_quality"] = "GOOD"
        elif len(news_items) >= 5:
            report["data_quality"] = "MODERATE"
        else:
            report["data_quality"] = "LIMITED"
        
        return report
    
    def _extract_themes(self, news_items: List[Dict]) -> List[Dict[str, Any]]:
        """Extract key themes from news titles."""
        themes = []
        theme_keywords = {
            "earnings": ["earnings", "profit", "revenue", "quarterly", "results", "Q1", "Q2", "Q3", "Q4"],
            "deal": ["acquisition", "merger", "deal", "partnership", "agreement", "buy", "stake"],
            "regulation": ["SEBI", "RBI", "government", "regulation", "policy", "compliance", "ban"],
            "expansion": ["expand", "growth", "new", "launch", "enter", "market", "invest"],
            "management": ["CEO", "director", "appoint", "resign", "board", "leadership"],
            "dividend": ["dividend", "payout", "bonus", "split"],
            "rating": ["upgrade", "downgrade", "rating", "target", "analyst"],
            "global": ["global", "US", "China", "Fed", "dollar", "crude", "oil"],
        }
        
        theme_counts = {theme: 0 for theme in theme_keywords}
        theme_examples = {theme: [] for theme in theme_keywords}
        
        for item in news_items:
            title = item.get("title", "").lower()
            for theme, keywords in theme_keywords.items():
                if any(kw.lower() in title for kw in keywords):
                    theme_counts[theme] += 1
                    if len(theme_examples[theme]) < 2:
                        theme_examples[theme].append(item.get("title", ""))
        
        # Return themes with at least one mention
        for theme, count in sorted(theme_counts.items(), key=lambda x: x[1], reverse=True):
            if count > 0:
                themes.append({
                    "theme": theme.upper(),
                    "mentions": count,
                    "examples": theme_examples[theme],
                })
        
        return themes[:5]  # Top 5 themes
    
    def _generate_signals(
        self,
        news_items: List[Dict],
        sentiment: Dict,
    ) -> List[Dict[str, Any]]:
        """Generate trading signals from news analysis."""
        signals = []
        
        overall_sentiment = sentiment.get("overall_sentiment", "NEUTRAL")
        score = sentiment.get("overall_score", 0)
        confidence = sentiment.get("confidence", 0)
        
        # Sentiment-based signal
        if confidence > 0.3:  # Only if we have reasonable confidence
            if score > 0.3:
                signals.append({
                    "type": "SENTIMENT",
                    "signal": "BULLISH",
                    "strength": "STRONG" if score > 0.5 else "MODERATE",
                    "reason": f"Positive news sentiment (score: {score:.2f})",
                })
            elif score < -0.3:
                signals.append({
                    "type": "SENTIMENT",
                    "signal": "BEARISH",
                    "strength": "STRONG" if score < -0.5 else "MODERATE",
                    "reason": f"Negative news sentiment (score: {score:.2f})",
                })
            else:
                signals.append({
                    "type": "SENTIMENT",
                    "signal": "NEUTRAL",
                    "strength": "WEAK",
                    "reason": f"Mixed/neutral news sentiment (score: {score:.2f})",
                })
        
        # Volume of news signal
        news_count = len(news_items)
        if news_count >= 10:
            signals.append({
                "type": "NEWS_VOLUME",
                "signal": "HIGH_ATTENTION",
                "strength": "MODERATE",
                "reason": f"High news coverage ({news_count} articles)",
            })
        
        # Check for specific events in positive/negative signals
        top_positive = sentiment.get("top_positive_signals", [])
        top_negative = sentiment.get("top_negative_signals", [])
        
        if "upgrade" in top_positive:
            signals.append({
                "type": "ANALYST_ACTION",
                "signal": "BULLISH",
                "strength": "STRONG",
                "reason": "Analyst upgrade mentioned in news",
            })
        
        if "downgrade" in top_negative:
            signals.append({
                "type": "ANALYST_ACTION",
                "signal": "BEARISH",
                "strength": "STRONG",
                "reason": "Analyst downgrade mentioned in news",
            })
        
        return signals
    
    def _determine_bias(self, sentiment: Dict) -> tuple:
        """Determine overall bias and confidence from sentiment analysis."""
        score = sentiment.get("overall_score", 0)
        confidence = sentiment.get("confidence", 0)
        news_analyzed = sentiment.get("news_analyzed", 0)
        
        # Adjust confidence based on data quality
        if news_analyzed < 3:
            confidence = min(confidence, 0.3)
        
        if score > 0.2 and confidence > 0.3:
            return "BULLISH", confidence
        elif score < -0.2 and confidence > 0.3:
            return "BEARISH", confidence
        else:
            return "NEUTRAL", confidence * 0.5  # Lower confidence for neutral
    
    def _sentiment_to_score(self, sentiment: str) -> float:
        """Convert LLM sentiment label to numeric score."""
        sentiment = sentiment.upper()
        if "VERY_BULLISH" in sentiment or "STRONGLY_BULLISH" in sentiment:
            return 0.9
        elif "BULLISH" in sentiment:
            return 0.6
        elif "SLIGHTLY_BULLISH" in sentiment or "MILDLY_BULLISH" in sentiment:
            return 0.3
        elif "VERY_BEARISH" in sentiment or "STRONGLY_BEARISH" in sentiment:
            return -0.9
        elif "BEARISH" in sentiment:
            return -0.6
        elif "SLIGHTLY_BEARISH" in sentiment or "MILDLY_BEARISH" in sentiment:
            return -0.3
        else:
            return 0.0
    
    def _generate_summary(
        self,
        symbol: str,
        news_count: int,
        sentiment: Dict,
        themes: List[Dict],
    ) -> str:
        """Generate human-readable summary."""
        parts = [f"News Analysis for {symbol}:"]
        
        # News volume
        parts.append(f"Found {news_count} relevant news articles.")
        
        # Sentiment
        overall = sentiment.get("overall_sentiment", "NEUTRAL")
        score = sentiment.get("overall_score", 0)
        parts.append(f"Overall sentiment: {overall} (score: {score:.2f})")
        
        # Key themes
        if themes:
            theme_str = ", ".join([t["theme"] for t in themes[:3]])
            parts.append(f"Key themes: {theme_str}")
        
        # Positive/Negative drivers
        top_pos = sentiment.get("top_positive_signals", [])
        top_neg = sentiment.get("top_negative_signals", [])
        
        if top_pos:
            parts.append(f"Positive drivers: {', '.join(top_pos[:3])}")
        if top_neg:
            parts.append(f"Negative drivers: {', '.join(top_neg[:3])}")
        
        return " | ".join(parts)
    
    def get_market_news_analysis(self) -> Dict[str, Any]:
        """Analyze overall Indian market news."""
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "type": "MARKET_OVERVIEW",
            "analyst": "News Analyst",
        }
        
        market_news = self.news_feed.fetch_market_news(max_results=20)
        news_items = market_news.get("news", [])
        
        if not news_items:
            report["summary"] = "No market news available."
            report["bias"] = "NEUTRAL"
            report["confidence"] = 0.0
            return report
        
        sentiment = self.sentiment_analyzer.analyze_news_batch(news_items)
        report["sentiment"] = sentiment
        report["news_count"] = len(news_items)
        report["key_themes"] = self._extract_themes(news_items)
        report["bias"], report["confidence"] = self._determine_bias(sentiment)
        report["summary"] = self._generate_summary(
            symbol="MARKET",
            news_count=len(news_items),
            sentiment=sentiment,
            themes=report["key_themes"],
        )
        
        return report


# Convenience function
def analyze_stock_news(symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
    """Quick news analysis for a stock."""
    analyst = NewsAnalyst()
    return analyst.analyze(symbol, exchange)
