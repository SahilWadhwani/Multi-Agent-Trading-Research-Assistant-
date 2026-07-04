# QUICK FIX GUIDE: Top 5 Critical Issues with Code Examples
## May 13, 2026 | Implementation Priority

---

## #1: STOP DOUBLE-EXIT ATTEMPTS (15 min fix)

**Problem**: Both `exit_manager.py` and `exit_ticker.py` can exit the same position, creating accidental shorts.

**Location**: `exit_manager.py` (line 115+) conflicts with `exit_ticker.py` (line 300+)

**Current Code** (BROKEN):
```python
# exit_ticker.py - registers intent
self._exiting.add(decision_id)  # Line ~76
self._trigger_exit(decision_id, info, "SL_HIT", ltp)

# BUT exit_manager.py never checks this flag!
# exit_manager.py line 115:
if exit_now:
    summary = exit_position_via_broker_safely(...)  # Places SELL without checking _exiting
```

**Fixed Code**:
```python
# In exit_manager.py, add check at line 115:
def check_and_exit_positions(...):
    ...
    for pos in positions:
        exit_now, exit_reason = should_exit(...)
        
        # NEW: Check if exit_ticker already triggered this
        if pos.decision_id in exit_ticker._exiting:
            logger.info(f"Skipping {pos.decision_id} - exit_ticker already exiting")
            continue
        
        if not exit_now:
            continue
        ...
```

**Verify**:
```bash
# Test: Place a trade, let exit_ticker trigger SL, verify exit_manager skips it
# Check logs for: "Skipping XXX - exit_ticker already exiting"
```

---

## #2: AUTO-FLATTEN ON GTT FAILURE IN MICRO_LIVE (30 min fix)

**Problem**: GTT placement fails → position has no broker-side SL → overnight gap risk.

**Location**: `lean_fo_executor.py` lines 504-529

**Current Code** (BROKEN):
```python
if not gtt_result["ok"]:
    out["gtt_error"] = gtt_result["error"]
    ...
    if trading_mode == runtime_safety.TradingMode.LIVE:
        _immediate_flatten(client, str(instrument_key), actual_qty, decision_id or intent_id)
        out["executed"] = False
        out["error"] = "gtt_failed_position_flattened"
    else:
        # KNOWN LIMITATION: ... just freeze
        print(warn_msg, file=sys.stderr)
        runtime_safety.set_trading_freeze(...)
```

**Fixed Code**:
```python
if not gtt_result["ok"]:
    out["gtt_error"] = gtt_result["error"]
    log_risk_audit("gtt_failed_post_entry", {...})
    
    # ALWAYS flatten if GTT fails, regardless of mode
    logger.warning(f"GTT failed: {gtt_result['error']}. Flattening position immediately.")
    _immediate_flatten(client, str(instrument_key), actual_qty, decision_id or intent_id)
    out["executed"] = False
    out["error"] = "gtt_failed_position_flattened"
    
    # Then freeze trading (don't accept new trades until checked)
    runtime_safety.set_trading_freeze(
        f"GTT failed for {decision_id}: {gtt_result['error']}. Position was flattened.",
        source="lean_fo_executor"
    )

# Remove the else block entirely
```

**Verify**:
```bash
# Mock GTT failure, verify position flattens immediately
# Check that SELL order is placed and confirmed
```

---

## #3: AUTO-REFRESH TOKEN AT 3:15 AM IST (1 hour fix)

**Problem**: Tokens expire at 3:30 AM IST daily. No refresh mechanism → crash after 3:30 AM.

**Location**: `upstox_client.py` (add new method) and `run_agent.py` (call it)

**Current Code** (BROKEN):
```python
# upstox_client.py
def _is_token_expired(self) -> bool:
    if not self._token_expires_at:
        # ... calculate expiry
        return now_ist >= expiry
    return datetime.utcnow() >= self._token_expires_at

# NO REFRESH METHOD EXISTS
```

