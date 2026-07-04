#!/usr/bin/env python3
"""
Upstox Connection Test Suite

Tests the Upstox API connection independently.
This test requires authentication - run `python main.py --auth` first.

Run with: python -m tests.test_upstox_connection
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_upstox_connection():
    """Test Upstox API connection."""
    print("\n" + "=" * 60)
    print("   UPSTOX CONNECTION TEST")
    print("   " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)
    
    from mcp_server.upstox_client import get_upstox_client
    from mcp_server.guardrails import is_market_hours, get_market_status
    
    # Market status (no auth needed)
    is_open, status_msg = get_market_status()
    print(f"\n📊 Market Status: {status_msg}")
    
    # Check authentication
    client = get_upstox_client()
    
    print("\n" + "-" * 40)
    print("AUTHENTICATION STATUS:")
    print("-" * 40)
    
    if not client.is_authenticated():
        print("❌ Not authenticated")
        print("\n⚠️  To authenticate, run:")
        print("   python main.py --auth")
        print("\nNote: This requires:")
        print("  1. Valid Upstox API credentials in .env")
        print("  2. Trading segments activated in your Upstox account")
        print("  3. Browser access for OAuth flow")
        return False
    
    print("✅ Authenticated with valid token")
    
    # Test API endpoints
    print("\n" + "-" * 40)
    print("API ENDPOINT TESTS:")
    print("-" * 40)
    
    tests_passed = 0
    tests_failed = 0
    
    # Test 1: Profile
    try:
        profile = client.get_profile()
        if profile.get("status") == "success":
            data = profile.get("data", {})
            print(f"✅ Profile: {data.get('user_name', 'Unknown')}")
            tests_passed += 1
        else:
            print(f"❌ Profile: {profile.get('message', 'Unknown error')}")
            tests_failed += 1
    except Exception as e:
        print(f"❌ Profile: {e}")
        tests_failed += 1
    
    # Test 2: Funds
    try:
        funds = client.get_funds_and_margin()
        if funds.get("status") == "success":
            equity = funds.get("data", {}).get("equity", {})
            margin = equity.get("available_margin", 0)
            print(f"✅ Funds: ₹{float(margin):,.2f} available")
            tests_passed += 1
        else:
            print(f"❌ Funds: {funds.get('message', 'Unknown error')}")
            tests_failed += 1
    except Exception as e:
        print(f"❌ Funds: {e}")
        tests_failed += 1
    
    # Test 3: Positions
    try:
        positions = client.get_positions()
        if positions.get("status") == "success":
            pos_data = positions.get("data", [])
            print(f"✅ Positions: {len(pos_data)} open positions")
            tests_passed += 1
        else:
            print(f"❌ Positions: {positions.get('message', 'Unknown error')}")
            tests_failed += 1
    except Exception as e:
        print(f"❌ Positions: {e}")
        tests_failed += 1
    
    # Test 4: Holdings
    try:
        holdings = client.get_holdings()
        if holdings.get("status") == "success":
            hold_data = holdings.get("data", [])
            print(f"✅ Holdings: {len(hold_data)} holdings")
            tests_passed += 1
        else:
            print(f"❌ Holdings: {holdings.get('message', 'Unknown error')}")
            tests_failed += 1
    except Exception as e:
        print(f"❌ Holdings: {e}")
        tests_failed += 1
    
    # Test 5: Market Quote (only during market hours or if API allows)
    try:
        quote = client.get_market_quote("RELIANCE", "NSE")
        if quote.get("status") == "success":
            data = list(quote.get("data", {}).values())
            if data:
                ltp = data[0].get("last_price", "N/A")
                print(f"✅ Quote (RELIANCE): ₹{ltp}")
            tests_passed += 1
        else:
            # Market might be closed
            msg = quote.get("message", "")
            if "market" in msg.lower() or "closed" in msg.lower():
                print(f"⚠️  Quote: Market closed (expected)")
                tests_passed += 1
            else:
                print(f"❌ Quote: {msg}")
                tests_failed += 1
    except Exception as e:
        print(f"❌ Quote: {e}")
        tests_failed += 1
    
    # Summary
    print("\n" + "-" * 40)
    print("TEST SUMMARY:")
    print("-" * 40)
    print(f"✅ Passed: {tests_passed}")
    print(f"❌ Failed: {tests_failed}")
    
    if tests_failed == 0:
        print("\n🎉 All Upstox API tests passed!")
        print("   Ready for live trading (when market opens)")
    else:
        print("\n⚠️  Some tests failed. Check:")
        print("   1. API credentials in .env")
        print("   2. Trading segments activated")
        print("   3. Network connectivity")
    
    print("=" * 60)
    
    return tests_failed == 0


if __name__ == "__main__":
    success = test_upstox_connection()
    sys.exit(0 if success else 1)
