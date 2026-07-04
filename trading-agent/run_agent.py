#!/usr/bin/env python3
"""
AUTONOMOUS TRADING AGENT RUNNER

This script runs the trading agent independently of Cursor/IDE.
It handles:
- 24/7 operation with market hours awareness
- Automatic position management (intraday exit)
- Logging to files (not just console)
- Crash recovery and health monitoring
- Efficient batch scanning (not one-by-one)

Usage:
    python run_agent.py                 # Run forever
    python run_agent.py --once          # Single scan
    python run_agent.py --status        # Check status
    nohup python run_agent.py &         # Run in background

Long-term Vision: Fully autonomous, makes money while you sleep.
"""

import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Any
import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from execution import runtime_safety

# Setup logging to file
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_file = os.path.join(LOG_DIR, f"agent_{datetime.now().strftime('%Y%m%d')}.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()  # Also print to console
    ]
)
logger = logging.getLogger(__name__)

# Configuration
CAPITAL = 20000
IST = pytz.timezone('Asia/Kolkata')

# Scan intervals
FO_SCAN_INTERVAL_MINS = 5       # Scan F&O every 5 mins
EQUITY_SCAN_INTERVAL_MINS = 30  # Scan equity every 30 mins (slower)
POSITION_CHECK_MINS = 1         # Exits / SL: check every 1 min during market hours


def _runtime_safety_bundle() -> Dict[str, Any]:
    """Token, risk, reconciliation, and broker order permission."""
    from database.operations import is_token_valid
    from execution import runtime_safety
    from execution.reconciliation import reconcile_state, audit_and_recover_gtt_protection
    from execution.risk_runtime import evaluate_risk_runtime

    mode = runtime_safety.load_trading_mode()
    tok = is_token_valid()
    risk_ok, risk_reason, _ = evaluate_risk_runtime()
    client = None
    if tok and mode in (
        runtime_safety.TradingMode.MICRO_LIVE,
        runtime_safety.TradingMode.LIVE,
    ):
        from mcp_server.upstox_client import get_upstox_client

        client = get_upstox_client()
    rec_ok, rec_rep = reconcile_state(
        token_valid=tok, fetch_broker=client is not None, client=client
    )
    if rec_ok and client is not None:
        try:
            gtt_ok, gtt_rep = audit_and_recover_gtt_protection(client)
            rec_rep["gtt_audit"] = gtt_rep
            rec_ok = rec_ok and gtt_ok
        except Exception as ex:
            rec_rep["gtt_audit_error"] = str(ex)
            rec_ok = False
    state, broker_ok = runtime_safety.evaluate_runtime_safety(
        token_valid=tok,
        reconciliation_ok=rec_ok,
        risk_ok=risk_ok,
        risk_lock_reason=risk_reason,
    )
    return {
        "mode": mode,
        "token_valid": tok,
        "risk_ok": risk_ok,
        "risk_reason": risk_reason,
        "reconciliation_ok": rec_ok,
        "reconciliation_report": rec_rep,
        "safety_state": state.to_dict(),
        "broker_orders_allowed": broker_ok,
    }


def is_market_hours() -> tuple:
    """Check if market is open. Returns (is_open, message)."""
    now = datetime.now(IST)
    
    # Weekend
    if now.weekday() >= 5:
        return False, f"Weekend ({now.strftime('%A')})"
    try:
        from agent.market_hours import MarketHoursChecker

        if now.strftime("%Y-%m-%d") in MarketHoursChecker().holidays:
            return False, "Market holiday"
    except Exception:
        pass
    
    # Market hours: 9:15 AM - 3:30 PM IST
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0, tzinfo=IST)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0, tzinfo=IST)
    
    # Trading hours: 9:30 AM - 3:30 PM (avoid first 15 min; align with NSE cash close)
    trade_start = now.replace(hour=9, minute=30, second=0, microsecond=0, tzinfo=IST)
    trade_end = now.replace(hour=15, minute=30, second=0, microsecond=0, tzinfo=IST)
    
    if now < market_open:
        mins_to_open = (market_open - now).seconds // 60
        return False, f"Pre-market ({mins_to_open} mins to open)"
    
    if now > market_close:
        return False, "Market closed"
    
    if now < trade_start:
        return False, "First 15 mins - waiting"
    
    if now > trade_end:
        return False, "After market close window"
    
    return True, "Trading hours"


