#!/usr/bin/env python3
"""
Phase 2 Test Suite for QUANT-1 Trading Agent

Tests News Feed, Sentiment Analyzer, and New Analyst Agents.
Uses mock data where possible, real news sources for integration tests.

Run with: python -m tests.test_phase2
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Test results tracking
test_results = {
    "passed": 0,
    "failed": 0,
    "errors": [],
}


def log_test(name: str, passed: bool, message: str = ""):
    """Log test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if not passed and message:
        print(f"         {message}")
    
    if passed:
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
        test_results["errors"].append(f"{name}: {message}")


def test_sentiment_analyzer():
    """Test the local sentiment analyzer."""
    print("\n" + "=" * 60)
    print("TEST: Sentiment Analyzer (Local)")
    print("=" * 60)
    
    try:
        from data_feeds.sentiment_analyzer import (
            FinancialSentimentAnalyzer,
            get_sentiment_analyzer,
            SentimentLevel,
        )
        log_test("Import SentimentAnalyzer", True)
    except Exception as e:
        log_test("Import SentimentAnalyzer", False, str(e))
        return
    
    analyzer = get_sentiment_analyzer()
    
    # Test positive sentiment
    try:
        result = analyzer.analyze_text(
            "Stock surges 10% after strong quarterly earnings beat expectations. "
            "Analysts upgrade rating to buy with bullish outlook."
        )
        is_positive = result.score > 0.3
        log_test("Positive Text Detection", is_positive, 
                 f"Score: {result.score:.2f}, Expected > 0.3")
    except Exception as e:
        log_test("Positive Text Detection", False, str(e))
    
    # Test negative sentiment
    try:
        result = analyzer.analyze_text(
            "Stock crashes 15% amid fears of bankruptcy. "
            "Analysts downgrade to sell, citing severe concerns about losses."
        )
        is_negative = result.score < -0.3
        log_test("Negative Text Detection", is_negative,
                 f"Score: {result.score:.2f}, Expected < -0.3")
    except Exception as e:
        log_test("Negative Text Detection", False, str(e))
    
    # Test neutral sentiment
    try:
        result = analyzer.analyze_text(
            "Company announces routine quarterly meeting scheduled for next week."
        )
        is_neutral = -0.3 <= result.score <= 0.3
        log_test("Neutral Text Detection", is_neutral,
                 f"Score: {result.score:.2f}, Expected between -0.3 and 0.3")
    except Exception as e:
        log_test("Neutral Text Detection", False, str(e))
    
    # Test insufficient data handling
    try:
        result = analyzer.analyze_text("")
        is_insufficient = result.sentiment == SentimentLevel.INSUFFICIENT_DATA
        log_test("Empty Text = INSUFFICIENT_DATA", is_insufficient)
    except Exception as e:
        log_test("Empty Text = INSUFFICIENT_DATA", False, str(e))
    
    # Test batch analysis
    try:
        news_items = [
            {"title": "Stock rallies on strong growth", "description": "Positive outlook"},
            {"title": "Company faces challenges", "description": "Concerns about future"},
            {"title": "Neutral market update", "description": "Regular trading day"},
        ]
        result = analyzer.analyze_news_batch(news_items)
        has_keys = all(k in result for k in ["overall_sentiment", "overall_score", "confidence"])
        log_test("Batch Analysis", has_keys)
    except Exception as e:
        log_test("Batch Analysis", False, str(e))
    
    # Test Indian market keywords
    try:
        result = analyzer.analyze_text("FII buying continues, supporting rupee strength")
        has_positive = result.score > 0
        log_test("Indian Keywords (FII buying)", has_positive,
                 f"Score: {result.score:.2f}")
    except Exception as e:
        log_test("Indian Keywords (FII buying)", False, str(e))


def test_news_feed():
    """Test the news feed (requires internet)."""
    print("\n" + "=" * 60)
    print("TEST: News Feed (Internet Required)")
    print("=" * 60)
    
    try:
        from data_feeds.news_feed import IndianNewsFeed, get_news_feed
        log_test("Import NewsFeed", True)
    except Exception as e:
        log_test("Import NewsFeed", False, str(e))
        return
    
    feed = get_news_feed()
    
    # Test Google News fetch (free, no API key)
    try:
        news = feed.fetch_google_news("Indian stock market", max_results=5)
        has_news = len(news) > 0
        log_test("Google News Fetch", has_news, 
                 f"Found {len(news)} articles" if has_news else "No news returned")
    except Exception as e:
        log_test("Google News Fetch", False, str(e))
    
    # Test stock news fetch
    try:
        result = feed.fetch_stock_news("RELIANCE", max_results=5)
        has_result = "news" in result and "symbol" in result
        news_count = len(result.get("news", []))
        log_test("Stock News Fetch (RELIANCE)", has_result,
                 f"Found {news_count} articles")
    except Exception as e:
        log_test("Stock News Fetch (RELIANCE)", False, str(e))
    
    # Test market news fetch
    try:
        result = feed.fetch_market_news(max_results=5)
        has_result = "news" in result and "type" in result
        log_test("Market News Fetch", has_result)
    except Exception as e:
        log_test("Market News Fetch", False, str(e))
    
    # Test sentiment hints in news items
    try:
        news = feed.fetch_google_news("stock rally surge", max_results=3)
        if news:
            has_hint = any(n.sentiment_hint is not None for n in news)
            log_test("News Has Sentiment Hints", has_hint)
        else:
            log_test("News Has Sentiment Hints", True, "Skipped - no news")
    except Exception as e:
        log_test("News Has Sentiment Hints", False, str(e))


