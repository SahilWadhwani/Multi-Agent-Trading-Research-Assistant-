#!/usr/bin/env python3
"""
TRADING SCHEDULER

Runs the trading agent during Indian market hours.
- Scans every 30 minutes during market hours
- Sleeps outside market hours
- Runs 24/7, wakes up for IST market sessions

Scans ALL asset classes:
- F&O: NIFTY, BANKNIFTY options
- Equity: Top stocks (RELIANCE, TCS, etc.)
- ETFs: NIFTYBEES, BANKBEES

Market Hours (IST):
- Pre-market: 9:00 AM - 9:15 AM (no trading, just prep)
- Market Open: 9:15 AM - 3:30 PM
- We trade: 9:30 AM - 3:30 PM (skip first 15 mins; align with cash close)

Usage:
    python scheduler.py                    # Run forever
    python scheduler.py --once             # Run once and exit
    python scheduler.py --test             # Test mode (prints without trading)
    python scheduler.py --fo-only          # Only scan F&O (options)
    python scheduler.py --equity-only      # Only scan equity
"""

import os
import sys
import time
import json
import argparse
from datetime import datetime, timedelta
from typing import Any, Dict

import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brain.lean_fo_brain import LeanFOBrain, activity_report
from brain.orchestrator import TradingBrain
from execution import runtime_safety
from execution.lean_fo_executor import maybe_execute_lean_fo_order
from database.operations import is_token_valid
from execution.reconciliation import reconcile_state
from execution.risk_runtime import evaluate_risk_runtime


# Configuration
SCAN_INTERVAL_MINS = 30  # Scan every 30 minutes
CAPITAL = 20000  # Available capital
PAPER_MODE = True  # Legacy: ignored if TRADING_MODE is set; else False => micro_live

# F&O Symbols (Index Options)
FO_SYMBOLS = ["NIFTY", "BANKNIFTY"]


# Major index constituents (for --universe option)
NIFTY_100_ADDITIONS = [
    "ADANIGREEN", "AMBUJACEM", "AUROPHARMA", "BAJAJ-AUTO", "BANKBARODA",
    "BERGEPAINT", "BIOCON", "BOSCHLTD", "CHOLAFIN", "COLPAL", "DLF",
    "DABUR", "GAIL", "GODREJCP", "HAVELLS", "ICICIGI", "ICICIPRULI",
    "INDIGO", "IOC", "IRCTC", "JINDALSTEL", "LICI", "LTTS", "LUPIN",
    "MAXHEALTH", "MOTHERSON", "MUTHOOTFIN", "NAUKRI", "NHPC", "OBEROIRLTY",
    "OFSS", "PAGEIND", "PEL", "PFC", "PIDILITIND", "PIIND", "PNB",
    "POLYCAB", "RECLTD", "SAIL", "SBICARD", "SHREECEM", "SIEMENS",
    "SRF", "TATAPOWER", "TORNTPHARM", "TRENT", "UNIONBANK", "VBL", "ZOMATO",
]

NIFTY_200_ADDITIONS = [
    "3MINDIA", "AARTIDRUGS", "AAVAS", "ABB", "ABCAPITAL", "ABFRL", "ACC",
    "ADANIENSOL", "ADANITRANS", "AJANTPHARM", "ALKEM", "ANGELONE",
    "APARINDS", "APLAPOLLO", "APTUS", "ASHOKLEY", "ASTRAL", "ATUL",
    "AUBANK", "AUROPHARMA", "BALKRISIND", "BANDHANBNK", "BDL", "BEL",
    "BHARATFORG", "BHEL", "BSE", "CAMS", "CANFINHOME", "CDSL", "CENTRALBK",
    "CGPOWER", "CESC", "CLEAN", "COFORGE", "CONCOR", "COROMANDEL",
    "CROMPTON", "CUB", "CUMMINSIND", "DEEPAKNTR", "DELHIVERY", "DEVYANI",
    "DMART", "EASEMYTRIP", "EIDPARRY", "ELGIEQUIP", "EMAMILTD", "ESCORTS",
    "EXIDEIND", "FACT", "FEDERALBNK", "FINCABLES", "FLUOROCHEM", "FORTIS",
]


