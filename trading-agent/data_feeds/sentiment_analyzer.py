"""
Sentiment Analyzer for Indian Markets

Local sentiment analysis - NO external AI API required.
Uses rule-based analysis with financial domain keywords.

NO HALLUCINATION RULE: 
- Only analyzes actual provided text
- Returns "INSUFFICIENT_DATA" if text is empty/too short
- Confidence is based on keyword matches, not assumptions
"""

import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class SentimentLevel(Enum):
    VERY_BULLISH = "VERY_BULLISH"
    BULLISH = "BULLISH"
    SLIGHTLY_BULLISH = "SLIGHTLY_BULLISH"
    NEUTRAL = "NEUTRAL"
    SLIGHTLY_BEARISH = "SLIGHTLY_BEARISH"
    BEARISH = "BEARISH"
    VERY_BEARISH = "VERY_BEARISH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass
class SentimentResult:
    """Structured sentiment analysis result."""
    sentiment: SentimentLevel
    score: float  # -1.0 (very bearish) to +1.0 (very bullish)
    confidence: float  # 0.0 to 1.0
    positive_signals: List[str]
    negative_signals: List[str]
    keyword_matches: int
    text_analyzed: int  # Characters analyzed
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "sentiment": self.sentiment.value,
            "score": round(self.score, 3),
            "confidence": round(self.confidence, 3),
            "positive_signals": self.positive_signals,
            "negative_signals": self.negative_signals,
            "keyword_matches": self.keyword_matches,
            "text_analyzed": self.text_analyzed,
        }


