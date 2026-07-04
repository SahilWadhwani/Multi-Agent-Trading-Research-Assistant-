#!/usr/bin/env python3
"""
QUANT-1: AI Trading Agent - Main Entry Point

Multi-agent trading system for Indian markets (NSE/BSE).
Orchestrates: Technical Analyst → Trader → Risk Manager → Portfolio Manager

Usage:
    python main.py --auth           # Authenticate with Upstox
    python main.py --status         # Check market & account status
    python main.py --analyze SYMBOL # Analyze a stock
    python main.py --decide SYMBOL  # Full analysis and trade decision
    python main.py --scan           # Scan watchlist for opportunities
    python main.py --dashboard      # Launch monitoring dashboard
"""

import os
import sys
import json
import argparse
from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.schema import init_database


def run_authentication():
    """Run the Upstox authentication flow."""
    print("\n" + "="*60)
    print("  UPSTOX AUTHENTICATION")
    print("="*60 + "\n")
    
    from mcp_server.upstox_client import UpstoxClient
    
    client = UpstoxClient()
    if client.authenticate():
        print("\n✓ Authentication successful!")
        
        # Test the connection
        print("\nTesting API connection...")
        try:
            profile = client.get_profile()
            if profile.get("status") == "success":
                data = profile.get("data", {})
                print(f"  ✓ Connected as: {data.get('user_name', 'Unknown')}")
                print(f"  ✓ Email: {data.get('email', 'N/A')}")
                
            funds = client.get_funds_and_margin()
            if funds.get("status") == "success":
                equity = funds.get("data", {}).get("equity", {})
                available = equity.get("available_margin", 0)
                print(f"  ✓ Available Margin: ₹{float(available):,.2f}")
        except Exception as e:
            print(f"  ⚠ Could not fetch details: {e}")
        
        print("\n" + "="*60)
        print("  READY! You can now use Claude/Cursor to trade.")
        print("="*60)
    else:
        print("\n✗ Authentication failed. Please try again.")
        return False
    
    return True


def show_status():
    """Show current trading status."""
    print("\n" + "="*60)
    print("  TRADING STATUS")
    print("="*60 + "\n")
    
    from agent.trading_tools import get_trading_tools
    
    tools = get_trading_tools()
    
    # Market status
    status = tools.get_market_status()
    print(f"📊 Market: {status['status']} - {status['message']}")
    print(f"⏰ Time: {status['current_time_ist']}")
    print(f"🎯 Mode: {status['trading_mode']} TRADING")
    
    # Try to get balance
    print("\n" + "-"*40)
    balance = tools.get_balance()
    if "error" in balance:
        print(f"⚠ Balance: {balance['error']}")
    else:
        if balance.get("funds", {}).get("status") == "success":
            equity = balance["funds"]["data"].get("equity", {})
            print(f"💰 Available Margin: ₹{float(equity.get('available_margin', 0)):,.2f}")
            print(f"📈 Max Trade Value: ₹{balance['max_trade_value']:,.2f} (20% limit)")
    
    # Daily summary
    print("\n" + "-"*40)
    summary = tools.get_daily_summary()
    print(f"📈 Today's P&L: ₹{summary['todays_pnl']:,.2f}")
    print(f"🔄 Today's Trades: {summary['todays_trades']}")
    wr = summary['win_rate']
    print(f"🎯 Win Rate: {wr['win_rate']:.1f}% ({wr['winning_trades']}/{wr['total_trades']})")
    
    # Holdings
    if summary['holdings']:
        print("\n" + "-"*40)
        print("📦 Current Holdings:")
        for symbol, data in summary['holdings'].items():
            print(f"   {symbol}: {data['quantity']} @ ₹{data['avg_price']:.2f} ({data['side']})")
    
    print("\n" + "="*60 + "\n")


def run_dashboard():
    """Launch the Streamlit dashboard."""
    import subprocess
    
    dashboard_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "dashboard",
        "app.py"
    )
    
    print("\n" + "="*60)
    print("  LAUNCHING DASHBOARD")
    print("="*60)
    print("\n  Opening at: http://localhost:8501")
    print("  Press Ctrl+C to stop\n")
    
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", dashboard_path,
        "--server.port", "8501",
        "--server.headless", "true",
    ])


def run_mcp_server():
    """Run the MCP server for Cursor integration."""
    print("\n" + "="*60)
    print("  STARTING MCP SERVER")
    print("="*60 + "\n")
    
    from mcp_server.server import main as mcp_main
    mcp_main()


def quick_trade(symbol: str, side: str, quantity: int, reasoning: str):
    """Execute a quick trade from command line."""
    from agent.trading_tools import get_trading_tools
    
    tools = get_trading_tools()
    result = tools.execute_trade(
        symbol=symbol,
        side=side,
        quantity=quantity,
        reasoning=reasoning,
    )
    
    print(json.dumps(result, indent=2, default=str))


def analyze_stock(symbol: str, exchange: str = "NSE"):
    """Perform technical analysis on a stock."""
    print(f"\n🔍 Analyzing {symbol} on {exchange}...")
    print("=" * 60)
    
    from brain.orchestrator import TradingBrain
    
    brain = TradingBrain(paper_mode=True)
    report = brain.analyze_stock(symbol, exchange)
    
    if "error" in report:
        print(f"\n❌ Error: {report['error']}")
        return
    
    print(f"\n📊 {symbol} Technical Analysis")
    print("-" * 40)
    print(f"Current Price: ₹{report.get('quote', {}).get('ltp', 'N/A')}")
    print(f"Bias: {report.get('bias', 'N/A')}")
    print(f"Confidence: {report.get('confidence', 0):.0%}")
    print(f"\nSummary: {report.get('summary', 'N/A')}")
    
    if report.get("signals"):
        print("\nSignals:")
        for sig in report["signals"]:
            print(f"  - {sig.get('indicator')}: {sig.get('action')} ({sig.get('strength')})")
    
    sr = report.get("support_resistance", {})
    if sr.get("nearest_support"):
        print(f"\nSupport: ₹{sr['nearest_support']['level']} ({sr['nearest_support']['type']})")
    if sr.get("nearest_resistance"):
        print(f"Resistance: ₹{sr['nearest_resistance']['level']} ({sr['nearest_resistance']['type']})")
    
    print("=" * 60)