def get_dynamic_symbols(universe: str = "nifty50"):
    """
    Fetch symbols dynamically from Upstox.
    
    Args:
        universe: "nifty50", "nifty100", "nifty200", "all", or integer count
    
    Returns:
        tuple: (equity_symbols, etf_symbols)
    """
    try:
        from data_feeds.instrument_master import get_instrument_master
        master = get_instrument_master()
        
        # Base: NIFTY 50
        equity = master.get_nifty50()
        
        # Expand based on universe
        if universe == "nifty100":
            equity = equity + [s for s in NIFTY_100_ADDITIONS if master.get(s)]
        elif universe == "nifty200":
            equity = equity + [s for s in NIFTY_100_ADDITIONS if master.get(s)]
            equity = equity + [s for s in NIFTY_200_ADDITIONS if master.get(s)]
        elif universe == "all":
            equity = [i.symbol for i in master.get_all_equity()]
        elif universe.isdigit():
            # Get top N stocks (NIFTY 50 + extensions)
            n = int(universe)
            all_stocks = master.get_nifty50() + NIFTY_100_ADDITIONS + NIFTY_200_ADDITIONS
            equity = [s for s in all_stocks if master.get(s)][:n]
        
        # Get ETFs
        etf_list = master.get_etfs()
        etfs = [e.symbol for e in etf_list[:20]]  # Top 20 ETFs
        
        print(f"   Universe: {universe}")
        print(f"   Loaded {len(equity)} equity symbols")
        print(f"   Loaded {len(etfs)} ETF symbols")
        
        return equity, etfs
        
    except Exception as e:
        print(f"   Warning: Could not load dynamic symbols: {e}")
    
    # Fallback to NIFTY 50 (hardcoded as backup only)
    print("   Using default NIFTY 50 symbols")
    return [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
        "SBIN", "BHARTIARTL", "KOTAKBANK", "ITC", "LT", "AXISBANK",
        "ASIANPAINT", "MARUTI", "WIPRO", "HCLTECH", "BAJFINANCE",
        "SUNPHARMA", "TATAMOTORS", "TATASTEEL", "ONGC", "NTPC",
        "POWERGRID", "JSWSTEEL", "M&M", "ULTRACEMCO", "TECHM", "TITAN",
    ], ["NIFTYBEES", "BANKBEES"]

# Market hours (IST)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 30  # Start scanning 15 mins after open
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MIN = 30  # Match run_agent / NSE cash session end

IST = pytz.timezone('Asia/Kolkata')


def _scheduler_trading_mode() -> runtime_safety.TradingMode:
    if os.getenv("TRADING_MODE"):
        return runtime_safety.load_trading_mode()
    return runtime_safety.TradingMode.PAPER if PAPER_MODE else runtime_safety.TradingMode.MICRO_LIVE


def _scheduler_safety_bundle():
    mode = _scheduler_trading_mode()
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
            from execution.reconciliation import audit_and_recover_gtt_protection

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
    return mode, broker_ok, state, rec_rep


def _is_holiday(dt: datetime) -> bool:
    """Check NSE holiday list."""
    try:
        from agent.market_hours import MarketHoursChecker
        checker = MarketHoursChecker()
        return dt.strftime("%Y-%m-%d") in checker.holidays
    except Exception:
        return False


def is_market_hours() -> bool:
    """Check if current time is within trading hours (including holiday check)."""
    now = datetime.now(IST)
    
    # Weekend check
    if now.weekday() >= 5:
        return False

    # Holiday check
    if _is_holiday(now):
        return False
    
    # Time check
    market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0)
    market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0)
    
    return market_open <= now <= market_close