class FinancialSentimentAnalyzer:
    """
    Rule-based sentiment analyzer for financial text.
    
    IMPORTANT:
    - Only analyzes actual text provided
    - Does NOT make assumptions or guesses
    - Returns INSUFFICIENT_DATA for empty/minimal input
    """
    
    # Weighted positive keywords (keyword: weight)
    POSITIVE_KEYWORDS = {
        # Strong positive (weight 2)
        "surge": 2, "soar": 2, "skyrocket": 2, "boom": 2, "record high": 2,
        "breakthrough": 2, "outperform": 2, "beat expectations": 2,
        "strong buy": 2, "upgrade": 2, "bullish": 2,
        
        # Moderate positive (weight 1)
        "rise": 1, "gain": 1, "growth": 1, "profit": 1, "rally": 1,
        "advance": 1, "climb": 1, "jump": 1, "positive": 1, "upbeat": 1,
        "optimistic": 1, "recover": 1, "rebound": 1, "strength": 1,
        "opportunity": 1, "momentum": 1, "buy": 1, "accumulate": 1,
        "exceed": 1, "beat": 1, "higher": 1, "improve": 1,
        
        # Mild positive (weight 0.5)
        "stable": 0.5, "steady": 0.5, "support": 0.5, "maintain": 0.5,
        "hold": 0.5, "neutral to positive": 0.5,
    }
    
    # Weighted negative keywords (keyword: weight)
    NEGATIVE_KEYWORDS = {
        # Strong negative (weight 2)
        "crash": 2, "plunge": 2, "collapse": 2, "crisis": 2, "panic": 2,
        "catastrophic": 2, "severe": 2, "sell off": 2, "strong sell": 2,
        "downgrade": 2, "bearish": 2, "default": 2, "bankruptcy": 2,
        
        # Moderate negative (weight 1)
        "fall": 1, "drop": 1, "decline": 1, "loss": 1, "sink": 1,
        "tumble": 1, "slide": 1, "negative": 1, "concern": 1, "worry": 1,
        "fear": 1, "risk": 1, "warning": 1, "weak": 1, "underperform": 1,
        "miss": 1, "lower": 1, "cut": 1, "reduce": 1, "sell": 1,
        "pressure": 1, "volatility": 1, "uncertain": 1,
        
        # Mild negative (weight 0.5)
        "caution": 0.5, "careful": 0.5, "monitor": 0.5, "watch": 0.5,
        "neutral to negative": 0.5, "flat": 0.5,
    }
    
    # Indian market specific keywords
    INDIA_POSITIVE = {
        "FII buying": 2, "DII buying": 1.5, "RBI support": 1.5,
        "rupee strength": 1, "export growth": 1, "GDP growth": 1,
        "monsoon normal": 1, "reform": 1, "FDI inflow": 1.5,
    }
    
    INDIA_NEGATIVE = {
        "FII selling": 2, "DII selling": 1.5, "RBI concern": 1.5,
        "rupee weakness": 1, "import pressure": 1, "inflation high": 1.5,
        "monsoon deficit": 1, "policy uncertainty": 1, "FDI outflow": 1.5,
        "CAD widening": 1, "fiscal deficit": 1,
    }
    
    # Negation words that flip sentiment
    NEGATION_WORDS = {
        "not", "no", "never", "neither", "nobody", "nothing",
        "nowhere", "hardly", "barely", "doesn't", "isn't", "wasn't",
        "weren't", "haven't", "hasn't", "hadn't", "won't", "wouldn't",
        "couldn't", "shouldn't", "can't", "don't", "didn't",
    }
    
    def __init__(self):
        # Combine all keywords
        self.all_positive = {**self.POSITIVE_KEYWORDS, **self.INDIA_POSITIVE}
        self.all_negative = {**self.NEGATIVE_KEYWORDS, **self.INDIA_NEGATIVE}
    
    def analyze_text(self, text: str) -> SentimentResult:
        """
        Analyze sentiment of a single text.
        
        Args:
            text: Text to analyze
        
        Returns:
            SentimentResult with sentiment analysis
        """
        # Check for insufficient data
        if not text or len(text.strip()) < 10:
            return SentimentResult(
                sentiment=SentimentLevel.INSUFFICIENT_DATA,
                score=0.0,
                confidence=0.0,
                positive_signals=[],
                negative_signals=[],
                keyword_matches=0,
                text_analyzed=len(text) if text else 0,
            )
        
        text_lower = text.lower()
        words = text_lower.split()
        
        positive_score = 0.0
        negative_score = 0.0
        positive_signals = []
        negative_signals = []
        
        # Check for negation context (simple window-based)
        def is_negated(text: str, keyword: str, window: int = 3) -> bool:
            """Check if keyword is preceded by negation word."""
            idx = text.find(keyword)
            if idx == -1:
                return False
            # Check words before keyword
            before_text = text[:idx].split()[-window:]
            return any(neg in before_text for neg in self.NEGATION_WORDS)
        
        # Score positive keywords
        for keyword, weight in self.all_positive.items():
            if keyword in text_lower:
                if is_negated(text_lower, keyword):
                    # Negated positive = negative
                    negative_score += weight * 0.5
                    negative_signals.append(f"NOT {keyword}")
                else:
                    positive_score += weight
                    positive_signals.append(keyword)
        
        # Score negative keywords
        for keyword, weight in self.all_negative.items():
            if keyword in text_lower:
                if is_negated(text_lower, keyword):
                    # Negated negative = positive
                    positive_score += weight * 0.5
                    positive_signals.append(f"NOT {keyword}")
                else:
                    negative_score += weight
                    negative_signals.append(keyword)
        
        # Calculate final score
        total_matches = len(positive_signals) + len(negative_signals)
        
        if total_matches == 0:
            return SentimentResult(
                sentiment=SentimentLevel.NEUTRAL,
                score=0.0,
                confidence=0.2,  # Low confidence when no keywords matched
                positive_signals=[],
                negative_signals=[],
                keyword_matches=0,
                text_analyzed=len(text),
            )
        
        # Normalize score to -1 to +1
        total_score = positive_score - negative_score
        max_possible = positive_score + negative_score
        normalized_score = total_score / max_possible if max_possible > 0 else 0
        
        # Determine sentiment level
        sentiment = self._score_to_sentiment(normalized_score)
        
        # Calculate confidence based on keyword matches and text length
        text_factor = min(1.0, len(text) / 500)  # More text = higher confidence
        match_factor = min(1.0, total_matches / 5)  # More matches = higher confidence
        confidence = (text_factor * 0.3 + match_factor * 0.7)
        
        return SentimentResult(
            sentiment=sentiment,
            score=normalized_score,
            confidence=confidence,
            positive_signals=positive_signals[:5],  # Top 5
            negative_signals=negative_signals[:5],
            keyword_matches=total_matches,
            text_analyzed=len(text),
        )
    
    def _score_to_sentiment(self, score: float) -> SentimentLevel:
        """Convert numeric score to sentiment level."""
        if score >= 0.6:
            return SentimentLevel.VERY_BULLISH
        elif score >= 0.3:
            return SentimentLevel.BULLISH
        elif score >= 0.1:
            return SentimentLevel.SLIGHTLY_BULLISH
        elif score > -0.1:
            return SentimentLevel.NEUTRAL
        elif score > -0.3:
            return SentimentLevel.SLIGHTLY_BEARISH
        elif score > -0.6:
            return SentimentLevel.BEARISH
        else:
            return SentimentLevel.VERY_BEARISH
    
    def analyze_news_batch(
        self,
        news_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Analyze sentiment of a batch of news items.
        
        Args:
            news_items: List of news items with 'title' and/or 'description'
        
        Returns:
            Aggregated sentiment analysis
        """
        if not news_items:
            return {
                "overall_sentiment": SentimentLevel.INSUFFICIENT_DATA.value,
                "overall_score": 0.0,
                "confidence": 0.0,
                "news_analyzed": 0,
                "sentiment_distribution": {},
                "top_positive_signals": [],
                "top_negative_signals": [],
            }
        
        individual_results = []
        all_positive = []
        all_negative = []
        
        for item in news_items:
            # Combine title and description
            text = f"{item.get('title', '')} {item.get('description', '')}"
            result = self.analyze_text(text)
            
            if result.sentiment != SentimentLevel.INSUFFICIENT_DATA:
                individual_results.append(result)
                all_positive.extend(result.positive_signals)
                all_negative.extend(result.negative_signals)
        
        if not individual_results:
            return {
                "overall_sentiment": SentimentLevel.INSUFFICIENT_DATA.value,
                "overall_score": 0.0,
                "confidence": 0.0,
                "news_analyzed": len(news_items),
                "sentiment_distribution": {},
                "top_positive_signals": [],
                "top_negative_signals": [],
            }
        
        # Calculate weighted average score
        total_score = sum(r.score * r.confidence for r in individual_results)
        total_weight = sum(r.confidence for r in individual_results)
        avg_score = total_score / total_weight if total_weight > 0 else 0
        
        # Sentiment distribution
        distribution = {}
        for result in individual_results:
            level = result.sentiment.value
            distribution[level] = distribution.get(level, 0) + 1
        
        # Top signals by frequency
        from collections import Counter
        top_positive = [kw for kw, _ in Counter(all_positive).most_common(5)]
        top_negative = [kw for kw, _ in Counter(all_negative).most_common(5)]
        
        # Overall confidence
        avg_confidence = sum(r.confidence for r in individual_results) / len(individual_results)
        
        return {
            "overall_sentiment": self._score_to_sentiment(avg_score).value,
            "overall_score": round(avg_score, 3),
            "confidence": round(avg_confidence, 3),
            "news_analyzed": len(individual_results),
            "sentiment_distribution": distribution,
            "top_positive_signals": top_positive,
            "top_negative_signals": top_negative,
        }


# Singleton
_analyzer_instance = None

def get_sentiment_analyzer() -> FinancialSentimentAnalyzer:
    """Get sentiment analyzer singleton."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = FinancialSentimentAnalyzer()
    return _analyzer_instance


def analyze_sentiment(text: str) -> Dict[str, Any]:
    """Quick sentiment analysis function."""
    analyzer = get_sentiment_analyzer()
    return analyzer.analyze_text(text).to_dict()
