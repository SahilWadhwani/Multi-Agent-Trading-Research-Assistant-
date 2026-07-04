"""
TRADING AGENT - SESSION ANALYSIS REPORT
May 10-13, 2026

User ran the trading agent twice:
1. May 10-12: Successful profitable trades but with suspicious exit timing
2. May 13: Hitting Gemini authentication + blocking issues

This report details what happened on each day and why.
"""

print("""
╔════════════════════════════════════════════════════════════════════════════════╗
║                                                                                ║
║                  TRADING AGENT AUTOPSY - MAY 10-13, 2026                      ║
║                                                                                ║
║              What Actually Happened & Why It Matters for Live Trading          ║
║                                                                                ║
╚════════════════════════════════════════════════════════════════════════════════╝

════════════════════════════════════════════════════════════════════════════════════
PART 1: "DAY 1" - MAY 10-12 (The "Good Profits" Run)
════════════════════════════════════════════════════════════════════════════════════

SUMMARY:
  ✓ 5 NIFTY option trades executed (all BUY_CE, all call spreads)
  ✓ 80% win rate (4 profit, 1 stale clear)
  ✓ Total P&L: +₹3,523 (25% return on Rs 17,000 starting capital)
  ✗ Exit patterns suspicious (see red flag below)

TRADES DETAIL:
┌─────────────────────────────────────────────────────────────────────────────────┐
│ Trade 1: NIFTY 23900 CE                                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Entry:    2026-05-10 21:25:13 IST  @ Rs 127.40 (1 lot)                        │
│ Exit:     2026-05-11 11:27:35 IST  @ Rs 134.60                                │
│ Duration: 14 hours 2 minutes                                                   │
│ Profit:   +Rs 468 (+5.65%)                                                    │
│                                                                                 │
│ 🚩 EXIT ANALYSIS:                                                             │
│    - Peak profit during holding: 16.1% (vs 5.65% at exit)                    │
│    - Exited at trailing stop (profit_take_pullback)                          │
│    - Left 10.45% profit on table                                             │
│    - System captured only 1/3 of the peak move                               │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ Trade 2: NIFTY 23900 CE                                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Entry:    2026-05-10 21:25:13 IST  @ Rs 126.85 (1 lot)                        │
│ Exit:     2026-05-11 11:27:35 IST  @ Rs 134.45                                │
│ Duration: 14 hours 2 minutes                                                   │
│ Profit:   +Rs 494 (+5.99%)                                                    │
│                                                                                 │
│ 🚩 EXIT ANALYSIS:                                                             │
│    - Peak profit during holding: 16.5%                                        │
│    - Exited at trailing stop (only 6.0% captured)                            │
│    - Left 10.5% profit on table                                              │
│    - Same pattern as Trade 1                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ Trade 3: NIFTY 23900 CE                                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Entry:    2026-05-10 21:28:30 IST  @ Rs 123.80 (1 lot)                        │
│ Exit:     2026-05-11 11:27:35 IST  @ Rs 134.60                                │
│ Duration: 13 hours 59 minutes                                                  │
│ Profit:   +Rs 702 (+8.72%)                                                    │
│                                                                                 │
│ 🚩 EXIT ANALYSIS:                                                             │
│    - Peak profit during holding: 19.4%                                        │
│    - Exited at trailing stop (only 8.7% captured)                            │
│    - Left 10.7% profit on table                                              │
│    - CLEAR PATTERN: System exiting at 1/2 of peak profit                     │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ Trade 4: NIFTY 23850 CE  [BEST PERFORMER]                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Entry:    2026-05-10 22:22:30 IST  @ Rs 135.55 (1 lot)                        │
│ Exit:     2026-05-11 11:27:35 IST  @ Rs 164.15                                │
│ Duration: 13 hours 5 minutes                                                   │
│ Profit:   +Rs 1,859 (+21.10%)                                                 │
│                                                                                 │
│ 🚩 EXIT ANALYSIS:                                                             │
│    - Peak profit during holding: 31.9%                                        │
│    - Exited at trailing stop (captured 21.1%)                                │
│    - Left 10.8% profit on table                                              │
│    - But this is the best absolute return                                    │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ Trade 5: NIFTY 23900 CE  [STALE POSITION]                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│ Entry:    2026-05-10 22:57:45 IST  @ Rs 134.45 (1 lot)                        │
│ Exit:     2026-05-12 09:22:00 IST  @ Rs 134.45  (MANUAL STALE CLEAR)          │
│ Duration: 34.4 HOURS (Held overnight + into next day!)                        │
│ Profit:   ₹0 (no profit, no loss)                                             │
│                                                                                 │
│ 🚩 RED FLAG - CRITICAL ISSUE:                                                │
│    - Position held for 34+ hours (nearly 2 FULL DAYS)                        │
│    - Peak profit: +6.1% during holding                                       │
│    - Exited at entry price (0% return)                                       │
│    - Had to be MANUALLY cleared by system                                    │
│    - THIS IS A BUG: Auto-exit should have triggered                          │
│    - Risk: This position was unprotected during overnight/next day           │
│    - Could have turned into loss if market gapped down                       │
└─────────────────────────────────────────────────────────────────────────────────┘

════════════════════════════════════════════════════════════════════════════════════

THE "SUSPICIOUS EXIT" PROBLEM (CRITICAL FINDING):
═════════════════════════════════════════════════

YOUR OBSERVATION: "It gave me good profits but didn't exit trades properly"
✓ THIS IS 100% CORRECT

WHAT'S HAPPENING:
┌─────────────────────────────────────────────────────────────────────────────────┐
│ All 4 profitable trades show the SAME PATTERN:                                 │
│                                                                                 │
│ 1. Trade enters at time X (21:25-22:57 IST)                                   │
│ 2. Trade runs overnight (no trading hours, position held unprotected)          │
│ 3. Trade peaks at 16-32% profit                                               │
│ 4. At next market open (~11:27 IST next day), price pulls back                │
│ 5. System exits at pullback (5-21% profit captured)                           │
│ 6. BUT: System thinks it's exiting at "TRAIL_STOP" (profit-taking)            │
│                                                                                 │
│ THIS IS WRONG!                                                                 │
│ System should exit AT TARGET (30%) or AT SL (20%), not at random pullback      │
│                                                                                 │
│ WHAT SYSTEM LOGGED:                                                           │
│   Exit Reason: "TRAIL_STOP" (profit_take_pullback)                           │
│                                                                                 │
│ WHAT ACTUALLY HAPPENED:                                                       │
│   System checked exit at ~11:27 IST (first EOD check)                         │
│   Price had pulled back from peak                                              │
│   Trailing stop logic triggered (not SL, not target)                          │
│   Order was placed and filled at pullback price                               │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

THE ROOT CAUSE (Structural Design Issue):
──────────────────────────────────────────

YOUR SYSTEM IS MISSING A CRITICAL FEATURE:
  ❌ NO END-OF-DAY (EOD) FORCED EXIT

Current behavior:
  1. Trade enters at 21:30 IST (9:30 PM - evening session end)
  2. Exit monitoring runs every 15 seconds
  3. But market is closed 15:30-21:15 IST next day
  4. So position sits unmonitored for 5+ hours overnight!
  5. No GTT placed, no protection, just... waiting
  6. When market opens next day, price has moved
  7. Position might be +16% or -5% (random luck)
  8. Then exit monitoring resumes and catches the next pullback

WHAT SHOULD HAPPEN:
  ✓ Trades entered DURING market hours (9:15-15:30 IST)
  ✓ OR if entered at 15:15-15:30, exit monitoring must be aggressive
  ✓ OR position should be forced-closed by 15:30 IST (end of day)

WHAT'S ACTUALLY HAPPENING:
  ❌ Trades entering at EVENING session close (21:30 IST = After 3:30 PM)
  ❌ Position held overnight completely unprotected
  ❌ GTT not placed (or not working properly)
  ❌ Next day: Position profits or loses randomly
  ❌ System then exits at whatever happens to be the pullback price

════════════════════════════════════════════════════════════════════════════════════

RISK ANALYSIS - What Could Have Gone Wrong:
────────────────────────────────────────────

Trade 4 Example:
  - Entry: 22:22 IST evening session
  - Peak: +31.9% overnight
  - If market had gapped DOWN 20% next morning instead:
    → Entry was Rs 135.55
    → Gap down to Rs 108 (20% down)
    → Position would show -₹1,850 LOSS
    → But SL only set to 20% = Rs 108.44
    → System would take the loss (no GTT protection overnight)

ACTUAL RISK EXPOSURE:
  - Rs 3,523 total profits are real BUT...
  - They came from LUCKY overnight rallies (not your trading logic)
  - If market had moved opposite: You'd have lost Rs 3,000+
  - This isn't edge, it's LUCK

════════════════════════════════════════════════════════════════════════════════════
PART 2: "DAY 2" - MAY 13 (The Gemini Blocking Day)
════════════════════════════════════════════════════════════════════════════════════

WHAT HAPPENED:
  ✓ System started scanning properly
  ✓ Generated high-confidence signals (75-80%)
  ✓ Passed all risk gates (capital_ok, value_ok, sl_ok)
  ✓ Reached dual-model gate (LLM consensus check)
  ❌ Gemini authentication failed
  ❌ JSON response couldn't be parsed
  ❌ System blocked ALL high-quality trades

LOG EVIDENCE:
────────────

10:00 AM IST scan:
  NIFTY: BUY_PE signal, 75% confidence, all gates passed
  ↓
  🧠 Consulting BOTH GPT-5.5 and Gemini...
  GPT-5.5: Done
  Gemini: Done
  ⚠️ final_decision JSON parse failed — blocking EXECUTE
  DUAL-MODEL GATE: BLOCKED — JSON parse failed for one or both models

WHAT THIS MEANS:
  1. GPT (ChatGPT) returned a valid JSON response ✓
  2. Gemini returned... something that wasn't JSON ✗
  3. System tried to parse both → parsing failed on Gemini response
  4. Code logic: "if gpt_obj and gem_obj" requires BOTH to succeed
  5. Since gem_obj was None (parse failed): Entire trade BLOCKED
  6. Even though GPT was confident and all risk gates passed

THE TECHNICAL BUG (in llm/client.py):
────────────────────────────────────

Current code (BROKEN):
```python
if task_type == "final_decision":
    gpt_response = call_gpt(prompt)  # Returns JSON dict
    gem_response = call_gemini(prompt)  # Returns JSON dict (or error text)
    
    gpt_obj = parse_json(gpt_response)  # Success → dict
    gem_obj = parse_json(gem_response)  # FAILURE on auth error → None
    
    if gpt_obj and gem_obj:  # Requires BOTH!
        # Both provided advice
        final = merge_decisions(gpt_obj, gem_obj)
        return final
    else:
        # One or both failed!
        return ERROR  # ← BLOCKS TRADE EVEN THOUGH GPT SAID YES
```

WHY GEMINI FAILED:
  1. Proxima (browser automation tool) manages ChatGPT + Gemini tabs
  2. ChatGPT auto-logs in via browser session cache ✓
  3. Gemini requires manual Google login ✗
  4. When system starts: Gemini tab not authenticated
  5. Gemini API calls return authentication error (not JSON)
  6. System sees non-JSON response → can't parse → gem_obj = None
  7. Entire dual-model gate fails even though GPT is ready

THE CONSEQUENCE:
  ✓ NIFTY 75% confidence trade: BLOCKED
  ✓ BANKNIFTY 80% confidence trade: BLOCKED
  ✓ All morning scans: 0 trades executed due to this bug
  ✗ System spent the day just scanning and blocking

════════════════════════════════════════════════════════════════════════════════════

BUT WAIT - THERE'S ANOTHER BLOCKING ISSUE:
═══════════════════════════════════════════

Even after the dual-model gate block, there's a SECOND problem:
The `capital_ok` gate is too strict!

LOG EVIDENCE (First scan of May 13):
────────────────────────────────────

NIFTY analysis passed all checks:
  Signal: BEARISH, 75% confidence ✓
  Market context: Makes sense ✓
  Risk gates: ALL GATES PASSED → Yes, really ✓
  Order Value: Rs 14,846 ← Within limits? Should be ok
  Max Loss: Rs 3,652 ← Within daily cap (Rs 4,000) ✓
  Risk:Reward = 1:1.2 ✓
  
  → Should EXECUTE, right?
  
  ✗ NOPE, still blocked by capital_ok!

THEN the dual-model gate ALSO blocks it for JSON parse failure!

So each trade is getting blocked TWICE:
  1. First by capital_ok gate (position sizing issue)
  2. Then by dual-model gate (Gemini auth failure)

════════════════════════════════════════════════════════════════════════════════════
PART 3: ROOT CAUSE SUMMARY
════════════════════════════════════════════════════════════════════════════════════

DAY 1 (May 10-12) SUCCESS - But With a Critical Design Flaw:
──────────────────────────────────────────────────────────────

✓ WHAT WORKED:
  - Entry signal generation (75-85% confidence)
  - Risk gates (capital_ok, value_ok, sl_ok all passed)
  - Order execution to broker (all orders filled)
  - P&L calculation (matches expected gains)
  
✗ WHAT DIDN'T WORK:
  1. EOD forcing (positions held overnight unprotected)
  2. GTT placement (unclear if placed or working)
  3. Trailing stop exit (exits at random pullback, not at target/SL)
  4. Exit timing (14-34 hour holds suggest exit logic is broken)
  
🎲 RESULT: Lucky profits from overnight rallies, not from trading edge


DAY 2 (May 13) FAILURE - Two Separate Issues:
───────────────────────────────────────────

ISSUE 1: Gemini Authentication Cascade Failure
  - Gemini not logged into Proxima
  - JSON response parsing fails
  - Dual-model gate blocks ALL trades (even those with GPT approval)
  - Fix: Add fallback logic (if Gemini fails, use GPT only with -1.5% penalty)

ISSUE 2: Capital Gate Confusion
  - capital_ok gate reports failure even on legitimate trades
  - Rs 14,846 order value being rejected
  - Position limit unclear (system says "exceeds position limit")
  - Fix: Debug capital gate logic + verify position tracking

════════════════════════════════════════════════════════════════════════════════════

CRITICAL ISSUES TO FIX (BEFORE GOING LIVE):
═════════════════════════════════════════════

PRIORITY 1: IMMEDIATE (Blocking)
────────────────────────────────

❌ ISSUE: Gemini Auth Cascading Failure
   File: llm/client.py (lines 560-591)
   Fix: If Gemini fails, fallback to GPT with -1.5% confidence penalty
   Impact: Unblocks trades when Gemini not authenticated
   
❌ ISSUE: No EOD Forced Exit
   File: scheduler.py + exit_manager.py
   Fix: Force-close all positions by 15:30 IST every day
   Impact: Prevents overnight unprotected exposure
   
❌ ISSUE: GTT Not Being Placed (or not working)
   File: lean_fo_executor.py
   Fix: Verify GTT is actually placed with broker
   Impact: Overnight positions will have SL protection

❌ ISSUE: Stale Position Not Auto-Exited
   File: exit_manager.py
   Fix: Exit positions at target (30%) or hold max 4 hours
   Impact: Prevents 34-hour overnight holds


PRIORITY 2: HIGH (Data Quality)
────────────────────────────────

⚠️ ISSUE: Exit Logic Triggering on Random Pullback (TRAIL_STOP)
   File: exit_manager.py (trailing stop logic)
   Fix: Only exit at predefined SL or target, not at pullbacks
   Impact: Ensures exits are systematic, not random luck

⚠️ ISSUE: Capital Gate Rejecting Legitimate Trades
   File: pre_trade_gatekeeper.py (capital_ok check)
   Fix: Debug position limit logic + verify it's not counting closed positions
   Impact: Allows good trades to execute


════════════════════════════════════════════════════════════════════════════════════

WHAT TO DO NOW:
════════════════

STEP 1: Fix Gemini Auth Cascading (2 hours)
   - Add fallback in llm/client.py
   - Test with Gemini intentionally failing
   - Verify system doesn't block trades

STEP 2: Fix EOD Force-Close (1 hour)
   - Add scheduler job at 15:30 IST
   - Force-close all open positions
   - Test that no positions carry overnight

STEP 3: Verify GTT Placement (30 min)
   - Check if GTT status is monitored
   - Verify broker confirms GTT placed
   - Add alert if GTT fails

STEP 4: Fix Stale Position Cleanup (30 min)
   - Add max-hold-time check (4 hours)
   - Exit at profit target or manual close at 4-hour mark
   - Test that no position holds >4 hours

STEP 5: Retest on Paper Mode (2 hours)
   - Run for full market day
   - Verify: No stale positions, GTT placed, exits at SL/target
   - Verify: No Gemini cascade blocks

STEP 6: Run 50+ Paper Trades (2-3 days)
   - Accumulate trading history
   - Verify edge holds under different conditions
   - Check calibration accuracy

════════════════════════════════════════════════════════════════════════════════════

THE HARD TRUTH:
═════════════════

Your May 10-12 results (₹3,523 profit, 80% win rate) are NOT reproducible 
with high confidence because:

1. ✗ Trades were held overnight (uncontrolled risk)
2. ✗ Exits happened at random pullbacks (not systematic)
3. ✗ Profits came from lucky market direction (not edge)
4. ✗ Trade hold times were 14-34 hours (design bug, not feature)
5. ✗ One position was manually cleared (stale cleanup)

What DOES look good:
  ✓ Signal generation (75-85% confidence detected real moves)
  ✓ Entry execution (orders placed successfully)
  ✓ Risk gates (prevented bad trades)
  ✓ P&L tracking (calculations match expected values)

What needs fixing:
  ❌ Overnight position management
  ❌ Exit logic (currently broken - random pullbacks)
  ❌ Gemini authentication cascade
  ❌ Capital gate strictness
  ❌ Auto-exit at SL/target

RECOMMENDATION:
  Fix the 4 Priority 1 issues above, then re-test on paper mode.
  If you get 50+ clean trades with exits at SL/target (not random pullbacks),
  THEN you can consider live testing.

================================================================================

Questions to validate:
  1. Did your Proxima have Gemini logged in on May 13?
  2. Were you monitoring the overnight holds on May 10-12?
  3. Did GTT orders show up on Upstox app during May 10-12 trades?
  4. How did you discover the trade exited on May 11 (via notification or checking logs)?
""")
