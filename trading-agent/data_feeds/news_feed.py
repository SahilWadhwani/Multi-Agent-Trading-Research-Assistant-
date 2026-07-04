"""
News Feed for Indian Markets

Fetches news from multiple FREE sources:
- Google News RSS (no API key needed)
- yfinance news (no API key needed)  
- NewsAPI.org (free tier - 100 requests/day)

NO HALLUCINATION RULE: Only returns actual fetched data.
If a source fails, returns empty list with error message.
"""

import os
import sys
import json
import hashlib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from urllib.parse import quote_plus
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class NewsItem:
    """Structured news item."""
    title: str
    description: str
    source: str
    url: str
    published_at: Optional[str]
    symbol: Optional[str]
    sentiment_hint: Optional[str] = None  # From title keywords
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "url": self.url,
            "published_at": self.published_at,
            "symbol": self.symbol,
            "sentiment_hint": self.sentiment_hint,
        }


class IndianNewsFeed:
    """
    Fetches news relevant to Indian stock market.
    Uses only FREE data sources - no paid APIs.
    
    IMPORTANT: This class only returns ACTUAL data from sources.
    It does NOT generate, guess, or hallucinate news.
    """
    
    # Indian business news keywords for filtering
    INDIAN_KEYWORDS = [
        "NSE", "BSE", "Sensex", "Nifty", "India", "Indian",
        "RBI", "SEBI", "rupee", "INR", "Mumbai", "FII", "DII",
    ]
    
    # Positive/Negative keywords for basic sentiment hints
    POSITIVE_KEYWORDS = [
        "surge", "rally", "gain", "rise", "jump", "soar", "profit",
        "growth", "bullish", "upgrade", "beat", "record", "high",
        "strong", "boost", "positive", "outperform",
    ]
    
    NEGATIVE_KEYWORDS = [
        "fall", "drop", "crash", "decline", "loss", "plunge", "sink",
        "bearish", "downgrade", "miss", "low", "weak", "negative",
        "underperform", "concern", "fear", "risk", "warning",
    ]
    
    def __init__(self, newsapi_key: Optional[str] = None):
        """
        Initialize news feed.
        
        Args:
            newsapi_key: Optional NewsAPI.org key for additional coverage
        """
        self.newsapi_key = newsapi_key or os.getenv("NEWSAPI_KEY")
        self.cache = {}  # Simple in-memory cache
        self.cache_duration = 300  # 5 minutes
    
    def _get_cache_key(self, source: str, query: str) -> str:
        """Generate cache key."""
        return hashlib.md5(f"{source}:{query}".encode()).hexdigest()
    
    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cache entry is still valid."""
        if cache_key not in self.cache:
            return False
        entry = self.cache[cache_key]
        entry_time = entry["timestamp"]
        # Handle timezone-aware timestamps
        if hasattr(entry_time, 'tzinfo') and entry_time.tzinfo:
            entry_time = entry_time.replace(tzinfo=None)
        return (datetime.utcnow() - entry_time).seconds < self.cache_duration
    
    def _add_sentiment_hint(self, title: str) -> Optional[str]:
        """Add basic sentiment hint based on title keywords."""
        title_lower = title.lower()
        
        pos_count = sum(1 for kw in self.POSITIVE_KEYWORDS if kw in title_lower)
        neg_count = sum(1 for kw in self.NEGATIVE_KEYWORDS if kw in title_lower)
        
        if pos_count > neg_count:
            return "POSITIVE"
        elif neg_count > pos_count:
            return "NEGATIVE"
        return "NEUTRAL"
    
    def _parse_pub_date(self, pub_date_str: str) -> Optional[datetime]:
        """Parse various date formats from news feeds."""
        if not pub_date_str:
            return None
        
        # Common date formats
        formats = [
            "%a, %d %b %Y %H:%M:%S %Z",      # RFC 822: "Sat, 03 May 2026 10:30:00 GMT"
            "%a, %d %b %Y %H:%M:%S %z",      # With timezone offset
            "%Y-%m-%dT%H:%M:%SZ",             # ISO format
            "%Y-%m-%dT%H:%M:%S%z",            # ISO with timezone
            "%Y-%m-%d %H:%M:%S",              # Simple datetime
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(pub_date_str.strip(), fmt)
            except ValueError:
                continue
        
        return None
    
    def _is_fresh_news(self, pub_date_str: str, max_age_hours: int = 48) -> bool:
        """Check if news is within the freshness window."""
        pub_date = self._parse_pub_date(pub_date_str)
        if not pub_date:
            # If we can't parse the date, assume it might be fresh
            return True
        
        # Make timezone-naive for comparison
        if pub_date.tzinfo:
            pub_date = pub_date.replace(tzinfo=None)
        
        age = datetime.utcnow() - pub_date
        return age.total_seconds() < (max_age_hours * 3600)
    
    def fetch_google_news(
        self,
        query: str,
        max_results: int = 10,
        max_age_hours: int = 48,  # Only get news from last 48 hours
    ) -> List[NewsItem]:
        """
        Fetch FRESH news from Google News RSS feed (FREE, no API key).
        
        Args:
            query: Search query (e.g., "RELIANCE stock" or "Indian market")
            max_results: Maximum number of results
            max_age_hours: Only include news from the last N hours (default: 48)
        
        Returns:
            List of NewsItem objects (ONLY FRESH NEWS)
        """
        cache_key = self._get_cache_key("google", query)
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]["data"]
        
        news_items = []
        
        try:
            # Google News RSS URL - add "when:2d" for news from last 2 days
            encoded_query = quote_plus(f"{query} when:2d")
            url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
            
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            # Parse RSS XML
            root = ET.fromstring(response.content)
            channel = root.find("channel")
            
            if channel is None:
                return []
            
            fresh_count = 0
            for item in channel.findall("item"):
                if fresh_count >= max_results:
                    break
                    
                title = item.find("title")
                link = item.find("link")
                pub_date = item.find("pubDate")
                description = item.find("description")
                source = item.find("source")
                
                pub_date_str = pub_date.text if pub_date is not None else None
                
                # FRESHNESS CHECK: Skip old news
                if pub_date_str and not self._is_fresh_news(pub_date_str, max_age_hours):
                    continue
                
                if title is not None and link is not None:
                    news_item = NewsItem(
                        title=title.text or "",
                        description=description.text if description is not None else "",
                        source=source.text if source is not None else "Google News",
                        url=link.text or "",
                        published_at=pub_date_str,
                        symbol=query.split()[0] if query else None,
                        sentiment_hint=self._add_sentiment_hint(title.text or ""),
                    )
                    news_items.append(news_item)
                    fresh_count += 1
            
            # Cache results
            self.cache[cache_key] = {
                "data": news_items,
                "timestamp": datetime.utcnow(),
            }
            
        except Exception as e:
            # Log error but don't crash - return empty with error info
            print(f"Google News fetch error: {e}")
            return []
        
        return news_items
    
    def fetch_newsapi(
        self,
        query: str,
        max_results: int = 10,
        days_back: int = 7,
    ) -> List[NewsItem]:
        """
        Fetch news from NewsAPI.org (FREE tier: 100 requests/day).
        
        Args:
            query: Search query
            max_results: Maximum results
            days_back: How many days back to search
        
        Returns:
            List of NewsItem objects
        """
        if not self.newsapi_key:
            return []  # No API key configured
        
        cache_key = self._get_cache_key("newsapi", query)
        if self._is_cache_valid(cache_key):
            return self.cache[cache_key]["data"]
        
        news_items = []
        
        try:
            from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": query,
                "from": from_date,
                "sortBy": "relevancy",
                "language": "en",
                "pageSize": max_results,
                "apiKey": self.newsapi_key,
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") != "ok":
                return []
            
            for article in data.get("articles", [])[:max_results]:
                news_item = NewsItem(
                    title=article.get("title", ""),
                    description=article.get("description", ""),
                    source=article.get("source", {}).get("name", "NewsAPI"),
                    url=article.get("url", ""),
                    published_at=article.get("publishedAt"),
                    symbol=query.split()[0] if query else None,
                    sentiment_hint=self._add_sentiment_hint(article.get("title", "")),
                )
                news_items.append(news_item)
            
            self.cache[cache_key] = {
                "data": news_items,
                "timestamp": datetime.now(),
            }
            
        except Exception as e:
            print(f"NewsAPI fetch error: {e}")
            return []
        
        return news_items
    
    def fetch_stock_news(
        self,
        symbol: str,
        max_results: int = 10,
    ) -> Dict[str, Any]:
        """
        Fetch news for a specific stock symbol.
        Combines multiple sources.
        
        Args:
            symbol: Stock symbol (e.g., "RELIANCE", "TCS")
            max_results: Max results per source
        
        Returns:
            Dict with news items and metadata
        """
        all_news = []
        sources_used = []
        errors = []
        
        # Google News - always available
        try:
            google_news = self.fetch_google_news(
                f"{symbol} stock NSE India",
                max_results=max_results,
            )
            all_news.extend(google_news)
            if google_news:
                sources_used.append("Google News")
        except Exception as e:
            errors.append(f"Google News: {e}")
        
        # NewsAPI if key available
        if self.newsapi_key:
            try:
                newsapi_news = self.fetch_newsapi(
                    f"{symbol} India stock",
                    max_results=max_results,
                )
                all_news.extend(newsapi_news)
                if newsapi_news:
                    sources_used.append("NewsAPI")
            except Exception as e:
                errors.append(f"NewsAPI: {e}")
        
        # Deduplicate by title similarity
        unique_news = self._deduplicate_news(all_news)
        
        # Sort by date (newest first)
        unique_news.sort(
            key=lambda x: x.published_at or "",
            reverse=True,
        )
        
        return {
            "symbol": symbol,
            "news_count": len(unique_news),
            "sources": sources_used,
            "errors": errors if errors else None,
            "news": [n.to_dict() for n in unique_news[:max_results]],
            "fetched_at": datetime.utcnow().isoformat(),
        }
    
    def fetch_market_news(self, max_results: int = 15) -> Dict[str, Any]:
        """
        Fetch general Indian market news.
        
        Returns:
            Dict with market news and metadata
        """
        queries = [
            "Indian stock market today",
            "Sensex Nifty",
            "NSE BSE trading",
        ]
        
        all_news = []
        sources_used = set()
        
        for query in queries:
            news = self.fetch_google_news(query, max_results=5)
            all_news.extend(news)
            if news:
                sources_used.add("Google News")
        
        unique_news = self._deduplicate_news(all_news)
        unique_news.sort(key=lambda x: x.published_at or "", reverse=True)
        
        return {
            "type": "market_overview",
            "news_count": len(unique_news),
            "sources": list(sources_used),
            "news": [n.to_dict() for n in unique_news[:max_results]],
            "fetched_at": datetime.utcnow().isoformat(),
        }
    
    def _deduplicate_news(self, news_items: List[NewsItem]) -> List[NewsItem]:
        """Remove duplicate news items based on title similarity."""
        seen_titles = set()
        unique = []
        
        for item in news_items:
            # Normalize title for comparison
            normalized = item.title.lower().strip()[:50]
            if normalized not in seen_titles:
                seen_titles.add(normalized)
                unique.append(item)
        
        return unique


# Singleton instance
_news_feed_instance = None

def get_news_feed() -> IndianNewsFeed:
    """Get the news feed singleton."""
    global _news_feed_instance
    if _news_feed_instance is None:
        _news_feed_instance = IndianNewsFeed()
    return _news_feed_instance