def time_until_market_open() -> timedelta:
    """Calculate time until next market open."""
    now = datetime.now(IST)
    
    # Find next market open
    next_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0)
    
    # If we're past today's open, move to next day
    if now >= next_open:
        next_open += timedelta(days=1)
    
    # Skip weekends and configured exchange holidays.
    while next_open.weekday() >= 5 or _is_holiday(next_open):
        next_open += timedelta(days=1)
    
    return next_open - now


def run_fo_scan(fo_brain: LeanFOBrain, capital: int, test_mode: bool = False):
    """Run F&O (options) scan on index symbols."""
    print(f"\n--- F&O OPTIONS SCAN ---")
    mode, broker_ok, state, _ = _scheduler_safety_bundle()

    # Pre-fetch LLM agent consensus (cached, no latency in hot path)
    for symbol in FO_SYMBOLS:
        try:
            fo_brain.prefetch_agent_consensus(symbol)
        except Exception as e:
            print(f"   Agent consensus prefetch skipped for {symbol}: {e}")

    for symbol in FO_SYMBOLS:
        try:
            result = fo_brain.analyze(symbol, capital)
            decision = result.get("decision", "ERROR")

            if decision == "EXECUTE" and not test_mode:
                print(f"\n🚨 F&O SIGNAL: {symbol}")
                sig = result.get('signal', {})
                print(f"   {sig.get('direction')} {sig.get('strike')} @ Rs {sig.get('premium', 0):.1f}")
                if test_mode:
                    print("   🧪 TEST MODE - no broker action")
                elif not broker_ok:
                    print(f"   ⛔ BROKER BLOCKED: {state.reasons_blocked}")
                else:
                    ex = maybe_execute_lean_fo_order(
                        symbol=symbol,
                        analyze_result=result,
                        broker_orders_allowed=broker_ok,
                        decision_id=result.get("decision_id"),
                        trading_mode=mode,
                    )
                    if ex.get("executed"):
                        print(f"   ✅ LIVE ORDER SUBMITTED intent={ex.get('intent_id')}")
                    else:
                        print(f"   📝 No broker order: {ex.get('reason') or ex.get('error') or ex}")
            else:
                ctx = result.get('context', {})
                print(f"   {symbol}: {ctx.get('trend', 'N/A')} trend, PCR {ctx.get('pcr', 'N/A')} → {decision}")
            
        except Exception as e:
            print(f"   Error scanning {symbol}: {e}")


def run_equity_scan(equity_brain: TradingBrain, symbols: list, asset_type: str, capital: int, test_mode: bool = False):
    """Run equity/ETF scan."""
    print(f"\n--- {asset_type} SCAN ({len(symbols)} symbols) ---")
    _, broker_ok, state, _ = _scheduler_safety_bundle()

    signals_found = 0
    for symbol in symbols:
        try:
            result = equity_brain.analyze_and_decide(symbol, available_capital=capital)
            decision = result.get("decision", "HOLD")
            action = result.get("action", "HOLD")
            
            # Check if there's a trade proposal
            tp = result.get("trade_proposal", {})
            
            if action in ["BUY", "SELL"] and not test_mode:
                signals_found += 1
                print(f"\n🚨 {asset_type} SIGNAL: {symbol}")
                print(f"   Action: {action} {tp.get('quantity', 0)} @ Rs {tp.get('price', 0):,.2f}")
                if not broker_ok:
                    print(f"   ⛔ BROKER BLOCKED (equity live needs EQUITY_LIVE_ENABLED + mode): {state.reasons_blocked}")
                else:
                    print("   ℹ️  Equity execution is handled inside TradingBrain when paper_mode=False and EQUITY_LIVE_ENABLED=1")
            else:
                tech = result.get("technical_analysis", {})
                # Only print details for symbols with notable bias
                bias = tech.get('bias', 'neutral')
                if bias in ['bullish', 'bearish']:
                    print(f"   {symbol}: {bias} bias → {action}")
            
        except Exception as e:
            print(f"   Error scanning {symbol}: {e}")
    
    print(f"   Scanned {len(symbols)} {asset_type.lower()} | Signals: {signals_found}")