def scan_fo(
    brain,
    capital: int,
    broker_ok: bool,
    mode: runtime_safety.TradingMode,
) -> List[Dict]:
    """Scan F&O instruments. Fast - only 2-3 symbols."""
    from execution.lean_fo_executor import maybe_execute_lean_fo_order

    symbols = ["NIFTY", "BANKNIFTY"]
    signals = []

    # Pre-fetch LLM agent consensus once (cached for all symbols this cycle)
    for symbol in symbols:
        try:
            brain.prefetch_agent_consensus(symbol)
        except Exception as e:
            logger.warning(f"Agent consensus prefetch skipped for {symbol}: {e}")

    for symbol in symbols:
        try:
            result = brain.analyze(symbol, capital)
            if result.get("decision") == "EXECUTE":
                ex = maybe_execute_lean_fo_order(
                    symbol=symbol,
                    analyze_result=result,
                    broker_orders_allowed=broker_ok,
                    decision_id=result.get("decision_id"),
                    trading_mode=mode,
                )
                result["broker_execution"] = ex
                signals.append({
                    "symbol": symbol,
                    "signal": result.get("signal", {}),
                    "context": result.get("context", {}),
                    "broker_execution": ex,
                })
                logger.info(
                    f"F&O SIGNAL: {symbol} {result.get('signal', {}).get('direction')} "
                    f"exec={ex.get('executed')} {ex.get('reason') or ex.get('error') or ''}"
                )
            else:
                logger.debug(f"F&O {symbol}: {result.get('decision')} - {result.get('context', {}).get('trend')}")
        except Exception as e:
            logger.error(f"F&O scan error {symbol}: {e}")

    return signals


def scan_equity_batch(brain, symbols: List[str], capital: int) -> List[Dict]:
    """
    Scan equity in batches (not one-by-one).
    This is more efficient for large symbol lists.
    """
    signals = []
    batch_size = 10  # Process 10 at a time
    
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        logger.debug(f"Scanning equity batch {i//batch_size + 1}: {batch}")
        
        for symbol in batch:
            try:
                result = brain.analyze_and_decide(symbol, available_capital=capital)
                fd = result.get("final_decision") or {}
                action = fd.get("action", "HOLD")
                
                if action in ["BUY", "SELL"]:
                    signals.append({
                        "symbol": symbol,
                        "action": action,
                        "proposal": result.get("trade_proposal", {}),
                    })
                    logger.info(f"EQUITY SIGNAL: {symbol} {action}")
            except Exception as e:
                import traceback
                logger.error(f"Equity scan error {symbol}: {e}")
                logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Small delay between batches to avoid API rate limits
        time.sleep(1)
    
    return signals


def _write_agent_health(*, note: str = "") -> None:
    """Heartbeat for dashboard / supervised wrapper."""
    try:
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data_cache",
            "agent_health.json",
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "updated_at": datetime.now(IST).isoformat(),
            "pid": os.getpid(),
            "note": note,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except OSError:
        pass


def check_positions():
    """Broker-backed exits (live) or local closes (paper)."""
    from execution.exit_manager import check_and_exit_positions

    closed = check_and_exit_positions()
    for row in closed:
        logger.warning(
            f"EXIT DONE: {row.get('symbol')} {row.get('strike')} {row.get('type')} "
            f"reason={row.get('reason')}"
        )
    _write_agent_health(note="position_check")
    return closed


def get_status() -> Dict[str, Any]:
    """Get current agent status."""
    from brain.signal_tracker import get_signal_tracker
    from brain.position_tracker import get_position_tracker
    from execution.runtime_safety import preflight_for_live_modes

    tracker = get_signal_tracker()
    pos_tracker = get_position_tracker()

    report = tracker.get_activity_report(days=1)
    summary = pos_tracker.get_portfolio_summary()

    is_open, market_status = is_market_hours()
    pf = preflight_for_live_modes()

    return {
        "timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
        "market_status": market_status,
        "is_trading": is_open,
        "preflight": pf,
        "scans_today": report.get("total_scans", 0),
        "signals_today": report.get("by_decision", {}).get("EXECUTE", 0),
        "signal_rate": report.get("signal_rate", "0%"),
        "open_positions": summary.get("open_positions", 0),
        "open_pnl": summary.get("open_pnl", 0),
        "realized_pnl": summary.get("net_realized", 0),
    }


def run_once():
    """Run a single scan cycle."""
    from brain.lean_fo_brain import LeanFOBrain

    bundle = _runtime_safety_bundle()
    mode = bundle["mode"]
    logger.info("=" * 60)
    logger.info("SINGLE SCAN CYCLE")
    logger.info(f"TRADING_MODE={mode.value} broker_orders_allowed={bundle['broker_orders_allowed']}")
    logger.info(f"Runtime safety: {json.dumps(bundle['safety_state'], default=str)}")
    logger.info("=" * 60)

    is_open, status = is_market_hours()
    logger.info(f"Market: {status}")

    if not is_open:
        logger.info("Market closed - checking positions only")
        check_positions()
        return

    brain = LeanFOBrain(paper_mode=(mode == runtime_safety.TradingMode.PAPER))

    logger.info("Scanning F&O...")
    fo_signals = scan_fo(brain, CAPITAL, bundle["broker_orders_allowed"], mode)
    logger.info(f"F&O signals: {len(fo_signals)}")
    _write_agent_health(note="fo_scan_once")

    logger.info("Checking positions...")
    exits = check_positions()
    logger.info(f"Exit signals: {len(exits)}")

    status = get_status()
    logger.info(f"Status: {json.dumps(status, indent=2)}")