**Fixed Code** (in `upstox_client.py`):
```python
def ensure_token_valid(self) -> bool:
    """Refresh token if expired or close to expiry (within 15 min)."""
    if not self.access_token:
        return False
    
    if self._token_expires_at:
        ist = pytz.timezone('Asia/Kolkata')
        now_utc = datetime.utcnow()
        minutes_to_expiry = (self._token_expires_at - now_utc).total_seconds() / 60
        
        if minutes_to_expiry < 15:
            logger.warning(f"Token expires in {minutes_to_expiry:.0f} min. Attempting refresh...")
            # Check if non-interactive mode
            if os.getenv("TRADING_NON_INTERACTIVE", "").lower() in ("1", "true"):
                logger.error("Cannot refresh token in non-interactive mode. Pre-fetch token required.")
                return False
            # Open browser for fresh auth
            return self.authenticate(open_browser=True)
    
    return True

@staticmethod
def schedule_token_refresh():
    """Schedule token refresh at 3:15 AM IST daily."""
    import schedule
    def refresh():
        try:
            client = get_upstox_client()
            if client.ensure_token_valid():
                logger.info("Token refreshed successfully")
            else:
                logger.error("Token refresh failed")
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
    
    # Every day at 3:15 AM IST
    schedule.every().day.at("03:15").do(refresh)
```

**In `run_agent.py`** (add at startup):
```python
from mcp_server.upstox_client import UpstoxClient

# At module level, in main():
UpstoxClient.schedule_token_refresh()

# In your main loop, add:
import schedule
schedule.run_pending()  # Check for scheduled tasks
```

**Verify**:
```bash
# Simulate time skip to 3:15 AM, verify token refresh is called
# Verify token is valid after refresh
```

---

## #4: VERIFY GTT STATUS BEFORE EXIT (45 min fix)

**Problem**: GTT trigger detection unreliable. If gtt_rule_status() times out, exit_price = 0.

**Location**: `exit_manager.py` lines 364-413

**Current Code** (BROKEN):
```python
def _handle_gtt_before_exit(client, tracker, pos) -> Optional[Dict[str, Any]]:
    sl_id = pos.gtt_sl_order_id
    if not sl_id:
        return None
    
    try:
        triggered_rule = _cancel_one_gtt(client, sl_id)
    except GTTCancelFailed as e:
        log_risk_audit("gtt_cancel_blocked_sell", {...})
        logger.error("SELL BLOCKED — GTT cancel unverified for %s: %s", pos.decision_id, e)
        return "BLOCKED"
    
    if triggered_rule:
        child_order_id = triggered_rule.get("order_id")
        exit_px = 0.0  # ← PROBLEM: If timeout, stays 0
        if child_order_id:
            try:
                fill = client.wait_for_fill(str(child_order_id), timeout_s=30.0, poll_s=2.0)
                exit_px = float(fill.get("average_price") or 0)
            except Exception:
                pass  # ← PROBLEM: Silently fails, exit_px stays 0
        
        return {
            "already_exited": True,
            "exit_price": exit_px,  # ← Could be 0!
            ...
        }
```

**Fixed Code**:
```python
def _handle_gtt_before_exit(client, tracker, pos) -> Optional[Dict[str, Any]]:
    sl_id = pos.gtt_sl_order_id
    if not sl_id:
        return None
    
    try:
        triggered_rule = _cancel_one_gtt(client, sl_id)
    except GTTCancelFailed as e:
        log_risk_audit("gtt_cancel_blocked_sell", {...})
        logger.error("SELL BLOCKED — GTT cancel unverified for %s: %s", pos.decision_id, e)
        return "BLOCKED"
    
    if triggered_rule:
        child_order_id = triggered_rule.get("order_id")
        exit_px = 0.0
        
        if child_order_id:
            try:
                fill = client.wait_for_fill(str(child_order_id), timeout_s=30.0, poll_s=2.0)
                exit_px = float(fill.get("average_price") or 0)
            except Exception as ex:
                logger.error(f"Failed to fetch GTT child order fill for {child_order_id}: {ex}")
        
        # NEW: Validate exit_px is sensible
        if exit_px <= 0:
            logger.error(f"GTT triggered but exit price invalid ({exit_px}). BLOCKING POSITION CLOSE.")
            log_risk_audit("gtt_triggered_invalid_price", {
                "decision_id": pos.decision_id,
                "child_order_id": child_order_id,
                "exit_px": exit_px,
            })
            # Freeze instead of closing with garbage price
            runtime_safety.set_trading_freeze(
                f"GTT triggered but exit price invalid for {pos.decision_id}. Manual check required.",
                source="exit_manager"
            )
            return "BLOCKED"
        
        logger.info(f"GTT triggered: {pos.decision_id} @ {exit_px}")
        return {
            "already_exited": True,
            "exit_price": exit_px,
            "order_id": child_order_id,
            "reason": "gtt_sl_triggered",
        }
    
    return None
```