def make_decision(symbol: str, exchange: str = "NSE", product: str = "INTRADAY"):
    """Full analysis and trading decision."""
    print(f"\n🧠 QUANT-1 Decision Engine")
    print("=" * 60)
    print(f"Symbol: {symbol} | Exchange: {exchange} | Product: {product}")
    print("=" * 60)
    
    from brain.orchestrator import TradingBrain
    
    brain = TradingBrain(paper_mode=True)
    result = brain.analyze_and_decide(
        symbol=symbol,
        exchange=exchange,
        product_type=product,
    )
    
    if "error" in result:
        print(f"\n❌ Error at {result.get('stage', 'unknown')}: {result['error']}")
        return
    
    # Print explanation
    explanation = brain.explain_decision(result)
    print(explanation)
    
    # Save result to file
    output_file = f"decision_{symbol}_{result['timestamp'][:10]}.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\n📁 Full result saved to: {output_file}")


def scan_watchlist():
    """Scan watchlist for opportunities."""
    # Default Indian market watchlist
    watchlist = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
        "BHARTIARTL", "ITC", "SBIN", "KOTAKBANK", "LT",
    ]
    
    print(f"\n🔎 Scanning Watchlist ({len(watchlist)} stocks)")
    print("=" * 60)
    
    from brain.orchestrator import TradingBrain
    
    brain = TradingBrain(paper_mode=True)
    opportunities = brain.scan_watchlist(watchlist)
    
    if not opportunities:
        print("\n📭 No actionable opportunities found")
    else:
        print(f"\n🎯 Found {len(opportunities)} opportunities:\n")
        for i, opp in enumerate(opportunities, 1):
            print(f"{i}. {opp['action']} {opp['symbol']}")
            print(f"   Confidence: {opp['confidence']:.0%}")
            print(f"   Price: ₹{opp['price']:,.2f}")
            print(f"   Qty: {opp['quantity']}")
            print()
    
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="QUANT-1: Multi-Agent AI Trading System for Indian Markets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
🧠 QUANT-1 Commands:

  Authentication:
    python main.py --auth           Authenticate with Upstox
    
  Analysis:
    python main.py --status         Check market & account status
    python main.py --analyze TCS    Technical analysis of a stock
    python main.py --decide TCS     Full analysis + trade decision
    python main.py --scan           Scan watchlist for opportunities
    
  Monitoring:
    python main.py --dashboard      Launch Streamlit dashboard

  Trading (Manual):
    python main.py --trade -s TCS --side BUY --qty 10

Examples:
    python main.py --analyze RELIANCE
    python main.py --decide HDFCBANK --product DELIVERY
    python main.py --scan
        """
    )
    
    # Core commands
    parser.add_argument("--auth", action="store_true", help="Authenticate with Upstox")
    parser.add_argument("--status", action="store_true", help="Show trading status")
    parser.add_argument("--dashboard", action="store_true", help="Launch Streamlit dashboard")
    parser.add_argument("--mcp", action="store_true", help="Start MCP server")
    
    # Brain commands
    parser.add_argument("--analyze", metavar="SYMBOL", help="Technical analysis of a stock")
    parser.add_argument("--decide", metavar="SYMBOL", help="Full analysis and trade decision")
    parser.add_argument("--scan", action="store_true", help="Scan watchlist for opportunities")
    parser.add_argument("--exchange", "-e", default="NSE", choices=["NSE", "BSE"], help="Exchange")
    parser.add_argument("--product", "-p", default="INTRADAY", choices=["INTRADAY", "DELIVERY"], help="Product type")
    
    # Manual trade options
    parser.add_argument("--trade", action="store_true", help="Execute a manual trade")
    parser.add_argument("--symbol", "-s", help="Stock symbol for trade")
    parser.add_argument("--side", choices=["BUY", "SELL"], help="Trade side")
    parser.add_argument("--qty", type=int, help="Quantity")
    parser.add_argument("--reason", default="Manual CLI trade", help="Reasoning")
    
    args = parser.parse_args()
    
    # Initialize database
    init_database()
    
    if args.auth:
        run_authentication()
    elif args.status:
        show_status()
    elif args.analyze:
        analyze_stock(args.analyze, args.exchange)
    elif args.decide:
        make_decision(args.decide, args.exchange, args.product)
    elif args.scan:
        scan_watchlist()
    elif args.dashboard:
        run_dashboard()
    elif args.mcp:
        run_mcp_server()
    elif args.trade:
        if not all([args.symbol, args.side, args.qty]):
            print("Error: --trade requires --symbol (-s), --side, and --qty")
            return
        quick_trade(args.symbol, args.side, args.qty, args.reason)
    else:
        # Default: show status
        show_status()
        print("\n🧠 QUANT-1 Trading Agent Ready!")
        print("Use --help for all commands")
        print("Use --analyze SYMBOL for quick analysis")
        print("Use --decide SYMBOL for full trade decision")


if __name__ == "__main__":
    main()
