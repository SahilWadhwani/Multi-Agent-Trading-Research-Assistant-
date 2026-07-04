"""
Decision Log - Persistent Memory for Trading Decisions.

Tracks every decision the agent makes for:
1. Learning from past trades
2. Pattern recognition
3. Strategy improvement
4. Accountability and debugging
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
import pytz

_IST = pytz.timezone("Asia/Kolkata")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class DecisionType(Enum):
    TRADE_ENTRY = "trade_entry"
    TRADE_EXIT = "trade_exit"
    TRADE_SKIP = "trade_skip"
    TRIGGER_SET = "trigger_set"
    STOP_LOSS_HIT = "stop_loss_hit"
    TARGET_HIT = "target_hit"
    MANUAL_EXIT = "manual_exit"


class DecisionOutcome(Enum):
    PROFITABLE = "profitable"
    LOSS = "loss"
    BREAKEVEN = "breakeven"
    PENDING = "pending"
    CANCELLED = "cancelled"


@dataclass
class TradingDecision:
    """A single trading decision."""
    decision_id: str
    timestamp: datetime
    decision_type: DecisionType
    symbol: str
    
    # What was decided
    action: str  # BUY_CE, BUY_PE, SELL, HOLD, SKIP
    strike: Optional[float] = None
    option_type: Optional[str] = None  # CE or PE
    lots: int = 0
    entry_price: Optional[float] = None
    
    # Why (LLM reasoning)
    reasoning: str = ""
    confidence: float = 0.5
    
    # Market context at decision time
    spot_price: float = 0
    iv_level: Optional[float] = None
    pcr: Optional[float] = None
    trend: Optional[str] = None  # BULLISH, BEARISH, SIDEWAYS
    vix: Optional[float] = None
    
    # Signals that led to decision
    technical_signal: Optional[str] = None
    sentiment_signal: Optional[str] = None
    news_signal: Optional[str] = None
    fo_signal: Optional[str] = None
    
    # Outcome (filled after trade closes)
    outcome: DecisionOutcome = DecisionOutcome.PENDING
    exit_price: Optional[float] = None
    pnl: float = 0
    exit_reason: Optional[str] = None
    
    # Strategy used
    strategy_name: Optional[str] = None
    
    # Model that made decision
    model_used: str = "unknown"


class DecisionLog:
    """
    Persistent decision log with SQLite storage.
    
    Features:
    - Logs every decision with full context
    - Tracks outcomes and P&L
    - Enables pattern analysis
    - Supports reflection queries
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data_cache",
            "decisions.db"
        )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Initialize decision log database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY,
                timestamp TEXT,
                decision_type TEXT,
                symbol TEXT,
                action TEXT,
                strike REAL,
                option_type TEXT,
                lots INTEGER,
                entry_price REAL,
                reasoning TEXT,
                confidence REAL,
                spot_price REAL,
                iv_level REAL,
                pcr REAL,
                trend TEXT,
                vix REAL,
                technical_signal TEXT,
                sentiment_signal TEXT,
                news_signal TEXT,
                fo_signal TEXT,
                outcome TEXT,
                exit_price REAL,
                pnl REAL,
                exit_reason TEXT,
                strategy_name TEXT,
                model_used TEXT
            )
        """)
        
        # Index for fast queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON decisions(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON decisions(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_outcome ON decisions(outcome)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_strategy ON decisions(strategy_name)")
        
        conn.commit()
        conn.close()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        return sqlite3.connect(self.db_path)
    
    def log_decision(self, decision: TradingDecision) -> str:
        """
        Log a trading decision.
        
        Args:
            decision: TradingDecision object
        
        Returns:
            Decision ID
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO decisions (
                decision_id, timestamp, decision_type, symbol, action,
                strike, option_type, lots, entry_price, reasoning,
                confidence, spot_price, iv_level, pcr, trend, vix,
                technical_signal, sentiment_signal, news_signal, fo_signal,
                outcome, exit_price, pnl, exit_reason, strategy_name, model_used
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            decision.decision_id,
            decision.timestamp.isoformat(),
            decision.decision_type.value,
            decision.symbol,
            decision.action,
            decision.strike,
            decision.option_type,
            decision.lots,
            decision.entry_price,
            decision.reasoning,
            decision.confidence,
            decision.spot_price,
            decision.iv_level,
            decision.pcr,
            decision.trend,
            decision.vix,
            decision.technical_signal,
            decision.sentiment_signal,
            decision.news_signal,
            decision.fo_signal,
            decision.outcome.value,
            decision.exit_price,
            decision.pnl,
            decision.exit_reason,
            decision.strategy_name,
            decision.model_used,
        ))
        
        conn.commit()
        conn.close()
        
        return decision.decision_id
    
    def patch_entry_fill(
        self,
        decision_id: str,
        entry_price: float,
        lots: Optional[int] = None,
    ) -> None:
        """Update entry price (and optionally lots) after broker fill confirmation."""
        conn = self._get_conn()
        cursor = conn.cursor()
        if lots is not None:
            cursor.execute(
                """
                UPDATE decisions SET entry_price = ?, lots = ?
                WHERE decision_id = ?
                """,
                (entry_price, lots, decision_id),
            )
        else:
            cursor.execute(
                "UPDATE decisions SET entry_price = ? WHERE decision_id = ?",
                (entry_price, decision_id),
            )
        conn.commit()
        conn.close()

    def update_outcome(
        self,
        decision_id: str,
        outcome: DecisionOutcome,
        exit_price: float = None,
        pnl: float = 0,
        exit_reason: str = None,
    ):
        """Update the outcome of a decision after trade closes."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE decisions
            SET outcome = ?, exit_price = ?, pnl = ?, exit_reason = ?
            WHERE decision_id = ?
        """, (outcome.value, exit_price, pnl, exit_reason, decision_id))
        
        conn.commit()
        conn.close()
    
    def get_recent_decisions(
        self,
        limit: int = 20,
        symbol: str = None,
        decision_type: DecisionType = None,
    ) -> List[TradingDecision]:
        """Get recent decisions."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        query = "SELECT * FROM decisions"
        params = []
        conditions = []
        
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol.upper())
        
        if decision_type:
            conditions.append("decision_type = ?")
            params.append(decision_type.value)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_decision(row) for row in rows]

    def get_decision(self, decision_id: str) -> Optional[TradingDecision]:
        """Fetch one decision by id."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM decisions WHERE decision_id = ?", (decision_id,))
        row = cursor.fetchone()
        conn.close()
        return self._row_to_decision(row) if row else None
    
    def _row_to_decision(self, row) -> TradingDecision:
        """Convert database row to TradingDecision."""
        return TradingDecision(
            decision_id=row[0],
            timestamp=datetime.fromisoformat(row[1]),
            decision_type=DecisionType(row[2]),
            symbol=row[3],
            action=row[4],
            strike=row[5],
            option_type=row[6],
            lots=row[7],
            entry_price=row[8],
            reasoning=row[9],
            confidence=row[10],
            spot_price=row[11],
            iv_level=row[12],
            pcr=row[13],
            trend=row[14],
            vix=row[15],
            technical_signal=row[16],
            sentiment_signal=row[17],
            news_signal=row[18],
            fo_signal=row[19],
            outcome=DecisionOutcome(row[20]) if row[20] else DecisionOutcome.PENDING,
            exit_price=row[21],
            pnl=row[22],
            exit_reason=row[23],
            strategy_name=row[24],
            model_used=row[25],
        )
    
    def get_performance_stats(
        self,
        days: int = 30,
        symbol: str = None,
        strategy: str = None,
    ) -> Dict[str, Any]:
        """Get performance statistics."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        since = (datetime.now(_IST) - timedelta(days=days)).isoformat()
        
        query = """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'profitable' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome = 'loss' THEN 1 ELSE 0 END) as losses,
                SUM(pnl) as total_pnl,
                AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
                AVG(CASE WHEN pnl < 0 THEN pnl END) as avg_loss,
                MAX(pnl) as max_win,
                MIN(pnl) as max_loss,
                AVG(confidence) as avg_confidence
            FROM decisions
            WHERE timestamp > ?
            AND decision_type = 'trade_entry'
            AND outcome != 'pending'
        """
        params = [since]
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol.upper())
        
        if strategy:
            query += " AND strategy_name = ?"
            params.append(strategy)
        
        cursor.execute(query, params)
        row = cursor.fetchone()
        conn.close()
        
        if not row or row[0] == 0:
            return {"total": 0, "win_rate": 0, "total_pnl": 0}
        
        total = row[0] or 0
        wins = row[1] or 0
        
        return {
            "total_trades": total,
            "winning_trades": wins,
            "losing_trades": row[2] or 0,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(row[3] or 0, 2),
            "avg_win": round(row[4] or 0, 2),
            "avg_loss": round(row[5] or 0, 2),
            "max_win": round(row[6] or 0, 2),
            "max_loss": round(row[7] or 0, 2),
            "avg_confidence": round(row[8] or 0, 2),
        }
    
    def get_strategy_performance(self, days: int = 30) -> Dict[str, Dict]:
        """Get performance breakdown by strategy."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        since = (datetime.now(_IST) - timedelta(days=days)).isoformat()
        
        cursor.execute("""
            SELECT 
                strategy_name,
                COUNT(*) as total,
                SUM(CASE WHEN outcome = 'profitable' THEN 1 ELSE 0 END) as wins,
                SUM(pnl) as total_pnl
            FROM decisions
            WHERE timestamp > ?
            AND decision_type = 'trade_entry'
            AND outcome != 'pending'
            AND strategy_name IS NOT NULL
            GROUP BY strategy_name
        """, (since,))
        
        rows = cursor.fetchall()
        conn.close()
        
        result = {}
        for row in rows:
            strategy = row[0] or "unknown"
            total = row[1] or 0
            wins = row[2] or 0
            
            result[strategy] = {
                "total": total,
                "wins": wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(row[3] or 0, 2),
            }
        
        return result
    
    def get_similar_situations(
        self,
        symbol: str,
        trend: str,
        iv_level: float,
        limit: int = 5,
    ) -> List[TradingDecision]:
        """
        Find similar past situations for learning.
        
        Useful for the agent to learn from past experience.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # Find decisions in similar conditions
        iv_low = iv_level * 0.8
        iv_high = iv_level * 1.2
        
        cursor.execute("""
            SELECT * FROM decisions
            WHERE symbol = ?
            AND trend = ?
            AND iv_level BETWEEN ? AND ?
            AND outcome != 'pending'
            ORDER BY timestamp DESC
            LIMIT ?
        """, (symbol.upper(), trend, iv_low, iv_high, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_decision(row) for row in rows]
    
    def generate_decision_id(self) -> str:
        """Generate unique decision ID."""
        return f"DEC_{datetime.now(_IST).strftime('%Y%m%d_%H%M%S_%f')}"


# Singleton
_decision_log = None

def get_decision_log() -> DecisionLog:
    """Get or create decision log singleton."""
    global _decision_log
    if _decision_log is None:
        _decision_log = DecisionLog()
    return _decision_log