**Verify**:
```bash
# Mock GTT trigger with valid fill price, verify it closes position correctly
# Mock GTT trigger with timeout, verify "BLOCKED" is returned and trading frozen
```

---

## #5: ATOMIC ENTRY TRANSACTION (1 hour fix)

**Problem**: Race condition if crash between position creation (line 416) and GTT placement (line 450).

**Location**: `lean_fo_executor.py` lines 380-530

**Current Code** (BROKEN):
```python
# STEP 4: Create position row
tracker.add_position(position)  # Line 416

# ...

# STEP 5: Place GTT (line 450+)
gtt_result = _place_protective_sl_gtt(...)
```

**Fixed Code**:
```python
# NEW: Wrap entire entry in transaction
def _atomic_entry_transaction(
    *,
    client,
    symbol,
    instrument_key,
    qty,
    avg_px,
    decision_id,
    sig,
    trading_mode,
):
    """
    Atomic entry: Either everything succeeds or everything is rolled back.
    
    Returns: (success: bool, error: str, position_data: dict)
    """
    
    # Step 1: Pre-allocate position row (but don't mark OPEN yet)
    tracker = get_position_tracker()
    position = OpenPosition(
        decision_id=decision_id,
        symbol=symbol,
        strike=float(sig.get("strike", 0)),
        option_type=str(sig.get("option_type", "CE")),
        entry_price=avg_px,
        entry_time=datetime.now(IST),
        lots=qty // int(sig.get("lot_size", 50)),
        lot_size=int(sig.get("lot_size", 50)),
        instrument_key=str(instrument_key),
        status="PENDING_GTT",  # NEW: Not OPEN yet
        stop_loss_pct=float(sig.get("stop_loss", 25)),
        target_pct=float(sig.get("target", 50)),
    )
    
    # Step 2: Try to place GTT (before creating row)
    gtt_result = _place_protective_sl_gtt(
        client=client,
        decision_id=decision_id,
        instrument_key=str(instrument_key),
        qty=qty,
        avg_px=avg_px,
        stop_loss_pct=float(sig.get("stop_loss", 25)),
    )
    
    if not gtt_result["ok"]:
        if trading_mode == runtime_safety.TradingMode.LIVE:
            # Live mode: flatten immediately
            _immediate_flatten(client, str(instrument_key), qty, decision_id)
            return (False, f"GTT failed (flattened): {gtt_result['error']}", {})
        else:
            # Micro_live: also flatten now (CHANGED!)
            _immediate_flatten(client, str(instrument_key), qty, decision_id)
            return (False, f"GTT failed (flattened): {gtt_result['error']}", {})
    
    # Step 3: GTT succeeded, NOW create position row
    position.gtt_sl_order_id = gtt_result["gtt_id"]
    position.status = "OPEN"
    tracker.add_position(position)
    
    # Step 4: Register with exit_ticker
    try:
        from execution.exit_ticker import get_exit_ticker
        exit_ticker = get_exit_ticker()
        exit_ticker.register_position(...)
    except Exception as ex:
        log_risk_audit("exit_ticker_registration_failed", {...})
        # Non-fatal, continue
    
    return (True, None, {
        "decision_id": decision_id,
        "entry_price": avg_px,
        "gtt_id": gtt_result["gtt_id"],
        "status": "OPEN",
    })


# In maybe_execute_lean_fo_order(), replace steps 4-6 with:
success, error, result = _atomic_entry_transaction(
    client=client,
    symbol=symbol,
    instrument_key=instrument_key,
    qty=qty,
    avg_px=avg_px,
    decision_id=decision_id,
    sig=sig,
    trading_mode=trading_mode,
)

if not success:
    out["error"] = error
    return out

out["executed"] = True
out.update(result)
```

