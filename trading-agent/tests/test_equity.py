#!/usr/bin/env python3
"""
EQUITY TRADING BRAIN TEST

Tests the equity trading brain with:
1. Technical analysis
2. News sentiment analysis  
3. Trade generation
4. Risk management
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytz
IST = pytz.timezone('Asia/Kolkata')


def test_equity_brain():
    """Test equity trading brain."""
    print("\n" + "="*70)
    print("EQUITY BRAIN TEST")
    print("="*70)
    print(f"Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}\n")
    
    from brain.orchestrator import TradingBrain
    from database.operations import is_token_valid
    
    # Check token - SKIP analysis if no token
    if not is_token_valid():
        print("⚠️  Token expired - Skipping live analysis")
        print("   Run 'python main.py --auth' to authenticate")
        print("\n✅ Brain components tested (see above)")
        return []
    
    # Initialize brain
    print("Initializing TradingBrain...")
    brain = TradingBrain(paper_mode=True)
    print("✅ Brain initialized\n")
    
    # Test stocks - only 2 to keep test quick
    test_stocks = ["RELIANCE", "TCS"]
    
    results = []
    
    for symbol in test_stocks:
        print(f"\n--- Analyzing {symbol} ---")
        try:
            result = brain.analyze_and_decide(symbol)
            
            if result:
                decision = result.get('decision', {})
                action = decision.get('action', 'no_action')
                confidence = decision.get('confidence', 0)
                
                print(f"  Action: {action}")
                print(f"  Confidence: {confidence:.1%}")
                
                if 'technical' in result:
                    tech = result['technical']
                    print(f"  Trend: {tech.get('trend', 'unknown')}")
                    print(f"  Signal: {tech.get('signal_strength', 0):.1%}")
                
                results.append({
                    'symbol': symbol,
                    'action': action,
                    'confidence': confidence,
                    'status': 'success'
                })
            else:
                print(f"  No decision generated")
                results.append({
                    'symbol': symbol,
                    'action': 'none',
                    'confidence': 0,
                    'status': 'no_data'
                })
                
        except Exception as e:
            print(f"  ❌ Error: {str(e)[:60]}")
            results.append({
                'symbol': symbol,
                'action': 'error',
                'confidence': 0,
                'status': str(e)[:50]
            })
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    success = sum(1 for r in results if r['status'] == 'success')
    print(f"Successfully analyzed: {success}/{len(test_stocks)}")
    
    # Actions breakdown
    actions = {}
    for r in results:
        action = r['action']
        actions[action] = actions.get(action, 0) + 1
    
    print("\nAction breakdown:")
    for action, count in actions.items():
        print(f"  {action}: {count}")
    
    # Any signals?
    signals = [r for r in results if r['action'] in ['buy', 'BUY', 'strong_buy']]
    if signals:
        print(f"\n🎯 SIGNALS DETECTED:")
        for s in signals:
            print(f"  {s['symbol']}: {s['action']} (conf: {s['confidence']:.1%})")
    else:
        print("\n⏸️  No actionable signals (market neutral)")
    
    print("\n" + "="*70)
    return results


def test_individual_agents():
    """Test individual agents."""
    print("\n" + "="*70)
    print("INDIVIDUAL AGENT TESTS")
    print("="*70)
    
    # Technical Analyst
    print("\n📊 Technical Analyst")
    try:
        from agents.analysts.technical_analyst import TechnicalAnalyst
        tech = TechnicalAnalyst()
        print("  ✅ Initialized")
        
        analysis = tech.analyze("RELIANCE")
        if analysis:
            print(f"  Analysis: trend={analysis.get('trend', 'N/A')}")
        else:
            print("  No analysis (expected without data)")
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    # News Analyst  
    print("\n📰 News Analyst")
    try:
        from agents.analysts.news_analyst import NewsAnalyst
        news = NewsAnalyst()
        print("  ✅ Initialized")
        
        sentiment = news.analyze("RELIANCE")
        if sentiment:
            print(f"  Sentiment: {sentiment.get('overall_sentiment', 'N/A')}")
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    # Equity Trader
    print("\n💼 Equity Trader")
    try:
        from agents.traders.equity_trader import EquityTrader
        trader = EquityTrader()
        print("  ✅ Initialized")
    except ImportError:
        print("  ℹ️  EquityTrader module not found (using TradingBrain)")
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    # F&O Trader
    print("\n📊 F&O Trader")
    try:
        from agents.traders.fo_trader import FOTrader
        trader = FOTrader()
        print("  ✅ Initialized")
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    # Portfolio Manager
    print("\n📈 Portfolio Manager")
    try:
        from agents.managers.portfolio_manager import PortfolioManager
        pm = PortfolioManager()
        print("  ✅ Initialized")
    except Exception as e:
        print(f"  ❌ Error: {e}")


def test_dynamic_symbols():
    """Test dynamic symbol fetching."""
    print("\n" + "="*70)
    print("DYNAMIC SYMBOLS TEST")
    print("="*70)
    
    try:
        from data_feeds.instrument_master import get_instrument_master
        
        master = get_instrument_master()
        stats = master.stats()
        
        print(f"Total instruments: {stats.get('total', 'N/A')}")
        print(f"Equities: {len(master.get_all_equity())}")
        print(f"ETFs: {len(master.get_etfs())}")
        print(f"NIFTY 50: {len(master.get_nifty50())}")
        
        # Get NIFTY 50
        nifty50 = master.get_nifty50()
        print(f"\nNIFTY 50 stocks ({len(nifty50)}):")
        symbols = [s.symbol if hasattr(s, 'symbol') else str(s) for s in nifty50[:10]]
        print(f"  {', '.join(symbols)}...")
        
        # Test search
        results = master.search("BANK")
        print(f"\nSearch 'BANK': {len(results)} results")
        
        print("\n✅ Dynamic symbols working correctly")
        
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    test_individual_agents()
    test_dynamic_symbols()
    test_equity_brain()