def _bump_agent_health(note: str = "scheduler") -> None:
    """Write agent_health.json so the Streamlit dashboard shows a fresh heartbeat."""
    now = datetime.now(IST)
    try:
        health_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data_cache",
            "agent_health.json",
        )
        os.makedirs(os.path.dirname(health_path), exist_ok=True)
        with open(health_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "updated_at": now.isoformat(),
                    "pid": os.getpid(),
                    "note": note,
                },
                f,
                indent=2,
            )
    except OSError:
        pass


def _exit_tick_and_health() -> None:
    """Market-hours exit / P&L tick (heartbeat is written separately every 15s)."""
    now = datetime.now(IST)
    try:
        from execution.exit_manager import check_and_exit_positions

        exits = check_and_exit_positions()
        if exits:
            print(f"\n   📤 Exits processed: {len(exits)}")
            for row in exits[:5]:
                print(f"      - {row.get('symbol')} {row.get('strike')} {row.get('reason')}")
        else:
            from brain.position_tracker import get_position_tracker
            tracker = get_position_tracker()
            positions = tracker.get_open_positions()
            if positions:
                summary = []
                for p in positions[:4]:
                    cp = tracker.estimate_current_price(p)
                    pnl = ((cp - p.entry_price) / p.entry_price) * 100
                    summary.append(f"{int(p.strike)}{p.option_type[0]}:{pnl:+.0f}%")
                print(f"   [{now.strftime('%H:%M:%S')}] Exit check: {' | '.join(summary)} (holding)")
    except Exception as e:
        print(f"   Exit check error: {e}")