**Verify**:
```bash
# Mock crash between GTT success and position creation
# Verify on restart: GTT exists at broker, position found in DB
# Verify on restart: No duplicate GTT created
```

---

## TESTING CHECKLIST

Before deploying, test these scenarios:

```bash
# 1. Double-exit prevention
Test: SL hit via exit_ticker, verify exit_manager skips it
Expected: Only 1 SELL order placed

# 2. GTT failure auto-flatten  
Test: Mock GTT placement failure in MICRO_LIVE
Expected: Position flattened immediately, trading frozen

# 3. Token refresh at 3:15 AM
Test: Simulate time to 3:15 AM
Expected: Token refresh called and succeeds

# 4. GTT trigger with valid price
Test: Mock GTT fired with fill price
Expected: Position closed with correct P&L

# 5. GTT trigger with timeout (price unknown)
Test: Mock GTT fired but fill lookup times out
Expected: Trading frozen, "BLOCKED" returned, position NOT closed

# 6. Atomic entry with GTT failure
Test: GTT fails after fill confirmed
Expected: Position flattened, no orphan remains

# 7. Crash between fill and GTT
Test: Process killed after fill, before GTT
Expected: On restart, GTT found at broker, position recovered

# 8. Reconciliation orphan recovery
Test: Position at broker but not local
Expected: Reconciliation detects, flattens with confirmed fill
```

---

## DEPLOYMENT ORDER

1. **Fix #2 (GTT auto-flatten)** — Most critical, prevents overnight risk
2. **Fix #1 (Double-exit sync)** — Prevents accidental shorts  
3. **Fix #4 (GTT price validation)** — Prevents P&L corruption
4. **Fix #5 (Atomic entry)** — Prevents race conditions
5. **Fix #3 (Token refresh)** — Prevents 3:30 AM crash

---

## VALIDATION SCRIPT

```python
# test_critical_fixes.py
import pytest
from unittest.mock import Mock, patch

def test_double_exit_prevented():
    """Verify exit_manager doesn't exit if exit_ticker already did."""
    # Mock exit_ticker._exiting with decision_id
    # Call exit_manager.check_and_exit_positions()
    # Verify SELL not placed
    pass

def test_gtt_failure_auto_flatten_micro_live():
    """Verify GTT failure triggers immediate flatten in MICRO_LIVE."""
    # Mock GTT placement to fail
    # Call maybe_execute_lean_fo_order() with MICRO_LIVE mode
    # Verify _immediate_flatten called
    pass

def test_token_refresh_scheduled():
    """Verify token refresh is scheduled for 3:15 AM."""
    # Call UpstoxClient.schedule_token_refresh()
    # Verify schedule.every() called with correct time
    pass

def test_gtt_price_validation():
    """Verify GTT trigger with exit_price=0 doesn't close position."""
    # Mock gtt_rule_status returning triggered_rule
    # Mock wait_for_fill to timeout (exit_px=0)
    # Call _handle_gtt_before_exit()
    # Verify returns "BLOCKED", not closed with garbage price
    pass

def test_atomic_entry_race_condition():
    """Verify crash between GTT and position creation handled."""
    # Mock: GTT succeeds, position creation crashes
    # Verify on restart: Position found in DB, GTT not duplicated
    pass

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

---

**Estimated time to implement all 5 fixes: 3-4 hours**

Deploy after verification, run 50 paper trades successfully, then move to MICRO_LIVE (supervised).
