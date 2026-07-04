#!/usr/bin/env python3
"""
Analyze the blocked dual-model gate trades without requiring Upstox API.

This script:
1. Fetches the 4 blocked trades from the database
2. Analyzes the signal quality (confidence, market context)
3. Estimates whether they would have been profitable based on:
   - Entry premium (from signal_tracker)
   - Market conditions at entry time (IV, trend, spot)
   - Standard theta decay patterns
"""

import os
import sys
import sqlite3
from datetime import datetime
from typing import Dict, List, Any, Optional
import pytz
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TradeAnalyzer:
    """Analyze blocked trades and estimate their theoretical performance."""
    
    def __init__(self):
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
                signal_premium, signal_strength, spot_price, iv_level, 
                iv_regime, trend, pcr, news_sentiment
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
                "iv_regime": row["iv_regime"],
                "trend": row["trend"],
                "pcr": row["pcr"],
                "news_sentiment": row["news_sentiment"],
            })
        
        return trades
    
    def estimate_exit_premium(self, trade: Dict) -> Dict[str, Any]:
        """
        Estimate exit premium based on Greeks and market movement.
        
        For options, premium changes based on:
        1. Theta decay (time decay) - always negative
        2. Delta/Gamma (spot movement) - varies with direction
        3. Vega (IV changes) - varies with IV direction
        
        Returns: {
            "exit_premium": estimated exit price,
            "theta_impact": decay from time,
            "spot_impact": change from spot movement,
            "vega_impact": change from IV,
            "total_move": total premium change
        }
        """
        entry = trade["entry_premium"]
        spot = trade["spot_price"]
        strike = trade["strike"]
        iv = trade["iv_level"]
        trend = trade["trend"]
        
        result = {
            "entry_premium": entry,
            "exit_premium": entry,
            "components": {}
        }
        
        # Estimate changes after 30 minutes (typical holding period)
        
        # 1. THETA DECAY (always works against long options, ~-20% of premium in first 30 mins)
        theta_decay = entry * 0.20  # Lose 20% of premium to time decay
        result["components"]["theta"] = -theta_decay
        
        # 2. SPOT MOVEMENT (affects delta)
        # For OTM PE: bullish movement = loss
        # For ATM/ITM options: more sensitive
        moneyness = (strike - spot) / spot * 100
        
        if trade["direction"] == "BUY_PE":
            # PE: profits if spot goes down
            if trend == "bullish" or trend == "STRONG_BULLISH":
                # Trend is bullish, PE will lose value
                delta_loss = entry * 0.15 if moneyness < -2 else entry * 0.25
                result["components"]["spot_movement"] = -delta_loss
            elif trend == "bearish" or trend == "STRONG_BEARISH":
                # Trend is bearish, PE will gain value
                delta_gain = entry * 0.10
                result["components"]["spot_movement"] = delta_gain
            else:
                # Neutral
                result["components"]["spot_movement"] = 0
        else:  # BUY_CE
            if trend == "bullish" or trend == "STRONG_BULLISH":
                delta_gain = entry * 0.10
                result["components"]["spot_movement"] = delta_gain
            elif trend == "bearish" or trend == "STRONG_BEARISH":
                delta_loss = entry * 0.15 if moneyness > 2 else entry * 0.25
                result["components"]["spot_movement"] = -delta_loss
            else:
                result["components"]["spot_movement"] = 0
        
        # 3. VEGA (IV change)
        # Lower IV = option becomes cheaper
        # Higher IV = option becomes more expensive
        if trade["iv_regime"] == "high" or trade["iv_regime"] == "elevated":
            # IV will likely contract in 30 mins
            vega_loss = entry * 0.05
            result["components"]["vega"] = -vega_loss
        elif trade["iv_regime"] == "low":
            # IV might expand
            vega_gain = entry * 0.05
            result["components"]["vega"] = vega_gain
        else:
            result["components"]["vega"] = 0
        
        # 4. NEWS IMPACT
        if trade["news_sentiment"] == "BULLISH":
            if trade["direction"] == "BUY_CE":
                news_boost = entry * 0.05
                result["components"]["news"] = news_boost
            else:
                news_loss = entry * 0.05
                result["components"]["news"] = -news_loss
        elif trade["news_sentiment"] == "BEARISH":
            if trade["direction"] == "BUY_PE":
                news_boost = entry * 0.05
                result["components"]["news"] = news_boost
            else:
                news_loss = entry * 0.05
                result["components"]["news"] = -news_loss
        else:
            result["components"]["news"] = 0
        
        # Calculate total
        total_change = sum(result["components"].values())
        exit_premium = max(0.05, entry + total_change)  # Can't go below 0.05
        
        result["exit_premium"] = exit_premium
        result["total_change"] = total_change
        
        return result
    
    def analyze_trade(self, trade: Dict) -> Dict[str, Any]:
        """Analyze one blocked trade."""
        entry_premium = trade["entry_premium"]
        qty = 50  # NIFTY lot size
        entry_value = entry_premium * qty
        
        # Estimate exit
        exit_est = self.estimate_exit_premium(trade)
        exit_premium = exit_est["exit_premium"]
        exit_value = exit_premium * qty
        
        # Calculate P&L
        pnl = exit_value - entry_value
        pnl_pct = (pnl / entry_value * 100) if entry_value > 0 else 0
        
        return {
            "trade": trade,
            "entry": {
                "premium": entry_premium,
                "value": entry_value,
                "qty": qty,
            },
            "exit": {
                "premium": exit_premium,
                "value": exit_value,
                "estimated": True,
            },
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "components": exit_est["components"],
        }
    
    def run_analysis(self) -> Dict[str, Any]:
        """Run the full analysis."""
        print("\n" + "="*80)
        print("  📊 ANALYSIS: Blocked Dual-Model Gate Trades (Theoretical P&L)")
        print("="*80)
        
        # Get blocked trades
        trades = self.get_blocked_trades()
        print(f"\nAnalyzing {len(trades)} blocked high-confidence trades\n")
        
        if not trades:
            print("No blocked trades found in database.")
            return {"status": "no_trades", "trades": []}
        
        # Analyze each trade
        results = []
        total_pnl = 0
        winning_trades = 0
        
        for i, trade in enumerate(trades, 1):
            result = self.analyze_trade(trade)
            results.append(result)
            
            # Print individual trade
            print(f"[Trade {i}]")
            print(f"  Symbol: {trade['symbol']} {trade['direction']} {trade['strike']}")
            print(f"  Entry Premium: Rs {trade['entry_premium']:.1f} (value: Rs {result['entry']['value']:,.0f})")
            print(f"  Confidence: {trade['confidence']:.0%} | Market: {trade['trend']} | IV: {trade['iv_regime']}")
            print(f"  Spot: {trade['spot_price']:.0f} | PCR: {trade['pcr']:.2f} | News: {trade['news_sentiment']}")
            
            # Show breakdown
            print(f"\n  Exit Premium Components (30-min hold):")
            for comp, impact in result['components'].items():
                pct = (impact / trade['entry_premium'] * 100) if trade['entry_premium'] > 0 else 0
                sign = "+" if impact >= 0 else "-"
                print(f"    • {comp.replace('_', ' ').title():20s}: {sign} Rs {abs(impact):6.1f} ({pct:+.1f}%)")
            
            print(f"\n  Exit Premium (estimated): Rs {result['exit']['premium']:.1f}")
            print(f"  P&L: Rs {result['pnl']:+,.0f} ({result['pnl_pct']:+.2f}%)")
            print()
            
            if result['pnl'] > 0:
                winning_trades += 1
            total_pnl += result['pnl']
        
        # Summary
        print("\n" + "="*80)
        print("  📈 THEORETICAL SUMMARY (30-minute hold assumption)")
        print("="*80)
        
        print(f"\nTrades analyzed: {len(results)}")
        print(f"Winning trades: {winning_trades}/{len(results)}")
        print(f"Win rate: {(winning_trades/len(results)*100):.1f}%")
        
        if len(results) > 0:
            avg_pnl = total_pnl / len(results)
            avg_pnl_pct = sum(r['pnl_pct'] for r in results) / len(results)
            
            print(f"\nTotal P&L (estimated): Rs {total_pnl:+,.0f}")
            print(f"Avg P&L per trade: Rs {avg_pnl:+,.0f}")
            print(f"Avg P&L %: {avg_pnl_pct:+.2f}%")
        
        # Recommendation
        print(f"\n" + "="*80)
        if winning_trades >= len(results) * 0.75:
            print("  ✅ RECOMMENDATION: These were GOOD trades to execute!")
            print("     Consider lowering the dual-gate threshold or authenticating Gemini.")
        elif winning_trades >= len(results) * 0.5:
            print("  ⚠️  MIXED RESULTS: About 50/50 trades.")
            print("     Dual-gate blocking some winners + some losers.")
        else:
            print("  ℹ️  MOSTLY LOSERS: These trades would have lost money.")
            print("     Dual-gate correctly filtered out poor setups.")
        print("="*80 + "\n")
        
        return {
            "status": "complete",
            "trades": results,
            "summary": {
                "total_trades": len(results),
                "winning_trades": winning_trades,
                "total_pnl": total_pnl,
                "win_rate_pct": (winning_trades/len(results)*100) if len(results) > 0 else 0,
                "avg_pnl": total_pnl / len(results) if len(results) > 0 else 0,
            }
        }


def main():
    try:
        analyzer = TradeAnalyzer()
        analyzer.run_analysis()
        return 0
    except Exception as e:
        print(f"❌ Analysis failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
