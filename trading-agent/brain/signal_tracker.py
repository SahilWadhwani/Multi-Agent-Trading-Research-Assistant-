"""
SIGNAL TRACKER

Tracks all market scans to ensure the agent isn't just sitting idle.
Logs every scan with:
- What was seen (trend, IV, PCR)
- Why it passed or failed
- Helps identify if thresholds are too strict

Also provides activity reports.
"""

import os
import sys
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class ScanRecord:
    """Record of a single market scan."""
    scan_id: str
    timestamp: datetime
    symbol: str
    
    # Market data seen
    spot_price: float
    trend: str
    trend_strength: float
    iv_level: float
    iv_regime: str
    pcr: float
    news_sentiment: str
    signal_strength: float
    
    # Decision
    should_trade: bool
    rejection_reason: Optional[str]
    
    # If signal was generated
    signal_direction: Optional[str] = None
    signal_strike: Optional[float] = None
    signal_premium: Optional[float] = None
    
    # Final outcome
    final_decision: str = "NO_TRADE"  # NO_TRADE, BLOCKED, EXECUTE
    blocked_by_gate: Optional[str] = None
    
    # Data quality
    used_fallback_data: bool = False


class SignalTracker:
    """
    Tracks all scans and provides activity reports.
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data_cache",
            "signal_tracker.db"
        )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scans (
                scan_id TEXT PRIMARY KEY,
                timestamp TEXT,
                symbol TEXT,
                spot_price REAL,
                trend TEXT,
                trend_strength REAL,
                iv_level REAL,
                iv_regime TEXT,
                pcr REAL,
                news_sentiment TEXT,
                signal_strength REAL,
                should_trade INTEGER,
                rejection_reason TEXT,
                signal_direction TEXT,
                signal_strike REAL,
                signal_premium REAL,
                final_decision TEXT,
                blocked_by_gate TEXT,
                used_fallback_data INTEGER
            )
        """)
        conn.commit()
        conn.close()
    
    def log_scan(self, record: ScanRecord):
        """Log a scan record."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO scans VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            record.scan_id,
            record.timestamp.isoformat(),
            record.symbol,
            record.spot_price,
            record.trend,
            record.trend_strength,
            record.iv_level,
            record.iv_regime,
            record.pcr,
            record.news_sentiment,
            record.signal_strength,
            1 if record.should_trade else 0,
            record.rejection_reason,
            record.signal_direction,
            record.signal_strike,
            record.signal_premium,
            record.final_decision,
            record.blocked_by_gate,
            1 if record.used_fallback_data else 0,
        ))
        conn.commit()
        conn.close()
    
    def get_activity_report(self, days: int = 7) -> Dict[str, Any]:
        """Get activity report for the last N days."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Total scans
        cursor.execute("SELECT COUNT(*) FROM scans WHERE timestamp > ?", (cutoff,))
        total_scans = cursor.fetchone()[0]
        
        # By decision
        cursor.execute("""
            SELECT final_decision, COUNT(*) as count 
            FROM scans WHERE timestamp > ? 
            GROUP BY final_decision
        """, (cutoff,))
        by_decision = {row["final_decision"]: row["count"] for row in cursor.fetchall()}
        
        # By rejection reason
        cursor.execute("""
            SELECT rejection_reason, COUNT(*) as count 
            FROM scans WHERE timestamp > ? AND should_trade = 0
            GROUP BY rejection_reason
            ORDER BY count DESC
            LIMIT 10
        """, (cutoff,))
        by_rejection = {row["rejection_reason"]: row["count"] for row in cursor.fetchall()}
        
        # By blocked gate
        cursor.execute("""
            SELECT blocked_by_gate, COUNT(*) as count 
            FROM scans WHERE timestamp > ? AND final_decision = 'BLOCKED'
            GROUP BY blocked_by_gate
            ORDER BY count DESC
        """, (cutoff,))
        by_gate = {row["blocked_by_gate"]: row["count"] for row in cursor.fetchall()}
        
        # Fallback data usage
        cursor.execute("""
            SELECT COUNT(*) FROM scans 
            WHERE timestamp > ? AND used_fallback_data = 1
        """, (cutoff,))
        fallback_count = cursor.fetchone()[0]
        
        # By symbol
        cursor.execute("""
            SELECT symbol, COUNT(*) as scans,
                   SUM(CASE WHEN final_decision = 'EXECUTE' THEN 1 ELSE 0 END) as executes
            FROM scans WHERE timestamp > ?
            GROUP BY symbol
        """, (cutoff,))
        by_symbol = {row["symbol"]: {"scans": row["scans"], "executes": row["executes"]} 
                     for row in cursor.fetchall()}
        
        # Daily breakdown
        cursor.execute("""
            SELECT DATE(timestamp) as day, COUNT(*) as scans,
                   SUM(CASE WHEN final_decision = 'EXECUTE' THEN 1 ELSE 0 END) as executes
            FROM scans WHERE timestamp > ?
            GROUP BY DATE(timestamp)
            ORDER BY day DESC
        """, (cutoff,))
        daily = [{"day": row["day"], "scans": row["scans"], "executes": row["executes"]} 
                 for row in cursor.fetchall()]
        
        conn.close()
        
        # Calculate rates
        signal_rate = by_decision.get("EXECUTE", 0) / total_scans * 100 if total_scans > 0 else 0
        
        return {
            "period_days": days,
            "total_scans": total_scans,
            "by_decision": by_decision,
            "signal_rate": f"{signal_rate:.1f}%",
            "top_rejection_reasons": by_rejection,
            "blocked_by_gates": by_gate,
            "fallback_data_used": fallback_count,
            "fallback_rate": f"{fallback_count/total_scans*100:.1f}%" if total_scans > 0 else "0%",
            "by_symbol": by_symbol,
            "daily": daily,
        }
    
    def get_last_trade_date(self) -> Optional[datetime]:
        """Get the date of the last executed trade."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp FROM scans 
            WHERE final_decision = 'EXECUTE'
            ORDER BY timestamp DESC LIMIT 1
        """)
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return datetime.fromisoformat(row[0])
        return None
    
    def days_since_last_trade(self) -> int:
        """Get days since last trade (or -1 if never traded)."""
        last = self.get_last_trade_date()
        if not last:
            return -1
        return (datetime.utcnow() - last).days
    
    def print_report(self, days: int = 7):
        """Print a human-readable activity report."""
        report = self.get_activity_report(days)
        
        print(f"\n{'='*60}")
        print(f"SIGNAL TRACKER - ACTIVITY REPORT ({days} days)")
        print(f"{'='*60}")
        
        print(f"\nTotal Scans: {report['total_scans']}")
        print(f"Signal Rate: {report['signal_rate']} (scans that resulted in EXECUTE)")
        print(f"Fallback Data Used: {report['fallback_rate']}")
        
        print(f"\nDecision Breakdown:")
        for decision, count in report['by_decision'].items():
            print(f"  {decision}: {count}")
        
        if report['top_rejection_reasons']:
            print(f"\nTop Rejection Reasons:")
            for reason, count in list(report['top_rejection_reasons'].items())[:5]:
                print(f"  {reason}: {count}")
        
        if report['blocked_by_gates']:
            print(f"\nBlocked by Risk Gates:")
            for gate, count in report['blocked_by_gates'].items():
                print(f"  {gate}: {count}")
        
        if report['by_symbol']:
            print(f"\nBy Symbol:")
            for symbol, data in report['by_symbol'].items():
                print(f"  {symbol}: {data['scans']} scans, {data['executes']} trades")
        
        if report['daily']:
            print(f"\nDaily Activity:")
            for day in report['daily'][:7]:
                print(f"  {day['day']}: {day['scans']} scans, {day['executes']} trades")
        
        days_since = self.days_since_last_trade()
        if days_since == -1:
            print(f"\n⚠️  NO TRADES YET")
        elif days_since > 3:
            print(f"\n⚠️  {days_since} days since last trade - check thresholds!")
        else:
            print(f"\n✓ Last trade: {days_since} days ago")
        
        print(f"{'='*60}\n")


# Singleton
_tracker = None

def get_signal_tracker() -> SignalTracker:
    """Get or create signal tracker singleton."""
    global _tracker
    if _tracker is None:
        _tracker = SignalTracker()
    return _tracker


def generate_scan_id() -> str:
    """Generate unique scan ID."""
    return f"SCAN_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"


if __name__ == "__main__":
    tracker = get_signal_tracker()
    tracker.print_report(7)
