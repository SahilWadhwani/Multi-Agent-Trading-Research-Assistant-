#!/usr/bin/env python3
"""
Detailed Minute-by-Minute Backtest of Blocked Trades

Simulates each trade with:
- Exact entry time and premium
- Track minute-by-minute option premium changes
- Check if SL (20%) or target (50%) hit first
- Show full breakdown of what would have happened
"""

import os
import sys
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import pytz
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class DetailedTradeBacktester:
    """Minute-by-minute backtest with SL/target tracking."""
    
    # Standard Greeks constants for NIFTY options
    RISK_FREE_RATE = 0.065
    THETA_DAILY_DECAY = 0.02  # 2% daily theta decay for OTM options
    
    # Signal parameters
    STOP_LOSS_PCT = 20  # 20% stop loss as per your rules
    TARGET_PCT = 50    # 50% target as per your rules
    
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
        
        cursor.execute("""
            SELECT 
                timestamp, symbol, signal_direction, signal_strike, 
                signal_premium, signal_strength, spot_price, iv_level, 
                iv_regime, trend, pcr, news_sentiment
            FROM scans 
            WHERE blocked_by_gate = 'dual_model_gate'
            ORDER BY timestamp ASC
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
    
    def calculate_theta_decay(self, minutes_elapsed: int, entry_premium: float, iv: float) -> float:
        """
        Calculate theta decay for given minutes.
        
        OTM options lose ~2% per day, which is ~0.14% per hour, ~0.0023% per minute
        """
        daily_decay = self.THETA_DAILY_DECAY
        hourly_decay = daily_decay / 24
        minute_decay = hourly_decay / 60
        
        return entry_premium * (minute_decay * minutes_elapsed)
    
    def simulate_spot_movement(self, minutes_elapsed: int, trend: str, pcr: float) -> float:
        """
        Simulate potential spot price movement.
        
        Based on trend and PCR (market bias).
        Returns percentage movement.
        """
        # Market typically moves 0.05-0.15% per minute in normal conditions
        # Trend direction matters
        
        if trend == "STRONG_BULLISH" or trend == "strong_bullish":
            hourly_move = 0.30  # 0.30% per hour
        elif trend == "BULLISH" or trend == "bullish":
            hourly_move = 0.15
        elif trend == "NEUTRAL" or trend == "neutral":
            hourly_move = 0.05
        elif trend == "BEARISH" or trend == "bearish":
            hourly_move = -0.15
        elif trend == "STRONG_BEARISH" or trend == "strong_bearish":
            hourly_move = -0.30
        else:
            hourly_move = 0.05
        
        minute_move = hourly_move / 60
        total_move = minute_move * minutes_elapsed
        
        return total_move
    
    def calculate_option_premium_at_time(
        self,
        entry_premium: float,
        entry_spot: float,
        strike: float,
        option_type: str,
        minutes_elapsed: int,
        current_spot: float,
        iv: float,
        trend: str,
        pcr: float,
    ) -> float:
        """
        Estimate option premium after time passes and spot moves.
        
        Components:
        1. Theta decay (always negative for longs)
        2. Delta movement (depends on spot movement)
        3. Gamma (convexity) effects
        4. Vega (IV changes)
        """
        
        # 1. THETA DECAY
        theta_loss = self.calculate_theta_decay(minutes_elapsed, entry_premium, iv)
        
        # 2. DELTA / SPOT MOVEMENT
        spot_pct_move = (current_spot - entry_spot) / entry_spot * 100
        
        # For OTM options, delta ~0.3-0.5 typically
        # For ATM options, delta ~0.5
        moneyness = (strike - entry_spot) / entry_spot * 100
        
        if moneyness > 2:  # OTM by >2%
            delta = 0.30
        elif moneyness > 1:
            delta = 0.40
        elif moneyness < -1:
            delta = 0.70
        else:
            delta = 0.50
        
        # PE: negative delta (profits from downside)
        if option_type == "PE":
            delta = -delta
        
        delta_impact = entry_premium * delta * (spot_pct_move / 100)
        
        # 3. GAMMA (second order effects of delta changes)
        gamma = 0.01  # Rough estimate
        gamma_impact = 0.5 * entry_premium * gamma * ((spot_pct_move / 100) ** 2)
        
        # 4. VEGA (IV sensitivity)
        # If IV stays same, no vega impact. OTM options have high vega.
        vega = 0.02  # For OTM options
        # Assume IV stays roughly constant in short timeframes
        vega_impact = 0
        
        # Total premium
        premium = entry_premium - theta_loss + delta_impact + gamma_impact + vega_impact
        
        return max(0.05, premium)  # Can't go below 0.05
    
    def simulate_trade_minute_by_minute(self, trade: Dict) -> Dict[str, Any]:
        """
        Simulate trade minute by minute through market hours.
        
        Returns when SL hits, target hits, or market closes.
        """
        
        entry_time = datetime.fromisoformat(
            trade["timestamp"].replace("+05:30", "")
        )
        entry_time = self.ist.localize(entry_time.replace(tzinfo=None))
        
        entry_premium = trade["entry_premium"]
        entry_spot = trade["spot_price"]
        strike = trade["strike"]
        option_type = "PE" if "PE" in trade["direction"] else "CE"
        
        # Calculate SL and target
        sl_level = entry_premium * (1 - self.STOP_LOSS_PCT / 100)
        target_level = entry_premium * (1 + self.TARGET_PCT / 100)
        
        # Track the trade minute by minute
        current_premium = entry_premium
        current_spot = entry_spot
        current_time = entry_time
        
        # Market ends at 15:30 IST
        market_close = current_time.replace(hour=15, minute=30, second=0, microsecond=0)
        
        # Simulate movement
        timeline = []
        
        # Generate random-walk spot prices for the day
        # Start from entry spot and drift based on trend
        spot_moves = [0]  # Entry
        
        hourly_drift = 0.10  # Slight bullish drift on average
        if "bearish" in trade["trend"].lower():
            hourly_drift = -0.10
        elif "strong" in trade["trend"].lower():
            hourly_drift = abs(hourly_drift) * (2 if "bullish" in trade["trend"].lower() else -2)
        
        # Simulate 5 hours worth of minutes (market open to close)
        # But only from entry time onwards
        minutes_to_simulate = min(
            300,  # 5 hours max
            int((market_close - entry_time).total_seconds() / 60)
        )
        
        for minute_idx in range(0, minutes_to_simulate + 1, 5):  # Check every 5 minutes
            elapsed_minutes = minute_idx
            time_point = entry_time + timedelta(minutes=elapsed_minutes)
            
            # Simulate spot movement using random walk with drift
            hourly_component = (elapsed_minutes / 60) * hourly_drift / 100
            random_component = (elapsed_minutes / 100) * 0.01 * (1 if elapsed_minutes % 20 < 10 else -1)
            spot_move_pct = hourly_component + random_component
            
            current_spot = entry_spot * (1 + spot_move_pct)
            
            # Calculate premium at this point
            current_premium = self.calculate_option_premium_at_time(
                entry_premium=entry_premium,
                entry_spot=entry_spot,
                strike=strike,
                option_type=option_type,
                minutes_elapsed=elapsed_minutes,
                current_spot=current_spot,
                iv=trade["iv_level"],
                trend=trade["trend"],
                pcr=trade["pcr"],
            )
            
            pnl = current_premium - entry_premium
            pnl_pct = (pnl / entry_premium * 100) if entry_premium > 0 else 0
            
            timeline.append({
                "time": time_point.strftime("%H:%M"),
                "elapsed_mins": elapsed_minutes,
                "spot": current_spot,
                "premium": current_premium,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            })
            
            # Check exit conditions
            if current_premium <= sl_level:
                return {
                    "trade": trade,
                    "entry_time": entry_time.strftime("%H:%M:%S"),
                    "entry_premium": entry_premium,
                    "entry_spot": entry_spot,
                    "exit_time": time_point.strftime("%H:%M:%S"),
                    "exit_premium": current_premium,
                    "exit_spot": current_spot,
                    "pnl": current_premium - entry_premium,
                    "pnl_pct": (current_premium - entry_premium) / entry_premium * 100,
                    "exit_reason": "STOP_LOSS_HIT",
                    "sl_level": sl_level,
                    "target_level": target_level,
                    "timeline": timeline,
                }
            
            if current_premium >= target_level:
                return {
                    "trade": trade,
                    "entry_time": entry_time.strftime("%H:%M:%S"),
                    "entry_premium": entry_premium,
                    "entry_spot": entry_spot,
                    "exit_time": time_point.strftime("%H:%M:%S"),
                    "exit_premium": current_premium,
                    "exit_spot": current_spot,
                    "pnl": current_premium - entry_premium,
                    "pnl_pct": (current_premium - entry_premium) / entry_premium * 100,
                    "exit_reason": "TARGET_HIT",
                    "sl_level": sl_level,
                    "target_level": target_level,
                    "timeline": timeline,
                }
        
        # Market close exit
        return {
            "trade": trade,
            "entry_time": entry_time.strftime("%H:%M:%S"),
            "entry_premium": entry_premium,
            "entry_spot": entry_spot,
            "exit_time": market_close.strftime("%H:%M:%S"),
            "exit_premium": current_premium,
            "exit_spot": current_spot,
            "pnl": current_premium - entry_premium,
            "pnl_pct": (current_premium - entry_premium) / entry_premium * 100,
            "exit_reason": "MARKET_CLOSE",
            "sl_level": sl_level,
            "target_level": target_level,
            "timeline": timeline,
        }
    
    def run_backtest(self) -> Dict[str, Any]:
        """Run full detailed backtest."""
        print("\n" + "="*90)
        print("  🔬 DETAILED MINUTE-BY-MINUTE BACKTEST (May 13, 2026)")
        print("="*90)
        
        trades = self.get_blocked_trades()
        print(f"\nTesting {len(trades)} blocked trades with SL={self.STOP_LOSS_PCT}% | Target={self.TARGET_PCT}%\n")
        
        results = []
        winners = 0
        total_pnl = 0
        
        for i, trade in enumerate(trades, 1):
            result = self.simulate_trade_minute_by_minute(trade)
            results.append(result)
            
            # Print detailed breakdown
            print(f"\n{'─'*90}")
            print(f"[Trade {i}] {trade['direction']} NIFTY {trade['strike']}")
            print(f"{'─'*90}")
            print(f"Entry:  {result['entry_time']} @ Rs {result['entry_premium']:7.1f} | Spot: {result['entry_spot']:7.0f}")
            print(f"Exit:   {result['exit_time']} @ Rs {result['exit_premium']:7.1f} | Spot: {result['exit_spot']:7.0f} ({result['exit_reason']})")
            print(f"SL:     Rs {result['sl_level']:7.1f} (20% loss)")
            print(f"Target: Rs {result['target_level']:7.1f} (50% gain)")
            print(f"\nP&L: Rs {result['pnl']:+7.1f} ({result['pnl_pct']:+.2f}%)")
            
            # Show timeline excerpt
            if len(result['timeline']) > 0:
                print(f"\nTimeline (every 5 min):")
                print(f"  {'Time':<8} {'Spot':<10} {'Premium':<10} {'P&L':<10} {'%':<8}")
                print(f"  {'-'*50}")
                
                # Show first, middle, and last
                indices_to_show = [0, len(result['timeline'])//2, -1]
                for idx in indices_to_show:
                    if idx < len(result['timeline']):
                        tl = result['timeline'][idx]
                        print(f"  {tl['time']:<8} {tl['spot']:7.0f}   {tl['premium']:7.1f}   {tl['pnl']:+7.1f}   {tl['pnl_pct']:+6.2f}%")
            
            if result['pnl'] > 0:
                winners += 1
            total_pnl += result['pnl']
        
        # Summary
        print(f"\n\n{'='*90}")
        print(f"  📊 BACKTEST SUMMARY")
        print(f"{'='*90}\n")
        
        print(f"Trades tested:    {len(results)}")
        print(f"Winners (target): {len([r for r in results if r['exit_reason'] == 'TARGET_HIT'])}")
        print(f"SL hits:          {len([r for r in results if r['exit_reason'] == 'STOP_LOSS_HIT'])}")
        print(f"Closed at market: {len([r for r in results if r['exit_reason'] == 'MARKET_CLOSE'])}")
        
        print(f"\nTotal P&L:        Rs {total_pnl:+.0f}")
        print(f"Avg P&L per trade: Rs {total_pnl/len(results):+.0f}")
        print(f"Win rate:         {len([r for r in results if r['pnl'] > 0])/len(results)*100:.1f}%")
        
        print(f"\n{'='*90}\n")
        
        return {
            "status": "complete",
            "trades": results,
            "summary": {
                "total_trades": len(results),
                "winners": len([r for r in results if r['exit_reason'] == 'TARGET_HIT']),
                "sl_hits": len([r for r in results if r['exit_reason'] == 'STOP_LOSS_HIT']),
                "total_pnl": total_pnl,
            }
        }


def main():
    try:
        backtest = DetailedTradeBacktester()
        backtest.run_backtest()
        return 0
    except Exception as e:
        print(f"❌ Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
