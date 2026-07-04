"""
CRITICAL BUG: EXIT TIMING ANALYSIS
May 10-12 Trades Show Design Flaw in Exit Logic

This document explains why trades exited at "wrong" times
and what needs to be fixed before live trading.
"""

ANALYSIS = """
════════════════════════════════════════════════════════════════════════════════
                        EXIT TIMING ISSUE EXPLAINED
════════════════════════════════════════════════════════════════════════════════

THE OBSERVATION (from user):
"It gave me good profits but didn't exit trades properly"

THE DATA CONFIRMS THIS:
┌──────────────────────────────────────────────────────────────────────────────┐
│ Trade  │ Entry      │ Exit       │ Hold    │ Peak   │ Actual │ Exit Type   │
│        │ Premium    │ Premium    │ Time    │ Profit │ Profit │             │
├──────────────────────────────────────────────────────────────────────────────┤
│ T1     │ Rs 127.40  │ Rs 134.60  │ 14h 2m  │ 16.1%  │ 5.65%  │ TRAIL_STOP  │
│ T2     │ Rs 126.85  │ Rs 134.45  │ 14h 2m  │ 16.5%  │ 5.99%  │ TRAIL_STOP  │
│ T3     │ Rs 123.80  │ Rs 134.60  │ 13h 59m │ 19.4%  │ 8.72%  │ TRAIL_STOP  │
│ T4     │ Rs 135.55  │ Rs 164.15  │ 13h 5m  │ 31.9%  │ 21.10% │ TRAIL_STOP  │
│ T5     │ Rs 134.45  │ Rs 134.45  │ 34h 42m │ 6.1%   │ 0.00%  │ MANUAL_STALE│
└──────────────────────────────────────────────────────────────────────────────┘

PATTERN OBSERVED:
  ✗ All 4 trades exit with TRAIL_STOP (not at SL or target)
  ✗ All capture only ~50-65% of peak profit
  ✗ Hold times are 13-14 hours overnight (should be max 4 hours)
  ✗ One trade held 34+ hours and manually cleared

════════════════════════════════════════════════════════════════════════════════

WHAT SHOULD HAPPEN (CORRECT BEHAVIOR):
═══════════════════════════════════════

Entry at 21:30 IST (evening session close)
  ↓
System places SL order @ 20% loss
System places target order @ 30% profit
  ↓
1. If price hits target (30%) → IMMEDIATE EXIT at target
2. If price hits SL (20% loss) → IMMEDIATE EXIT at SL
3. If time = 15:30 IST next day → FORCED EXIT (EOD)
4. If hold > 4 hours → FORCED EXIT (time limit)

Expected outcomes:
  ✓ Exit at SL (20% loss) or target (30% profit)
  ✓ OR exit by 15:30 IST (end of day)
  ✓ OR exit after 4 hours max
  ✗ NEVER hold 14-34 hours
  ✗ NEVER exit at random pullback price

════════════════════════════════════════════════════════════════════════════════

WHAT ACTUALLY HAPPENS (BROKEN BEHAVIOR):
════════════════════════════════════════════

Entry at 21:30 IST:
  - BUY_CE at Rs 127.40
  - SL: 20% = Rs 101.92
  - Target: 30% = Rs 165.62
  ↓
Night (Market closed 15:30-21:15):
  - Position held unmonitored for ~6 hours
  - No exit monitoring during night
  - If GTT was placed → should protect
  - If GTT NOT placed → completely exposed
  ↓
Next morning (11:30 IST):
  - Market opens, price gaps
  - Exit monitoring resumes
  - Price is at Rs 145 (let's say, +13.84% profit)
  - Price then pulls back to Rs 134.60 (-5.36% from peak, but still +5.65% from entry)
  ↓
System exits at pullback:
  - Log shows: "Exit Reason: TRAIL_STOP"
  - System exited at Rs 134.60 (+5.65% profit)
  - But peak was Rs 145 (+13.84%)
  - System had opportunity to exit at target (Rs 165.62) but didn't
  ↓
RESULT:
  ✗ Captured only 5.65% of available 13.84%
  ✗ Left profit on table
  ✗ Exit reason is "TRAIL_STOP" (not SL or target)

════════════════════════════════════════════════════════════════════════════════

ROOT CAUSE ANALYSIS:
═════════════════════

CAUSE 1: No EOD Forced Exit
──────────────────────────

Code likely does NOT have:
  ```python
  if market_time > 15:30 IST:
      # Force-close all open positions
      flatten_all_positions()
  ```

Instead, positions are held indefinitely until:
  - Manual intervention
  - Exit monitoring randomly catches a pullback
  - Or position goes to 0 value (expires)

CAUSE 2: No Position Time Limit
──────────────────────────────────

Code likely does NOT have:
  ```python
  if position_hold_time > 4_hours:
      # Exit even if not at SL or target
      exit_position("HOLD_TIME_EXCEEDED")
  ```

Result: Position can sit for 14+ hours waiting for... what?
  - System logged "TRAIL_STOP" exit, not SL or target
  - This suggests exit monitoring found a pullback and exited there
  - NOT because SL or target was hit

CAUSE 3: Unclear Exit Logic
───────────────────────────

"TRAIL_STOP" exit reason suggests:
  - System is monitoring trailing stops (not just SL/target)
  - When price falls from peak → system exits
  - This creates random exit prices, not systematic

SHOULD BE:
  ```python
  if current_price >= target_price:
      exit_position("TARGET_HIT")
  elif current_price <= sl_price:
      exit_position("SL_HIT")
  elif eod_time:
      exit_position("EOD_FORCED")
  elif hold_time > max_hours:
      exit_position("HOLD_TIME_LIMIT")
  ```

NOT:
  ```python
  if current_price < recent_peak:  # ← WRONG!
      exit_position("TRAIL_STOP")
  ```

════════════════════════════════════════════════════════════════════════════════

THE STALE POSITION BUG (Trade 5):
═════════════════════════════════

Trade 5 was held 34.42 hours:
  - Entry: May 10 22:57 IST
  - Exit: May 12 09:22 IST
  - Exit reason: "MANUAL_STALE_CLEAR_TODAY"
  - Exit price: Identical to entry (₹0 profit)

This tells us:
  ✓ System recognized the position was "stale"
  ✓ System had logic to "clear" stale positions
  ✗ But this logic ran MANUALLY (system couldn't auto-fix)
  ✗ Position was held for 34+ hours unmonitored
  ✗ System waited until next morning to clear it

THIS IS A CRITICAL BUG FOR LIVE TRADING:
  - Overnight, position was completely unprotected
  - Could have turned into massive loss if market gapped 50% down
  - (Unlikely but possible in certain scenarios)

════════════════════════════════════════════════════════════════════════════════

FILES TO CHECK/FIX:
═══════════════════

1. execution/exit_manager.py
   ├─ Look for: _check_exit_conditions()
   ├─ Problem: Probably checking for pullbacks, not SL/target
   ├─ Fix: Make logic explicit - exit ONLY on:
   │   ✓ Target hit
   │   ✓ SL hit
   │   ✓ EOD time reached
   │   ✓ Hold time exceeded (4 hours max)
   ├─ Remove: TRAIL_STOP logic (causes random exits)
   
2. scheduler.py
   ├─ Look for: Exit monitoring loop
   ├─ Problem: Probably doesn't have EOD check
   ├─ Fix: Add check at 15:30 IST to force-close all positions
   │   ```python
   │   if current_time > 15:30 IST:
   │       close_all_positions()
   │   ```
   
3. execution/lean_fo_executor.py
   ├─ Look for: Position registration
   ├─ Problem: Probably doesn't track hold time
   ├─ Fix: Add created_time timestamp to every position
   │   ```python
   │   position.created_at = current_time
   │   position.max_hold = 4 hours
   │   ```

4. brain/position_tracker.py
   ├─ Look for: Check for stale positions
   ├─ Problem: Manual stale_clear suggests auto-cleanup not working
   ├─ Fix: Ensure stale position auto-exit works properly
   │   (Currently working only manually)

════════════════════════════════════════════════════════════════════════════════

VERIFICATION TEST:
═══════════════════

After fixes, run this test to verify exit logic works:

TEST 1: Exit at Target
  Entry: NIFTY 23900 CE @ Rs 100
  Target: Rs 130 (+30%)
  SL: Rs 80 (-20%)
  Expected: If price hits Rs 130 → Exit immediately at Rs 130
  Verify: Exit reason should be "TARGET_HIT"

TEST 2: Exit at SL
  Entry: NIFTY 23900 CE @ Rs 100
  Target: Rs 130 (+30%)
  SL: Rs 80 (-20%)
  Expected: If price hits Rs 80 → Exit immediately at Rs 80
  Verify: Exit reason should be "SL_HIT"

TEST 3: Exit at EOD
  Entry: 15:00 IST @ Rs 100
  Hold: 30 minutes
  Expected: At 15:30 IST → Exit position
  Verify: Exit reason should be "EOD_FORCED" (or "EOD_CLOSE")

TEST 4: Exit at Hold Time Limit
  Entry: 10:30 IST @ Rs 100
  Hold: 4 hours
  Expected: At 14:30 IST → Exit position (even if not at SL/target)
  Verify: Exit reason should be "HOLD_TIME_LIMIT"

TEST 5: Never Hold >4 Hours
  Run paper trading all day
  Verify: NO positions held >4 hours
  Verify: NO manually cleared stale positions

════════════════════════════════════════════════════════════════════════════════

IMPACT ON YOUR TRADING:
═══════════════════════

Current behavior (BROKEN):
  ✓ You make profit from lucky overnight rallies
  ✗ Exits are at random pullback prices (not systematic)
  ✗ You capture only ~50% of peak profit available
  ✗ One position held 34+ hours unprotected
  ✗ Can't reproduce results reliably

Fixed behavior (REQUIRED FOR LIVE):
  ✓ Exits happen at SL (protect against losses)
  ✓ Exits happen at target (lock in profits systematically)
  ✓ Exits happen by EOD (no overnight unprotected exposure)
  ✓ Position hold time capped at 4 hours (max risk window)
  ✓ Results become reproducible and systematic

════════════════════════════════════════════════════════════════════════════════

SUMMARY:
═════════

Your May 10-12 trading LOOKED good (₹3,523 profit, 80% win rate)
but was actually LUCKY because:

1. ✗ Positions held overnight without protection
2. ✗ Exits happened at random pullback prices
3. ✗ Captured only 50-65% of peak profit available
4. ✗ One position hit manual stale clear (34h hold)
5. ✗ If market had moved opposite direction → losses

What needs fixing (MUST DO):
1. Add EOD force-close at 15:30 IST
2. Add 4-hour hold time limit
3. Exit ONLY at SL, target, EOD, or time limit
4. Remove TRAIL_STOP exit logic
5. Ensure GTT is placed and working for overnight protection

After fixes:
✓ Restart paper trading (50+ trades)
✓ Verify exits happen at SL/target, not random
✓ Verify no stale positions
✓ Verify hold times < 4 hours
✓ Then safe to go to micro-live

════════════════════════════════════════════════════════════════════════════════
"""

print(ANALYSIS)