def test_news_analyst():
    """Test the News Analyst agent."""
    print("\n" + "=" * 60)
    print("TEST: News Analyst Agent")
    print("=" * 60)
    
    try:
        from agents.analysts.news_analyst import NewsAnalyst, analyze_stock_news
        log_test("Import NewsAnalyst", True)
    except Exception as e:
        log_test("Import NewsAnalyst", False, str(e))
        return
    
    analyst = NewsAnalyst()
    
    # Test stock analysis
    try:
        report = analyst.analyze("TCS", "NSE")
        has_keys = all(k in report for k in [
            "symbol", "sentiment", "signals", "bias", "confidence", "data_quality"
        ])
        log_test("Analyze Stock (TCS)", has_keys)
        
        # Check no hallucination rule
        if report.get("data_quality") == "NO_DATA":
            has_proper_response = report.get("confidence") == 0
            log_test("No Data = Zero Confidence", has_proper_response)
        else:
            log_test("No Data = Zero Confidence", True, "Skipped - has data")
            
    except Exception as e:
        log_test("Analyze Stock (TCS)", False, str(e))
    
    # Test market analysis
    try:
        report = analyst.get_market_news_analysis()
        has_keys = "bias" in report and "sentiment" in report
        log_test("Market News Analysis", has_keys)
    except Exception as e:
        log_test("Market News Analysis", False, str(e))


def test_sentiment_analyst():
    """Test the Sentiment Analyst agent."""
    print("\n" + "=" * 60)
    print("TEST: Sentiment Analyst Agent")
    print("=" * 60)
    
    try:
        from agents.analysts.sentiment_analyst import SentimentAnalyst, analyze_sentiment
        log_test("Import SentimentAnalyst", True)
    except Exception as e:
        log_test("Import SentimentAnalyst", False, str(e))
        return
    
    analyst = SentimentAnalyst()
    
    # Test with mock technical report
    try:
        mock_tech = {
            "symbol": "TEST",
            "bias": "BULLISH",
            "confidence": 0.7,
            "indicators": {"rsi": 55},
        }
        
        report = analyst.analyze("TEST", technical_report=mock_tech)
        has_keys = all(k in report for k in [
            "data_sources", "component_sentiments", "aggregated_sentiment", "bias"
        ])
        log_test("Analyze With Technical Report", has_keys)
    except Exception as e:
        log_test("Analyze With Technical Report", False, str(e))
    
    # Test without any reports (should fetch fresh)
    try:
        report = analyst.analyze("INFY")
        has_aggregated = "aggregated_sentiment" in report
        log_test("Analyze Without Reports (INFY)", has_aggregated)
    except Exception as e:
        log_test("Analyze Without Reports (INFY)", False, str(e))
    
    # Test that missing sources are tracked
    try:
        report = analyst.analyze("TEST", technical_report=None, news_report=None)
        tracks_missing = "missing_sources" in report
        log_test("Tracks Missing Sources", tracks_missing)
    except Exception as e:
        log_test("Tracks Missing Sources", False, str(e))


def test_brain_integration():
    """Test the brain orchestrator with new analysts."""
    print("\n" + "=" * 60)
    print("TEST: Brain Integration (Phase 2)")
    print("=" * 60)
    
    try:
        from brain.orchestrator import TradingBrain
        log_test("Import TradingBrain", True)
    except Exception as e:
        log_test("Import TradingBrain", False, str(e))
        return
    
    # Test brain has new analysts
    try:
        brain = TradingBrain(paper_mode=True)
        has_news = hasattr(brain, 'news_analyst')
        has_sentiment = hasattr(brain, 'sentiment_analyst')
        log_test("Brain Has News Analyst", has_news)
        log_test("Brain Has Sentiment Analyst", has_sentiment)
    except Exception as e:
        log_test("Brain Has New Analysts", False, str(e))


def run_all_tests():
    """Run all Phase 2 tests."""
    print("\n" + "=" * 60)
    print("   QUANT-1 PHASE 2 TEST SUITE")
    print("   " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)
    
    test_sentiment_analyzer()
    test_news_feed()
    test_news_analyst()
    test_sentiment_analyst()
    test_brain_integration()
    
    # Summary
    print("\n" + "=" * 60)
    print("   TEST SUMMARY")
    print("=" * 60)
    print(f"   ✅ Passed: {test_results['passed']}")
    print(f"   ❌ Failed: {test_results['failed']}")
    print(f"   Total:   {test_results['passed'] + test_results['failed']}")
    
    if test_results["errors"]:
        print("\n   Errors:")
        for err in test_results["errors"]:
            print(f"   - {err}")
    
    print("=" * 60)
    
    return test_results["failed"] == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