def run_scan(fo_brain: LeanFOBrain, equity_brain: TradingBrain, 
             equity_symbols: list, etf_symbols: list, capital: int,
             scan_fo: bool = True, scan_equity: bool = True, test_mode: bool = False):
    """Run a full scan on all asset classes."""
    now = datetime.now(IST)
    print(f"\n{'='*70}")
    print(f"SCHEDULED SCAN - {now.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"{'='*70}")
    
    # F&O Scan
    if scan_fo:
        run_fo_scan(fo_brain, capital, test_mode)
    
    # Equity Scan
    if scan_equity:
        run_equity_scan(equity_brain, equity_symbols, "EQUITY", capital, test_mode)
        
        # ETF Scan (if we have any)
        if etf_symbols:
            run_equity_scan(equity_brain, etf_symbols, "ETF", capital, test_mode)
    
    print(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Trading Scheduler")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--test", action="store_true", help="Test mode (no actual trades)")
    parser.add_argument("--interval", type=int, default=SCAN_INTERVAL_MINS, help="Scan interval in minutes")
    parser.add_argument("--fo-only", action="store_true", help="Only scan F&O (options)")
    parser.add_argument("--equity-only", action="store_true", help="Only scan equity stocks")
    parser.add_argument("--universe", type=str, default="nifty50", 
                       help="Stock universe: nifty50, nifty100, nifty200, all, or number (default: nifty50)")
    parser.add_argument("--capital", type=int, default=CAPITAL, help="Trading capital in Rs (default: 20000)")
    args = parser.parse_args()
    
    # Determine what to scan
    scan_fo = not args.equity_only
    scan_equity = not args.fo_only
    
    # Use specified capital
    capital = args.capital
    
    # Fetch dynamic symbols from Upstox (no more hardcoding!)
    print("\n📊 Loading symbols from Upstox...")
    equity_symbols, etf_symbols = get_dynamic_symbols(args.universe)
    
    symbols_info = []
    if scan_fo:
        symbols_info.append(f"F&O: {', '.join(FO_SYMBOLS)}")
    if scan_equity:
        symbols_info.append(f"Equity: {len(equity_symbols)} stocks + {len(etf_symbols)} ETFs")
    
    universe_str = f"Universe: {args.universe}"
    capital_str = f"Capital: Rs {capital:,}"
    mode = _scheduler_trading_mode()
    mode_str = f"Mode: {mode.value}"
    paper_legacy = PAPER_MODE and not os.getenv("TRADING_MODE")
    mode_str_full = f"{mode_str} (legacy PAPER_MODE={PAPER_MODE})" if paper_legacy else mode_str
    kill = runtime_safety.kill_switch_active()
    mode_display = f"{mode_str_full} | ENABLED={runtime_safety.trading_enabled_flag()} | KILL={kill}"

    print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║                    TRADING AGENT SCHEDULER                           ║
╠══════════════════════════════════════════════════════════════════════╣
║  {' | '.join(symbols_info):<66} ║
║  {universe_str:<30} {capital_str:<30} ║
║  {mode_display[:66]:<66} ║
║  Interval: {args.interval} min                                        ║
║  Market Hours: {MARKET_OPEN_HOUR}:{MARKET_OPEN_MIN:02d} - {MARKET_CLOSE_HOUR}:{MARKET_CLOSE_MIN:02d} IST                                 ║
╚══════════════════════════════════════════════════════════════════════╝
""")

    eq_paper = True
    if mode in (runtime_safety.TradingMode.MICRO_LIVE, runtime_safety.TradingMode.LIVE):
        if os.getenv("EQUITY_LIVE_ENABLED", "").strip().lower() in ("1", "true", "yes"):
            eq_paper = False

    # Initialize brains
    fo_brain = (
        LeanFOBrain(paper_mode=(mode == runtime_safety.TradingMode.PAPER)) if scan_fo else None
    )
    equity_brain = TradingBrain(paper_mode=eq_paper) if scan_equity else None
    
    if args.once:
        # Single run mode
        if is_market_hours():
            run_scan(fo_brain, equity_brain, equity_symbols, etf_symbols, capital, scan_fo, scan_equity, args.test)
        else:
            print("Market is closed. Use --test to run anyway in test mode.")
            if args.test:
                run_scan(fo_brain, equity_brain, equity_symbols, etf_symbols, capital, scan_fo, scan_equity, test_mode=True)
        
        # Show activity report
        print("\n")
        activity_report(1)
        return
    
    # Startup reconciliation (before first trade)
    try:
        from execution.reconciliation import startup_reconciliation
        startup_client = None
        if mode in (runtime_safety.TradingMode.MICRO_LIVE, runtime_safety.TradingMode.LIVE):
            try:
                from database.operations import get_stored_token
                from mcp_server.upstox_client import get_upstox_client
                tok = get_stored_token()
                if tok and tok.access_token:
                    startup_client = get_upstox_client()
            except Exception:
                pass
        recon = startup_reconciliation(client=startup_client)
        print(f"   Startup reconciliation: intents={recon['pending_intents_found']} "
              f"stale={recon['stale_positions']} freeze={recon['freeze_set']}")
        if recon["freeze_set"]:
            print("   ⚠️  TRADING FROZEN by startup reconciliation — manual check required")
    except Exception as e:
        print(f"   Startup reconciliation error: {e}")

    # Start real-time price feed (WebSocket V3 + REST fallback)
    price_feed = None
    try:
        from execution.websocket_feed import start_price_feed
        token_obj = None
        try:
            from database.operations import get_stored_token
            token_obj = get_stored_token()
        except Exception:
            pass
        if token_obj and token_obj.access_token:
            initial_keys = ["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"]
            price_feed = start_price_feed(token_obj.access_token, initial_keys)
            print("   Real-time price feed: ACTIVE (WebSocket V3 + REST fallback)")
        else:
            print("   Real-time price feed: SKIPPED (no token)")
    except Exception as e:
        print(f"   Real-time price feed: FAILED ({e})")

    # Enable real-time SL/target monitoring (tick-by-tick, no 15-sec delay)
    try:
        from execution.exit_ticker import enable_exit_ticker
        enable_exit_ticker()
        print("   Exit Ticker: ACTIVE (real-time SL/target on every tick)")
    except Exception as e:
        print(f"   Exit Ticker: DISABLED ({e})")

    # Continuous mode with SEPARATE exit thread (never blocked by scans)
    EXIT_CHECK_INTERVAL = 15.0  # Check exits every 15 seconds (backup safety check)
    print(f"Starting continuous scheduler...")
    print(f"   Exit checks every {EXIT_CHECK_INTERVAL:.0f}s | Full scans every {args.interval} min")
    print(f"   Exit thread: INDEPENDENT (never blocked by scans)")
    print("Press Ctrl+C to stop.\n")

    import threading

    _exit_thread_running = True

    _post_market_recovery_done_date = None
    pre_brief_state: Dict[str, Any] = {"date": None}

    def _is_pre_market_briefing_window() -> bool:
        """9:10–9:14 IST, weekdays, non-holiday."""
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False
        if _is_holiday(now):
            return False
        return now.hour == 9 and 10 <= now.minute < 15

    def _try_pre_market_briefing() -> None:
        if not _is_pre_market_briefing_window():
            return
        today = datetime.now(IST).date()
        if pre_brief_state["date"] == today:
            return
        if not scan_fo or fo_brain is None:
            return
        try:
            from brain.regime_detector import get_regime_detector
            from llm.client import get_llm_client

            rd = get_regime_detector()
            plan = rd.generate_pre_market_briefing(fo_brain.fo_feed, get_llm_client())
            pre_brief_state["date"] = today
            if plan:
                print(
                    f"\n[PRE-MARKET BRIEFING {datetime.now(IST).strftime('%H:%M')} IST] "
                    f"regime={plan.get('regime')} session_bias={plan.get('session_bias')}"
                )
        except Exception as e:
            print(f"   Pre-market briefing error: {e}")
            pre_brief_state["date"] = today

    def _is_post_market_recovery_window() -> bool:
        """15:30 - 16:00 IST: window to reconcile and close any stale positions."""
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False
        return (now.hour == 15 and now.minute >= 30) or (now.hour == 16 and now.minute < 1)

    def _post_market_recovery():
        """Run broker reconciliation FIRST, then close stale local positions.

        Order matters: if broker still holds a position, we must know before
        blindly marking local state as closed.
        """
        nonlocal _post_market_recovery_done_date
        today = datetime.now(IST).date()
        if _post_market_recovery_done_date == today:
            return
        _post_market_recovery_done_date = today

        print(f"\n[POST-MARKET RECOVERY] Running reconciliation at {datetime.now(IST).strftime('%H:%M')} IST")

        # STEP 1: Broker reconciliation — establishes ground truth
        # ok=True means "local and broker MATCH" — NOT necessarily "broker is flat".
        # We must check broker_fo_positions count to know if broker actually has zero positions.
        broker_has_positions = False
        try:
            from execution.reconciliation import reconcile_state
            from mcp_server.upstox_client import get_upstox_client
            client = get_upstox_client()
            ok, report = reconcile_state(
                token_valid=client.is_authenticated(),
                fetch_broker=client.is_authenticated(),
                client=client,
            )
            broker_fo_count = report.get("broker_fo_positions", 0)
            orphans = report.get("orphan_broker_positions", [])
            if broker_fo_count > 0:
                broker_has_positions = True
                print(f"   Broker still has {broker_fo_count} F&O position(s) post-market")
            if not ok:
                print(f"   Reconciliation mismatch: {orphans}")
            elif broker_fo_count == 0:
                print(f"   Reconciliation OK — broker confirmed flat (0 positions)")
        except Exception as e:
            print(f"   Reconciliation error: {e}")
            broker_has_positions = True  # Assume worst case

        # STEP 2: Close stale local positions only if broker is confirmed flat
        from brain.position_tracker import get_position_tracker
        tracker = get_position_tracker()
        open_positions = tracker.get_open_positions()

        if open_positions:
            if broker_has_positions:
                print(
                    f"   CRITICAL: {len(open_positions)} local positions OPEN and broker "
                    f"may still hold positions — NOT closing local state. Manual check required."
                )
                from execution.risk_runtime import log_risk_audit
                log_risk_audit("post_market_broker_not_flat", {
                    "local_open": len(open_positions),
                })
            else:
                print(f"   Closing {len(open_positions)} stale local positions (broker confirmed flat)")
                for pos in open_positions:
                    px = tracker.estimate_current_price(pos)
                    tracker.close_position_record(
                        pos,
                        exit_price=px if px > 0 else pos.entry_price * 0.7,
                        exit_reason="post_market_recovery_broker_flat",
                        ist_now=datetime.now(IST),
                    )
                    print(f"   Closed stale: {pos.symbol} {pos.strike}{pos.option_type} @ {px:.2f}")

    def _exit_monitor_thread():
        """Dedicated thread for exit checks - runs every 15s, including post-market recovery."""
        while _exit_thread_running:
            try:
                _try_pre_market_briefing()
                hb_note = "exit_thread_off_hours"
                if is_market_hours():
                    _exit_tick_and_health()
                    hb_note = "exit_thread_15s"
                elif _is_post_market_recovery_window():
                    _post_market_recovery()
                    hb_note = "post_market_recovery"
                _bump_agent_health(hb_note)
            except Exception as e:
                print(f"   Exit thread error: {e}")
            time.sleep(EXIT_CHECK_INTERVAL)

    exit_thread = threading.Thread(target=_exit_monitor_thread, daemon=True, name="exit-monitor")
    exit_thread.start()
    print("   Exit monitor thread started (checking every 15s)")
    
    last_scan_mon = 0.0
    interval_sec = max(60, int(args.interval) * 60)

    try:
        while True:
            if is_market_hours():
                nowm = time.monotonic()

                if nowm - last_scan_mon >= interval_sec:
                    run_scan(fo_brain, equity_brain, equity_symbols, etf_symbols, capital, scan_fo, scan_equity, args.test)
                    last_scan_mon = nowm
                    print(f"Next full scan in {args.interval} minutes (exits every {EXIT_CHECK_INTERVAL:.0f}s)...")

                sleep_s = max(1.0, min(30.0, (last_scan_mon + interval_sec) - time.monotonic()))
                time.sleep(sleep_s)
                
            else:
                # Outside market hours — run daily calibration once
                now = datetime.now(IST)
                if not hasattr(main, '_calibrated_today') or main._calibrated_today != now.date():
                    try:
                        from memory.calibrator import get_calibrator
                        calibrator = get_calibrator()
                        print(f"\n[{now.strftime('%H:%M')} IST] Running daily calibration...")
                        cal_result = calibrator.run_daily_calibration(days=14)
                        main._calibrated_today = now.date()
                        adj = cal_result.get("adjustments", [])
                        if adj:
                            print(f"   Calibration: {len(adj)} symbol(s) adjusted")
                        else:
                            print(f"   Calibration: No adjustments needed")
                    except Exception as e:
                        print(f"   Calibration error: {e}")
                        main._calibrated_today = now.date()

                wait_time = time_until_market_open()
                hours = wait_time.seconds // 3600
                minutes = (wait_time.seconds % 3600) // 60
                
                print(f"[{now.strftime('%H:%M')} IST] Market closed. Next open in {wait_time.days}d {hours}h {minutes}m")
                
                # Sleep for 5 minutes and check again
                time.sleep(300)
                
    except KeyboardInterrupt:
        print("\n\nScheduler stopped by user.")
        _exit_thread_running = False
        if price_feed:
            from execution.websocket_feed import stop_price_feed
            stop_price_feed()
            print("Price feed stopped.")
        print("\nFinal Activity Report:")
        activity_report(1)


if __name__ == "__main__":
    main()
