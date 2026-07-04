"""
Centralized runtime risk checks (pre-trade + loop guard).

Uses decision log + RiskGates constants where applicable.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz

from brain.lean_fo_brain import RiskGates
from memory.decision_log import DecisionLog, DecisionOutcome, DecisionType

IST = pytz.timezone("Asia/Kolkata")


def _max_open_positions() -> int:
    try:
        return int(os.getenv("MAX_OPEN_POSITIONS", "4"))
    except ValueError:
        return 4


def _max_consecutive_losses() -> int:
    try:
        return int(os.getenv("MAX_CONSECUTIVE_LOSSES", "4"))
    except ValueError:
        return 4


def _daily_loss_cap_rs() -> float:
    return float(RiskGates.MAX_DAILY_LOSS)


def _today_ist_bounds() -> Tuple[datetime, datetime]:
    now = datetime.now(IST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


def realized_pnl_today_from_decisions(log: DecisionLog) -> float:
    """Sum PnL from closed trade_entry decisions today (IST)."""
    start, end = _today_ist_bounds()
    rows = log.get_recent_decisions(limit=500)
    total = 0.0
    for d in rows:
        if d.decision_type != DecisionType.TRADE_ENTRY:
            continue
        if d.timestamp.tzinfo is None:
            ts = IST.localize(d.timestamp)
        else:
            ts = d.timestamp.astimezone(IST)
        if not (start <= ts < end):
            continue
        if d.outcome in (DecisionOutcome.PROFITABLE, DecisionOutcome.LOSS, DecisionOutcome.BREAKEVEN):
            total += float(d.pnl or 0)
    return total


def consecutive_losses_recent(log: DecisionLog, limit: int = 30) -> int:
    """Count consecutive LOSS outcomes from most recent closed trades."""
    rows = log.get_recent_decisions(limit=limit)
    closed = [
        d
        for d in rows
        if d.decision_type == DecisionType.TRADE_ENTRY
        and d.outcome in (DecisionOutcome.PROFITABLE, DecisionOutcome.LOSS, DecisionOutcome.BREAKEVEN)
    ]
    closed.sort(key=lambda x: x.timestamp, reverse=True)
    streak = 0
    for d in closed:
        if d.outcome == DecisionOutcome.LOSS:
            streak += 1
        else:
            break
    return streak


def open_position_count_from_tracker() -> int:
    from brain.position_tracker import get_position_tracker

    return len(get_position_tracker().get_open_positions())


def evaluate_risk_runtime(
    *,
    decision_log: Optional[DecisionLog] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Returns (risk_ok, lock_reason, audit dict).
    """
    log = decision_log or DecisionLog()
    audit: Dict[str, Any] = {}

    pnl_day = realized_pnl_today_from_decisions(log)
    audit["realized_pnl_source"] = "decision_log"
    audit["realized_pnl_today_rs"] = round(pnl_day, 2)

    broker_pnl_ok = False
    try:
        from database.operations import is_token_valid
        from execution import runtime_safety
        from mcp_server.upstox_client import get_upstox_client

        mode = runtime_safety.load_trading_mode()
        if (
            is_token_valid()
            and mode
            in (runtime_safety.TradingMode.MICRO_LIVE, runtime_safety.TradingMode.LIVE)
        ):
            client = get_upstox_client()
            if client.is_authenticated():
                summ = client.get_fo_pnl_today()
                if summ.get("ok"):
                    broker_pnl_ok = True
                    # Daily loss cap uses broker realised + unrealised (open MTM)
                    pnl_broker = float(summ.get("total_pnl") or 0)
                    audit["realized_pnl_source"] = "broker_fo_positions"
                    audit["broker_realized_pnl"] = summ.get("realized_pnl")
                    audit["broker_unrealized_pnl"] = summ.get("unrealized_pnl")
                    audit["broker_total_pnl"] = summ.get("total_pnl")
                    audit["margin_used"] = summ.get("margin_used")
                    audit["available_margin"] = summ.get("available_margin")
                    pnl_day = pnl_broker
                    audit["realized_pnl_today_rs"] = round(pnl_day, 2)
    except Exception as e:
        audit["broker_pnl_error"] = str(e)

    cap = _daily_loss_cap_rs()
    audit["daily_loss_cap_rs"] = cap
    if pnl_day <= -cap:
        src = audit.get("realized_pnl_source", "decision_log")
        return (
            False,
            f"Daily loss cap ({src}): PnL today Rs {pnl_day:.0f} <= -{cap:.0f}",
            audit,
        )

    streak = consecutive_losses_recent(log)
    audit["consecutive_losses"] = streak
    max_streak = _max_consecutive_losses()
    if streak >= max_streak:
        return False, f"Max consecutive losses ({streak} >= {max_streak})", audit

    open_n = open_position_count_from_tracker()
    audit["open_positions"] = open_n
    max_open = _max_open_positions()
    if open_n >= max_open:
        return False, f"Max open positions ({open_n} >= {max_open})", audit

    # Margin guard for new orders (when broker funds available)
    try:
        if broker_pnl_ok and audit.get("available_margin") is not None:
            min_avail = float(os.getenv("MIN_AVAILABLE_MARGIN_RS", "500"))
            if float(audit["available_margin"] or 0) < min_avail:
                return (
                    False,
                    f"Low available margin Rs {audit['available_margin']:.0f} < {min_avail:.0f}",
                    audit,
                )
    except (TypeError, ValueError):
        pass

    return True, "", audit


def log_risk_audit(message: str, details: Dict[str, Any]) -> None:
    """Append JSON lines to data_cache/risk_audit.logl."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "data_cache", "risk_audit.logl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = {
        "ts": datetime.now(IST).isoformat(),
        "message": message,
        "details": details,
    }
    try:
        import json

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str) + "\n")
    except OSError:
        pass
