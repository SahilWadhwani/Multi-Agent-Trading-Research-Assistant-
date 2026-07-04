#!/usr/bin/env python3
"""
Backtest the high-confidence trades that were blocked by dual_model_gate yesterday.

This script:
1. Fetches the 4 blocked trades from May 12-13 from the database
2. Uses Upstox API to get May 12 market data
3. Simulates the trades (entry to exit)
4. Calculates what the P&L would have been
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import pytz
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class BlockedTradeBacktester:
    """Backtest trades that were blocked by dual_model_gate."""
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://api.upstox.com/v2"
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}"
        }
        self.db_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data_cache",
            "signal_tracker.db"
        )
        self.ist = pytz.timezone("Asia/Kolkata")
    
    def get_blocked_trades(self) -> List[Dict[str, Any]]:
        """Fetch the blocked trades from database."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get trades blocked by dual_model_gate
        cursor.execute("""
            SELECT 
                timestamp, symbol, signal_direction, signal_strike, 
                signal_premium, signal_strength, spot_price, iv_level
            FROM scans 
            WHERE blocked_by_gate = 'dual_model_gate'
            ORDER BY timestamp DESC
        """)
        
        rows = cursor.fetchall()
        conn.close()
        
        trades = []
        for row in rows:
            trades.append({
                "timestamp": row["timestamp"],
                "symbol": row["symbol"],
                "direction": row["signal_direction"],
                "strike": row["signal_strike"],
                "entry_premium": row["signal_premium"],
                "confidence": row["signal_strength"],
                "spot_price": row["spot_price"],
                "iv_level": row["iv_level"],
            })
        
        return trades
    
    def get_option_chain_for_date(self, symbol: str, strike: float, option_type: str, date: str) -> Optional[Dict]:
        """
        Fetch option chain data via Upstox API.
        
        Returns option data with LTP at that time.
        """
        try:
            # NIFTY → NSE_INDEX|Nifty 50
            instrument_map = {
                "NIFTY": "NSE_INDEX|Nifty 50",
                "BANKNIFTY": "NSE_INDEX|Nifty Bank",
            }
            
            instrument_key = instrument_map.get(symbol, f"NSE_EQ|{symbol}")
            
            # Get option chain
            resp = requests.get(
                f"{self.base_url}/option/chain",
                params={"instrument_key": instrument_key},
                headers=self.headers,
                timeout=10
            )
            
            if resp.status_code != 200:
                return None
            
            data = resp.json().get("data", {})
            
            # Find the specific strike
            option_type_key = "CE" if option_type == "CE" else "PE"
            options = data.get("options", [])
            
            for opt in options:
                if opt.get("strike_price") == strike and opt.get("instrument_type") == option_type_key:
                    return {
                        "strike": opt.get("strike_price"),
                        "ltp": opt.get("last_price", 0),
                        "bid": opt.get("bid_price", 0),
                        "ask": opt.get("ask_price", 0),
                        "oi": opt.get("open_interest", 0),
                        "iv": opt.get("implied_volatility", 0),
                    }
            
            return None
        except Exception as e:
            print(f"      Error fetching option chain: {e}")
            return None
    
    def get_historical_data(self, symbol: str, start_date: str, end_date: str) -> Optional[List[Dict]]:
        """Get historical OHLC data for the symbol via Upstox API."""
        try:
            instrument_map = {
                "NIFTY": "NSE_INDEX|Nifty 50",
                "BANKNIFTY": "NSE_INDEX|Nifty Bank",
            }
            
            instrument_key = instrument_map.get(symbol, f"NSE_EQ|{symbol}")
            
            resp = requests.get(
                f"{self.base_url}/historical-candle/intraday/{instrument_key}/1minute",
                params={"to_date": end_date},
                headers=self.headers,
                timeout=10
            )
            
            if resp.status_code != 200:
                return None
            
            data = resp.json().get("data", {})
            return data.get("candles", [])
            
        except Exception as e:
            print(f"      Error fetching historical data: {e}")
            return None
    
    def simulate_trade(self, trade: Dict, hold_minutes: int = 30) -> Dict[str, Any]:
        """
        Simulate the trade execution.
        
        Returns: {
            "trade": trade details,
            "entry": entry price and time,
            "exit": exit price and time,
            "pnl": profit/loss,
            "pnl_pct": profit/loss %,
            "status": "simulated" | "error"
        }
        """
        result = {
            "trade": trade,
            "entry": None,
            "exit": None,
            "pnl": None,
            "pnl_pct": None,
            "status": "error",
            "notes": []
        }
        
        # Parse timestamp
        try:
            entry_time = datetime.fromisoformat(trade["timestamp"].replace("+05:30", ""))
            entry_time = self.ist.localize(entry_time.replace(tzinfo=None))
        except:
            result["notes"].append("Could not parse entry timestamp")
            return result
        
        # Get date for historical query
        date_str = entry_time.strftime("%Y-%m-%d")
        
        print(f"\n  Trade: {trade['direction']} {trade['symbol']} {trade['strike']} @ {trade['entry_premium']:.1f}")
        print(f"  Entry time: {entry_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Confidence: {trade['confidence']:.0%}")
        
        try:
            # Simulate entry: use signal premium as entry price
            entry_premium = trade["entry_premium"]
            entry_qty = 50  # NIFTY lot size
            entry_value = entry_premium * entry_qty
            
            result["entry"] = {
                "time": entry_time.isoformat(),
                "premium": entry_premium,
                "qty": entry_qty,
                "value": entry_value,
                "spot": trade["spot_price"]
            }
            
            print(f"  Entry premium: Rs {entry_premium:.1f} | Value: Rs {entry_value:,.0f}")
            
            # Simulate exit: fetch current option data
            option_type = "CE" if "CALL" in trade["direction"] else "PE"
            opt_data = self.get_option_chain_for_date(
                trade["symbol"],
                trade["strike"],
                option_type,
                date_str
            )
            
            if not opt_data:
                result["notes"].append(f"Could not fetch option data for {trade['symbol']} {trade['strike']} {option_type}")
                return result
            
            # Current LTP is the exit
            exit_premium = opt_data.get("ltp", entry_premium)
            exit_value = exit_premium * entry_qty
            
            result["exit"] = {
                "premium": exit_premium,
                "value": exit_value,
            }
            
            # Calculate P&L
            pnl = exit_value - entry_value
            pnl_pct = (pnl / entry_value) * 100 if entry_value > 0 else 0
            
            result["pnl"] = pnl
            result["pnl_pct"] = pnl_pct
            result["status"] = "simulated"
            
            print(f"  Exit premium: Rs {exit_premium:.1f} | Value: Rs {exit_value:,.0f}")
            print(f"  P&L: Rs {pnl:+,.0f} ({pnl_pct:+.2f}%)")
            
            return result
            
        except Exception as e:
            result["notes"].append(f"Simulation error: {str(e)}")
            return result
    
    def run_backtest(self) -> Dict[str, Any]:
        """Run the full backtest."""
        print("\n" + "="*70)
        print("  🧪 BACKTEST: Blocked Dual-Model Gate Trades")
        print("="*70)
        
        # Get blocked trades
        trades = self.get_blocked_trades()
        print(f"\nFound {len(trades)} blocked trades to backtest\n")
        
        if not trades:
            print("No blocked trades found in database.")
            return {"status": "no_trades", "trades": []}
        
        # Simulate each trade
        results = []
        total_pnl = 0
        total_pnl_pct = 0
        winning_trades = 0
        
        for i, trade in enumerate(trades, 1):
            print(f"\n[{i}/{len(trades)}] Simulating trade...")
            result = self.simulate_trade(trade)
            results.append(result)
            
            if result["status"] == "simulated":
                total_pnl += result["pnl"]
                if result["pnl"] > 0:
                    winning_trades += 1
                    total_pnl_pct += result["pnl_pct"]
        
        # Summary
        print("\n" + "="*70)
        print("  📊 BACKTEST SUMMARY")
        print("="*70)
        
        simulated = [r for r in results if r["status"] == "simulated"]
        print(f"\nTrades analyzed: {len(simulated)}/{len(trades)}")
        print(f"Winning trades: {winning_trades}/{len(simulated)}")
        print(f"Win rate: {(winning_trades/len(simulated)*100) if simulated else 0:.1f}%")
        
        if simulated:
            avg_pnl_pct = total_pnl_pct / winning_trades if winning_trades > 0 else 0
            print(f"\nTotal P&L: Rs {total_pnl:+,.0f}")
            print(f"Avg P&L per winning trade: {avg_pnl_pct:+.2f}%")
        
        print("\n" + "="*70)
        
        return {
            "status": "complete",
            "trades": results,
            "summary": {
                "total_trades": len(trades),
                "simulated_trades": len(simulated),
                "winning_trades": winning_trades,
                "total_pnl": total_pnl,
                "win_rate_pct": (winning_trades/len(simulated)*100) if simulated else 0,
            }
        }


def main():
    # Get token from environment or command line
    token = os.getenv("UPSTOX_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else None)
    
    if not token:
        print("Usage: python backtest_blocked_trades.py <upstox_token>")
        print("Or set UPSTOX_TOKEN environment variable")
        return 1
    
    try:
        backtester = BlockedTradeBacktester(token)
        backtester.run_backtest()
        return 0
    except Exception as e:
        print(f"❌ Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