def run_forever():
    """Run the agent continuously."""
    from brain.lean_fo_brain import LeanFOBrain
    from brain.orchestrator import TradingBrain
    from data_feeds.instrument_master import get_instrument_master

    bundle = _runtime_safety_bundle()
    mode = bundle["mode"]

    logger.info("=" * 60)
    logger.info("AUTONOMOUS TRADING AGENT STARTED")
    logger.info(f"Capital: Rs {CAPITAL:,}")
    logger.info(f"TRADING_MODE={mode.value}")
    logger.info(f"broker_orders_allowed={bundle['broker_orders_allowed']}")
    logger.info(f"Runtime safety: {json.dumps(bundle['safety_state'], default=str)}")
    logger.info(f"Log file: {log_file}")
    logger.info("=" * 60)

    # Startup reconciliation: recover pending intents + check broker truth
    try:
        from execution.reconciliation import startup_reconciliation
        from mcp_server.upstox_client import get_upstox_client
        startup_client = None
        if bundle["token_valid"] and mode in (
            runtime_safety.TradingMode.MICRO_LIVE,
            runtime_safety.TradingMode.LIVE,
        ):
            startup_client = get_upstox_client()
        recon_report = startup_reconciliation(client=startup_client)
        logger.info(f"Startup reconciliation: intents={recon_report['pending_intents_found']}, "
                    f"resolved={recon_report['pending_intents_resolved']}, "
                    f"stale={recon_report['stale_positions']}, "
                    f"freeze={recon_report['freeze_set']}")
        if recon_report["freeze_set"]:
            logger.warning("STARTUP RECONCILIATION SET TRADING FREEZE — manual check required")
    except Exception as e:
        logger.error(f"Startup reconciliation error: {e}")

    fo_brain = LeanFOBrain(paper_mode=(mode == runtime_safety.TradingMode.PAPER))
    equity_paper = True
    if mode in (runtime_safety.TradingMode.MICRO_LIVE, runtime_safety.TradingMode.LIVE):
        if os.getenv("EQUITY_LIVE_ENABLED", "").strip().lower() in ("1", "true", "yes"):
            equity_paper = False
    equity_brain = None  # Lazy load

    master = get_instrument_master()
    equity_symbols = master.get_nifty50()[:20]

    # Initialize with timezone-aware datetime (far in past to trigger first scan)
    now_tz = datetime.now(IST)
    last_fo_scan = now_tz - timedelta(hours=24)
    last_equity_scan = now_tz - timedelta(hours=24)
    last_position_check = now_tz - timedelta(hours=24)

    while True:
        try:
            now = datetime.now(IST)
            is_open, market_status = is_market_hours()

            if is_open:
                bundle = _runtime_safety_bundle()
                mode = bundle["mode"]
                broker_ok = bundle["broker_orders_allowed"]

                if (now - last_fo_scan).total_seconds() >= FO_SCAN_INTERVAL_MINS * 60:
                    logger.info("Running F&O scan...")
                    fo_signals = scan_fo(fo_brain, CAPITAL, broker_ok, mode)
                    last_fo_scan = now
                    _write_agent_health(note="fo_scan")

                    for sig in fo_signals:
                        logger.info(
                            f"  SIGNAL: {sig['symbol']} {sig['signal'].get('direction')} "
                            f"exec={sig.get('broker_execution', {}).get('executed')}"
                        )

                if (now - last_position_check).total_seconds() >= POSITION_CHECK_MINS * 60:
                    exits = check_positions()
                    last_position_check = now

                    for row in exits:
                        logger.warning(
                            f"  EXIT: {row.get('symbol')} {row.get('strike')} {row.get('reason', '')}"
                        )

                if (now - last_equity_scan).total_seconds() >= EQUITY_SCAN_INTERVAL_MINS * 60:
                    if equity_brain is None:
                        equity_brain = TradingBrain(paper_mode=equity_paper)

                    logger.info(f"Running equity scan ({len(equity_symbols)} symbols)...")
                    eq_signals = scan_equity_batch(equity_brain, equity_symbols, CAPITAL)
                    last_equity_scan = now

                    for sig in eq_signals:
                        logger.info(f"  SIGNAL: {sig['symbol']} {sig['action']}")

                time.sleep(60)

            else:
                logger.info(f"[{now.strftime('%H:%M')}] {market_status}")
                time.sleep(300)

        except KeyboardInterrupt:
            logger.info("Agent stopped by user")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="Autonomous Trading Agent")
    parser.add_argument("--once", action="store_true", help="Run single scan and exit")
    parser.add_argument("--status", action="store_true", help="Show current status")
    args = parser.parse_args()
    
    if args.status:
        status = get_status()
        print(json.dumps(status, indent=2))
    elif args.once:
        run_once()
    else:
        run_forever()


if __name__ == "__main__":
    main()
