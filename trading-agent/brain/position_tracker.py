"""
POSITION TRACKER

Monitors open paper trades and calculates real-time P&L.
Checks if trades hit stop-loss or target.
"""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.upstox_client import get_upstox_client
from memory.decision_log import get_decision_log, DecisionOutcome
from brain.smart_exit import should_exit


def estimate_options_round_trip_cost(entry_price: float, exit_price: float, qty: int) -> float:
    """
    Conservative approximate Indian index-options round-trip costs.

    This is not a broker contract note replacement; it prevents the learning
    loop from treating gross P&L as spendable edge.
    """
    qty = max(0, int(qty or 0))
    buy_value = max(0.0, float(entry_price or 0.0)) * qty
    sell_value = max(0.0, float(exit_price or 0.0)) * qty
    turnover = buy_value + sell_value
    if turnover <= 0:
        return 0.0

    brokerage = min(20.0, buy_value * 0.0005) + min(20.0, sell_value * 0.0005)
    exchange_txn = turnover * 0.00053
    sebi = turnover * 0.000001
    stamp = buy_value * 0.00003
    stt = sell_value * 0.001
    gst = 0.18 * (brokerage + exchange_txn + sebi)
    return round(brokerage + exchange_txn + sebi + stamp + stt + gst, 2)


@dataclass
class OpenPosition:
    """An open paper position."""
    decision_id: str
    symbol: str
    strike: float
    option_type: str  # CE or PE
    entry_price: float
    entry_time: datetime
    lots: int
    lot_size: int
    instrument_key: Optional[str] = None
    
    # Smart exit tracking (no fixed targets!)
    highest_pnl_pct: float = 0  # Track peak for trailing
    
    # Current state
    current_price: float = 0
    current_pnl_pct: float = 0
    current_pnl_rs: float = 0
    last_updated: Optional[datetime] = None
    
    # Outcome
    status: str = "OPEN"  # OPEN, PROFIT, LOSS, EXPIRED, CLOSED
    exit_price: float = 0
    exit_time: Optional[datetime] = None
    exit_reason: str = ""

    # Broker-side protective GTT order IDs (set after BUY fill in live mode)
    gtt_sl_order_id: Optional[str] = None
    gtt_target_order_id: Optional[str] = None

    # Execution risk parameters used for recovery / GTT re-placement
    stop_loss_pct: float = 25.0
    target_pct: float = 50.0


