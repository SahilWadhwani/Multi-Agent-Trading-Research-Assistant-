#!/usr/bin/env python3
"""
QUICK STATUS CHECK

Shows current status of:
- Market
- Token
- Open positions
- Recent signals
- Today's P&L
"""

import os
import sys
from datetime import datetime, timedelta
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytz
IST = pytz.timezone('Asia/Kolkata')


def print_header(title):
    print(f"\n{'='*60}")
    print(f" {title}")
    print(f"{'='*60}")


def check_market():
    """Check market status."""
    now = datetime.now(IST)
    
    print_header("MARKET STATUS")
    print(f"Time: {now.strftime('%Y-%m-%d %H:%M:%S IST')}")
    
    if now.weekday() >= 5:
        print("Status: 🔴 CLOSED (Weekend)")
        return False
    
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if now < market_open:
        mins = (market_open - now).seconds // 60
        print(f"Status: 🟡 Pre-Market (opens in {mins} mins)")
        return False
    elif now > market_close:
        print("Status: 🔴 CLOSED")
        return False
    else:
        print("Status: 🟢 OPEN")
        return True


def check_token():
    """Check Upstox token."""
    print_header("UPSTOX TOKEN")
    
    try:
        from database.operations import is_token_valid, get_stored_token
        
        if is_token_valid():
            token_data = get_stored_token()
            exp = getattr(token_data, "expires_at", None) if token_data else None
            if exp:
                print(f"Status: ✅ VALID")
                print(f"Expires: {exp}")
            else:
                print("Status: ✅ VALID (no expiry info)")
            return True
        else:
            print("Status: ❌ EXPIRED or MISSING")
            print("Action: Run 'python main.py --auth' to authenticate")
            return False
    except Exception as e:
        print(f"Error: {e}")
        return False


def check_positions():
    """Check open positions."""
    print_header("OPEN POSITIONS")
    
    try:
        from brain.position_tracker import PositionTracker
        
        tracker = PositionTracker()
        positions = tracker.get_open_positions()
        
        if not positions:
            print("No open positions")
            return
        
        print(f"Total: {len(positions)} open\n")
        
        total_pnl = 0
        for pos in positions:
            pnl = getattr(pos, 'current_pnl_rs', 0) or 0
            total_pnl += pnl
            pnl_str = f"₹{pnl:+,.0f}" if pnl else "N/A"
            
            print(f"  {pos.symbol} {pos.strike} {pos.option_type}")
            print(f"    Entry: ₹{pos.entry_price:.1f} | P&L: {pnl_str}")
        
        print(f"\nTotal P&L: ₹{total_pnl:+,.0f}")
        
    except Exception as e:
        print(f"Error: {e}")


def check_signals():
    """Check recent signals."""
    print_header("RECENT SIGNALS (24h)")
    
    db_path = os.path.join(os.path.dirname(__file__), "data_cache", "signal_tracker.db")
    
    if not os.path.exists(db_path):
        print("No signal data yet")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        cursor.execute("""
            SELECT symbol, trend, signal_strength, final_decision, timestamp
            FROM scans 
            WHERE timestamp > ?
            ORDER BY timestamp DESC
            LIMIT 10
        """, (cutoff,))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            print("No signals in last 24h")
            return
        
        execute_count = sum(1 for r in rows if r['final_decision'] == 'EXECUTE')
        print(f"Total: {len(rows)} scans, {execute_count} signals\n")
        
        for row in rows[:5]:
            decision = "🟢 EXECUTE" if row['final_decision'] == 'EXECUTE' else "⏸️ SKIP"
            print(f"  {row['symbol']} {row['trend']} | {decision}")
            
    except Exception as e:
        print(f"Error: {e}")


def check_decisions():
    """Check recent trade decisions."""
    print_header("RECENT TRADES (7d)")
    
    db_path = os.path.join(os.path.dirname(__file__), "data_cache", "decisions.db")
    
    if not os.path.exists(db_path):
        print("No decision data yet")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        cursor.execute("""
            SELECT symbol, action, outcome, pnl, timestamp
            FROM decisions 
            WHERE decision_type = 'trade_entry' AND timestamp > ?
            ORDER BY timestamp DESC
            LIMIT 10
        """, (cutoff,))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            print("No trades in last 7 days")
            return
        
        total_pnl = sum(r['pnl'] or 0 for r in rows)
        wins = sum(1 for r in rows if (r['pnl'] or 0) > 0)
        
        print(f"Total: {len(rows)} trades, {wins} wins")
        print(f"Net P&L: ₹{total_pnl:+,.0f}\n")
        
        for row in rows[:5]:
            pnl = row['pnl'] or 0
            icon = "✅" if pnl > 0 else "❌" if pnl < 0 else "⏳"
            print(f"  {icon} {row['symbol']} | ₹{pnl:+,.0f}")
            
    except Exception as e:
        print(f"Error: {e}")


def main():
    print("\n" + "="*60)
    print(" TRADING AGENT STATUS")
    print("="*60)
    
    check_market()
    has_token = check_token()
    check_positions()
    check_signals()
    check_decisions()
    
    print("\n" + "="*60)
    
    if not has_token:
        print("\n⚠️  TOKEN EXPIRED - Authenticate to enable trading")
        print("   Run: python main.py --auth")
    
    print()


if __name__ == "__main__":
    main()