class PositionTracker:
    """
    Tracks open paper positions and monitors P&L.
    """
    
    LOT_SIZES = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 40}
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data_cache",
            "positions.db"
        )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        self.client = get_upstox_client()
        self.decision_log = get_decision_log()
    
    def _init_db(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions_v2 (
                decision_id TEXT PRIMARY KEY,
                symbol TEXT,
                strike REAL,
                option_type TEXT,
                entry_price REAL,
                entry_time TEXT,
                lots INTEGER,
                lot_size INTEGER,
                instrument_key TEXT,
                highest_pnl_pct REAL DEFAULT 0,
                current_price REAL,
                current_pnl_pct REAL,
                current_pnl_rs REAL,
                last_updated TEXT,
                status TEXT,
                exit_price REAL,
                exit_time TEXT,
                exit_reason TEXT
            )
        """)
        conn.commit()
        try:
            cur = conn.execute("PRAGMA table_info(positions_v2)")
            cols = {row[1] for row in cur.fetchall()}
            for col in (
                "instrument_key",
                "gtt_sl_order_id",
                "gtt_target_order_id",
                "stop_loss_pct",
                "target_pct",
            ):
                if col not in cols:
                    col_type = "REAL" if col in ("stop_loss_pct", "target_pct") else "TEXT"
                    conn.execute(f"ALTER TABLE positions_v2 ADD COLUMN {col} {col_type}")
                    conn.commit()
        except sqlite3.OperationalError:
            pass
        conn.close()
    
    def add_position(self, position: OpenPosition):
        """Add a new open position and subscribe to real-time price feed."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO positions_v2 (
                decision_id, symbol, strike, option_type, entry_price,
                entry_time, lots, lot_size, instrument_key, highest_pnl_pct,
                current_price, current_pnl_pct, current_pnl_rs, last_updated,
                status, exit_price, exit_time, exit_reason,
                gtt_sl_order_id, gtt_target_order_id, stop_loss_pct, target_pct
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            position.decision_id,
            position.symbol,
            position.strike,
            position.option_type,
            position.entry_price,
            position.entry_time.isoformat(),
            position.lots,
            position.lot_size,
            position.instrument_key,
            position.highest_pnl_pct,
            position.current_price,
            position.current_pnl_pct,
            position.current_pnl_rs,
            position.last_updated.isoformat() if position.last_updated else None,
            position.status,
            position.exit_price,
            position.exit_time.isoformat() if position.exit_time else None,
            position.exit_reason,
            position.gtt_sl_order_id,
            position.gtt_target_order_id,
            position.stop_loss_pct,
            position.target_pct,
        ))
        conn.commit()
        conn.close()

        # Log BUY trade to main trades table for historical learning
        try:
            from database.schema import get_session, Trade
            from execution import runtime_safety
            mode = runtime_safety.load_trading_mode()
            is_paper = mode in (runtime_safety.TradingMode.PAPER, runtime_safety.TradingMode.SHADOW)
            session = get_session()
            session.add(Trade(
                symbol=f"{position.symbol} {int(position.strike)} {position.option_type}",
                exchange="NSE_FO",
                quantity=int(position.lots * position.lot_size),
                side="BUY",
                price=position.entry_price,
                order_type="MARKET",
                product_type="INTRADAY",
                order_id=position.decision_id,
                status="EXECUTED",
                is_paper_trade=is_paper,
                notes=f"instrument_key={position.instrument_key or ''}",
            ))
            session.commit()
            session.close()
        except Exception:
            pass

        # Subscribe to real-time price feed for this instrument
        if position.instrument_key:
            try:
                from execution.websocket_feed import get_price_feed_manager
                mgr = get_price_feed_manager()
                if mgr:
                    mgr.subscribe([position.instrument_key])
            except Exception:
                pass
    
    def get_open_positions(self) -> List[OpenPosition]:
        """Get all open positions."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM positions_v2 WHERE status = 'OPEN'")
        rows = cursor.fetchall()
        conn.close()
        
        positions = []
        for row in rows:
            keys = row.keys()
            ik = row["instrument_key"] if "instrument_key" in keys else None
            gtt_sl = row["gtt_sl_order_id"] if "gtt_sl_order_id" in keys else None
            gtt_tgt = row["gtt_target_order_id"] if "gtt_target_order_id" in keys else None
            sl_pct = row["stop_loss_pct"] if "stop_loss_pct" in keys else None
            target_pct = row["target_pct"] if "target_pct" in keys else None
            positions.append(OpenPosition(
                decision_id=row["decision_id"],
                symbol=row["symbol"],
                strike=row["strike"],
                option_type=row["option_type"],
                entry_price=row["entry_price"],
                entry_time=datetime.fromisoformat(row["entry_time"]),
                lots=row["lots"],
                lot_size=row["lot_size"],
                instrument_key=ik,
                highest_pnl_pct=row["highest_pnl_pct"] or 0,
                current_price=row["current_price"] or 0,
                current_pnl_pct=row["current_pnl_pct"] or 0,
                current_pnl_rs=row["current_pnl_rs"] or 0,
                last_updated=datetime.fromisoformat(row["last_updated"]) if row["last_updated"] else None,
                status=row["status"],
                gtt_sl_order_id=gtt_sl,
                gtt_target_order_id=gtt_tgt,
                stop_loss_pct=float(sl_pct or 25.0),
                target_pct=float(target_pct or 50.0),
            ))
        
        return positions
    
    def sync_from_decision_log(self):
        """
        Sync open positions from decision log.

        In paper mode: creates positions for PENDING decisions (immediate fill assumed).
        In live mode: positions are created ONLY by lean_fo_executor after
        confirmed broker fill via add_position(), so this is a no-op safety net
        that only picks up decisions tagged with strategy_name='FILL_CONFIRMED'.
        """
        from execution import runtime_safety
        mode = runtime_safety.load_trading_mode()
        is_live = mode in (runtime_safety.TradingMode.MICRO_LIVE, runtime_safety.TradingMode.LIVE)

        decisions = self.decision_log.get_recent_decisions(limit=50)
        
        for d in decisions:
            if d.outcome != DecisionOutcome.PENDING or not d.entry_price:
                continue

            # In live mode, only sync decisions that have been confirmed filled
            if is_live and d.strategy_name != "FILL_CONFIRMED":
                continue

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT decision_id FROM positions_v2 WHERE decision_id = ?", (d.decision_id,))
            exists = cursor.fetchone()
            conn.close()
            
            if not exists:
                lot_size = self.LOT_SIZES.get(d.symbol, 50)
                ik = None
                if d.fo_signal and isinstance(d.fo_signal, str) and "|" in d.fo_signal:
                    ik = d.fo_signal
                position = OpenPosition(
                    decision_id=d.decision_id,
                    symbol=d.symbol,
                    strike=d.strike,
                    option_type=d.option_type,
                    entry_price=d.entry_price,
                    entry_time=d.timestamp,
                    lots=d.lots or 1,
                    lot_size=lot_size,
                    instrument_key=ik,
                    highest_pnl_pct=0,
                    stop_loss_pct=25.0,
                    target_pct=50.0,
                )
                self.add_position(position)
                print(f"   Added position: {d.symbol} {d.strike} {d.option_type}")

    def has_position(self, decision_id: str) -> bool:
        """Return True if a local position row exists for this decision."""
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM positions_v2 WHERE decision_id = ? LIMIT 1",
                (decision_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    
    def estimate_current_price(self, position: OpenPosition) -> float:
        """
        Get current price using PriceHub (instant) with REST fallback.

        Priority:
        1. PriceHub cache (fed by WebSocket, sub-second)
        2. REST API direct quote (if PriceHub stale or empty)
        3. Delta approximation from index spot
        4. Entry price (last resort)
        """
        # --- Priority 1: PriceHub (instant, no network call) ---
        if position.instrument_key:
            try:
                from execution.price_hub import get_price_hub
                hub = get_price_hub()
                ltp = hub.get_ltp(position.instrument_key)
                if ltp is not None and ltp > 0 and not hub.is_stale(position.instrument_key, max_age_s=60.0):
                    return ltp
            except Exception:
                pass

        # --- Priority 2: REST API direct quote ---
        if position.instrument_key:
            try:
                q = self.client.get_full_market_quote(position.instrument_key)
                if q.get("status") == "success" and q.get("data"):
                    data = q.get("data", {})
                    key = next(iter(data.keys()), None)
                    if key:
                        lp = data[key].get("last_price") or data[key].get("ltp")
                        if lp:
                            return float(lp)
            except Exception as e:
                print(f"   Broker quote error: {e}")

        # --- Priority 3: Delta approximation from spot ---
        try:
            index_key = 'NSE_INDEX|Nifty 50' if position.symbol == 'NIFTY' else 'NSE_INDEX|Nifty Bank'

            current_spot = None
            try:
                from execution.price_hub import get_price_hub
                hub = get_price_hub()
                current_spot = hub.get_ltp(index_key)
            except Exception:
                pass

            if not current_spot:
                spot_result = self.client.get_full_market_quote(index_key)
                if spot_result.get('status') == 'success':
                    data = spot_result.get('data', {})
                    key = list(data.keys())[0] if data else None
                    if key:
                        current_spot = data[key].get('last_price', 0)

            if not current_spot:
                return position.entry_price

            if position.option_type == 'PE':
                moneyness = position.strike - current_spot
                delta = -0.45 if moneyness > 0 else -0.35
            else:
                moneyness = current_spot - position.strike
                delta = 0.45 if moneyness > 0 else 0.35

            entry_spot_est = position.strike
            spot_move = current_spot - entry_spot_est
            premium_change = abs(delta) * spot_move

            if position.option_type == 'PE':
                premium_change = -premium_change

            estimated_price = position.entry_price + premium_change
            return max(estimated_price, position.entry_price * 0.1)
            
        except Exception as e:
            print(f"   Price estimation error: {e}")
            return position.entry_price
    
    def refresh_open_metrics_only(self) -> None:
        """Update live marks for OPEN rows only (no exits)."""
        positions = self.get_open_positions()
        ist = pytz.timezone("Asia/Kolkata")
        now = datetime.now(ist)
        for pos in positions:
            current_price = self.estimate_current_price(pos)
            pnl_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
            pnl_rs = (current_price - pos.entry_price) * pos.lot_size * pos.lots
            highest_pnl = max(pos.highest_pnl_pct or 0, pnl_pct)
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                UPDATE positions_v2 SET
                    current_price = ?,
                    current_pnl_pct = ?,
                    current_pnl_rs = ?,
                    highest_pnl_pct = ?,
                    last_updated = ?
                WHERE decision_id = ? AND status = 'OPEN'
                """,
                (
                    current_price,
                    pnl_pct,
                    pnl_rs,
                    highest_pnl,
                    now.isoformat(),
                    pos.decision_id,
                ),
            )
            conn.commit()
            conn.close()

    def mark_exiting(self, decision_id: str) -> bool:
        """
        Atomically mark a position as EXITING to prevent concurrent exit attempts.
        Returns True if this caller got the lock (was OPEN), False otherwise.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "UPDATE positions_v2 SET status = 'EXITING' WHERE decision_id = ? AND status = 'OPEN'",
            (decision_id,),
        )
        changed = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    def revert_exiting(self, decision_id: str) -> None:
        """Revert EXITING back to OPEN (e.g. if broker SELL failed)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE positions_v2 SET status = 'OPEN' WHERE decision_id = ? AND status = 'EXITING'",
            (decision_id,),
        )
        conn.commit()
        conn.close()

    def close_position_record(
        self,
        pos: OpenPosition,
        exit_price: float,
        exit_reason: str,
        *,
        ist_now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Close one position in SQLite + decision log (paper or after broker fill)."""
        ist = pytz.timezone("Asia/Kolkata")
        now = ist_now or datetime.now(ist)
        qty = int(pos.lot_size) * int(pos.lots)
        gross_pnl_rs = (exit_price - pos.entry_price) * qty
        costs_rs = estimate_options_round_trip_cost(pos.entry_price, exit_price, qty)
        pnl_rs = gross_pnl_rs - costs_rs
        pnl_pct = (pnl_rs / (pos.entry_price * qty)) * 100 if pos.entry_price > 0 and qty > 0 else 0
        status = "PROFIT" if pnl_rs > 0 else "LOSS"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            UPDATE positions_v2 SET
                current_price = ?,
                current_pnl_pct = ?,
                current_pnl_rs = ?,
                last_updated = ?,
                status = ?,
                exit_price = ?,
                exit_time = ?,
                exit_reason = ?
            WHERE decision_id = ? AND status IN ('OPEN', 'EXITING')
            """,
            (
                exit_price,
                pnl_pct,
                pnl_rs,
                now.isoformat(),
                status,
                exit_price,
                now.isoformat(),
                exit_reason,
                pos.decision_id,
            ),
        )
        conn.commit()
        conn.close()
        outcome = DecisionOutcome.PROFITABLE if pnl_pct > 0 else DecisionOutcome.LOSS
        self.decision_log.update_outcome(
            pos.decision_id,
            outcome=outcome,
            exit_price=exit_price,
            pnl=pnl_rs,
            exit_reason=exit_reason,
        )

        # Log SELL trade to main trades table for historical learning
        try:
            from database.schema import get_session, Trade
            from execution import runtime_safety
            mode = runtime_safety.load_trading_mode()
            is_paper = mode in (runtime_safety.TradingMode.PAPER, runtime_safety.TradingMode.SHADOW)
            session = get_session()
            session.add(Trade(
                symbol=f"{pos.symbol} {int(pos.strike)} {pos.option_type}",
                exchange="NSE_FO",
                quantity=int(pos.lots * pos.lot_size),
                side="SELL",
                price=exit_price,
                order_type="MARKET",
                product_type="INTRADAY",
                order_id=pos.decision_id,
                status="EXECUTED",
                is_paper_trade=is_paper,
                pnl=pnl_rs,
                notes=(
                    f"reason={exit_reason} | peak_pnl={pos.highest_pnl_pct:.1f}% "
                    f"| gross_pnl={gross_pnl_rs:.2f} costs={costs_rs:.2f}"
                ),
            ))
            session.commit()
            session.close()
        except Exception:
            pass

        # Unregister from real-time exit monitoring (exit_ticker)
        try:
            from execution.exit_ticker import get_exit_ticker
            ticker = get_exit_ticker()
            ticker.unregister_position(pos.decision_id)
        except Exception:
            pass

        # Unsubscribe from real-time feed for this closed position
        if pos.instrument_key:
            try:
                from execution.websocket_feed import get_price_feed_manager
                mgr = get_price_feed_manager()
                if mgr:
                    mgr.unsubscribe([pos.instrument_key])
            except Exception:
                pass

        return {
            "symbol": pos.symbol,
            "strike": pos.strike,
            "type": pos.option_type,
            "entry": pos.entry_price,
            "exit": exit_price,
            "pnl_pct": pnl_pct,
            "pnl_rs": pnl_rs,
            "gross_pnl_rs": gross_pnl_rs,
            "costs_rs": costs_rs,
            "status": status,
            "reason": exit_reason,
        }

    def update_positions(self) -> List[Dict]:
        """
        Paper/shadow exit path (no broker SELL). Delegates to exit_manager.
        """
        from execution.exit_manager import check_and_exit_positions

        return check_and_exit_positions(use_broker_exits=False)
    
    def patch_instrument_key(self, decision_id: str, instrument_key: str) -> None:
        """Attach broker instrument_key to an open position (e.g. after live fill)."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE positions_v2 SET instrument_key = ? WHERE decision_id = ?",
                (instrument_key, decision_id),
            )
            conn.commit()
        finally:
            conn.close()

    def store_gtt_ids(
        self,
        decision_id: str,
        sl_order_id: Optional[str] = None,
        target_order_id: Optional[str] = None,
    ) -> None:
        """Persist protective GTT order IDs for an open position."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE positions_v2
                SET gtt_sl_order_id = COALESCE(?, gtt_sl_order_id),
                    gtt_target_order_id = COALESCE(?, gtt_target_order_id)
                WHERE decision_id = ?
                """,
                (sl_order_id, target_order_id, decision_id),
            )
            conn.commit()
        finally:
            conn.close()

    def clear_gtt_ids(self, decision_id: str) -> None:
        """Remove GTT IDs from a position (after cancel/trigger/close)."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                UPDATE positions_v2
                SET gtt_sl_order_id = NULL, gtt_target_order_id = NULL
                WHERE decision_id = ?
                """,
                (decision_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def get_gtt_ids(self, decision_id: str) -> tuple:
        """Return (gtt_sl_order_id, gtt_target_order_id) for a position."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT gtt_sl_order_id, gtt_target_order_id FROM positions_v2 WHERE decision_id = ?",
                (decision_id,),
            )
            row = cur.fetchone()
            return (row[0], row[1]) if row else (None, None)
        finally:
            conn.close()

    def patch_entry_after_fill(
        self,
        decision_id: str,
        entry_price: float,
        lots: Optional[int] = None,
    ) -> None:
        """Align SQLite position row with broker-confirmed fill."""
        conn = sqlite3.connect(self.db_path)
        try:
            if lots is not None:
                conn.execute(
                    """
                    UPDATE positions_v2 SET entry_price = ?, lots = ?
                    WHERE decision_id = ?
                    """,
                    (entry_price, lots, decision_id),
                )
            else:
                conn.execute(
                    "UPDATE positions_v2 SET entry_price = ? WHERE decision_id = ?",
                    (entry_price, decision_id),
                )
            conn.commit()
        finally:
            conn.close()
    
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get summary of all positions."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Open positions
        cursor.execute("""
            SELECT COUNT(*) as count, SUM(current_pnl_rs) as total_pnl
            FROM positions_v2 WHERE status = 'OPEN'
        """)
        open_row = cursor.fetchone()
        
        # Closed today
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("""
            SELECT 
                COUNT(*) as count,
                SUM(CASE WHEN current_pnl_rs > 0 THEN current_pnl_rs ELSE 0 END) as total_profit,
                SUM(CASE WHEN current_pnl_rs <= 0 THEN current_pnl_rs ELSE 0 END) as total_loss,
                SUM(CASE WHEN status = 'PROFIT' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN status = 'LOSS' THEN 1 ELSE 0 END) as losses
            FROM positions_v2 
            WHERE status != 'OPEN' AND exit_time LIKE ?
        """, (f"{today}%",))
        closed_row = cursor.fetchone()
        
        conn.close()
        
        return {
            "open_positions": open_row["count"] or 0,
            "open_pnl": open_row["total_pnl"] or 0,
            "closed_today": closed_row["count"] or 0,
            "wins": closed_row["wins"] or 0,
            "losses": closed_row["losses"] or 0,
            "realized_profit": closed_row["total_profit"] or 0,
            "realized_loss": closed_row["total_loss"] or 0,
            "net_realized": (closed_row["total_profit"] or 0) + (closed_row["total_loss"] or 0),
        }
    
    def print_status(self):
        """Print current position status."""
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        
        print(f"\n{'='*70}")
        print(f"SMART POSITION TRACKER - {now.strftime('%H:%M IST')}")
        print(f"{'='*70}")
        print(f"Exit Logic: DYNAMIC (LLM decides, only hard stop at -25%)")
        
        # Sync and update
        self.sync_from_decision_log()
        closed = self.update_positions()
        
        # Get positions
        positions = self.get_open_positions()
        summary = self.get_portfolio_summary()
        
        print(f"\nOPEN POSITIONS: {summary['open_positions']}")
        print(f"-"*70)
        
        if positions:
            for pos in positions:
                status_emoji = "🟢" if pos.current_pnl_pct > 0 else "🔴"
                peak_info = f" (peak: {pos.highest_pnl_pct:+.1f}%)" if pos.highest_pnl_pct > 0 else ""
                print(f"{status_emoji} {pos.symbol} {pos.strike} {pos.option_type}")
                print(f"   Entry: Rs {pos.entry_price:.1f} → Now: Rs {pos.current_price:.1f}")
                print(f"   P&L: {pos.current_pnl_pct:+.1f}%{peak_info} = Rs {pos.current_pnl_rs:+,.0f}")
                
                # Smart exit status
                will_exit, reason = should_exit(pos.current_pnl_pct, pos.highest_pnl_pct)
                if will_exit:
                    print(f"   ⚠️  EXIT SIGNAL: {reason}")
                else:
                    print(f"   Status: HOLDING")
                print()
        else:
            print("   No open positions")
        
        # Closed positions
        if closed:
            print(f"\nJUST CLOSED (Smart Exit):")
            print(f"-"*70)
            for c in closed:
                emoji = "🎯" if c["pnl_pct"] > 0 else "🛑"
                print(f"{emoji} {c['symbol']} {c['strike']} {c['type']}")
                print(f"   {c['entry']:.1f} → {c['exit']:.1f} = {c['pnl_pct']:+.1f}% (Rs {c['pnl_rs']:+,.0f})")
                print(f"   Reason: {c.get('reason', 'N/A')}")
        
        # Summary
        print(f"\n{'='*70}")
        print(f"TODAY'S SUMMARY")
        print(f"-"*70)
        print(f"Open P&L:     Rs {summary['open_pnl']:+,.0f}")
        print(f"Realized:     Rs {summary['net_realized']:+,.0f} ({summary['wins']} wins, {summary['losses']} losses)")
        print(f"{'='*70}\n")


# Singleton
_tracker = None

def get_position_tracker() -> PositionTracker:
    global _tracker
    if _tracker is None:
        _tracker = PositionTracker()
    return _tracker


def check_positions():
    """Convenience function to check positions."""
    tracker = get_position_tracker()
    tracker.print_status()


if __name__ == "__main__":
    check_positions()
